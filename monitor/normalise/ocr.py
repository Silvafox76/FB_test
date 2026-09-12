"""Text out of a PDF bulletin. The text layer when there is one, Textract when there is not.

Burkina Faso publishes its notices as a weekly *Revue des marchés publics* PDF, and
some issues are born digital while others are a scan of the printed bulletin. The
same connector reads both, so something has to decide which kind of file arrived
(BUILD_ORDER step 18).

**It is a measurement, not a fallback (rule 1).** The character count of the text
layer is taken first, unconditionally, for every PDF. That number decides the route
once and the decision is recorded. Nothing is attempted, found wanting and retried a
different way: a born-digital issue never touches Textract and a scan never pretends
to have a text layer. If `pdftotext` itself fails — a corrupt file, a missing binary
— that raises, and the bulletin is not quietly sent to OCR instead. A failure that
opens a second path is exactly the shape rule 1 exists to forbid; a branch on a
property of the input, taken before anything is tried, is not.

**The threshold is in config** (`pdf_text_layer_min_chars`, rule 6). It is a tuning
number: a scan with a bad auto-OCR layer already applied by the publisher can yield a
few hundred characters of noise, and where the line sits between that and a thin but
real issue is something the weekly cadence at step 27 is expected to move.

**Polling is not a retry (rule 2).** A multi-page PDF only goes through Textract's
asynchronous API — `StartDocumentTextDetection`, then `GetDocumentTextDetection`
until the job leaves `IN_PROGRESS` — because the synchronous one takes single images
and a bulletin is forty pages. Reading the status of one job that was started once is
not re-attempting a failed call. Nothing here retries anything: a job that comes back
`FAILED` raises with the reason Textract gave, and the bulletin is not started again.

**Unexecuted, and this is the honest part.** The text-layer route below has been run:
`pdftotext` is a real binary and `tests/unit/test_ocr.py` exercises it against real
PDFs. The Textract route has never been executed. The credentials available while it
was written were invalid (`InvalidClientTokenId` from `sts:GetCallerIdentity` on
2026-09-12), and step 12's Terraform is roughed in and unverified, so neither the
bucket nor the IAM policy this needs exists yet. Its response parsing is built against
the documented shape rather than a recorded call, which is the one place in this
codebase that is true, and it is called out here rather than discovered later. The
first real run should be treated as a fixture-recording exercise: capture the
response, save it under `tests/contract/fixtures/`, and correct whatever this got
wrong.

What the deployment needs before that first run, none of which is in the Terraform yet:

  - an S3 bucket for the uploads, named by `MONITOR_OCR_BUCKET`;
  - `textract:StartDocumentTextDetection` and `textract:GetDocumentTextDetection` on
    the instance role, plus `s3:PutObject` and `s3:GetObject` scoped to that bucket;
  - a lifecycle rule expiring the uploads, and on a versioned bucket that means
    `noncurrent_version_expiration` as well as `expiration`, which is the mistake
    already made once in `infra/terraform/observability.tf` and fixed in 4f33ce3.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import structlog
import yaml

from monitor.registry.load import CONFIG_DIR

log = structlog.get_logger(__name__)

TEXT_LAYER = "text_layer"
TEXTRACT = "textract"

BUCKET_ENV = "MONITOR_OCR_BUCKET"

# How long `pdftotext` is given. It is a local binary reading a local file, so this is
# a guard against a pathological PDF rather than a network timeout.
PDFTOTEXT_TIMEOUT_SECONDS = 120

# How long a Textract job is given to finish, and how often its status is read. A
# forty-page bulletin runs in well under a minute in Textract's own documentation;
# ten minutes is the point at which something is wrong rather than slow.
JOB_TIMEOUT_SECONDS = 600
POLL_SECONDS = 5


@dataclass(frozen=True)
class Extraction:
    """The text of one bulletin, and which route produced it.

    `route` is on the record rather than inferred later because the two routes have
    different failure modes and different costs, and a page of Textract output that
    reads oddly is a different investigation from a page of text layer that does.
    """

    text: str
    route: str
    text_layer_chars: int


def text_layer_minimum() -> int:
    """The character count at or above which a PDF is read as born digital. From config.

    Read rather than hardcoded (rule 6). Validated here because a threshold that
    arrived as a string or a negative number would send every bulletin down one route
    and look like a property of the bulletins (rule 4).
    """
    value = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))["pdf_text_layer_min_chars"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"pdf_text_layer_min_chars must be a positive integer, got {value!r}")
    return value


def text_layer(pdf: Path) -> str:
    """Everything `pdftotext` can read out of the file. Raises if it cannot run.

    `-layout` because a bulletin is a table of notices and the column positions are
    what separate a reference number from a deadline; without it the columns
    interleave into one unreadable line per row. `-enc UTF-8` because the French
    bulletins carry accented characters and poppler's default encoding depends on how
    it was built.

    A missing binary or a corrupt PDF raises here and the bulletin is not routed to
    Textract instead: see the module docstring on why that would be a fallback.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, path from the connector
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"],
        capture_output=True,
        timeout=PDFTOTEXT_TIMEOUT_SECONDS,
        check=True,
    )
    return result.stdout.decode("utf-8", errors="strict")


def extract(pdf: Path, textract=None, s3=None) -> Extraction:
    """Read one bulletin. The one entry point; the connector calls nothing else here.

    The two clients are taken as arguments rather than built here, so the scanned route
    can be exercised against a recorded response with no AWS anywhere. Both are only
    touched when the measurement says the file is a scan: a born-digital issue needs
    neither, and passing None for one is correct.
    """
    layer = text_layer(pdf)
    chars = len(layer.strip())
    minimum = text_layer_minimum()

    if chars >= minimum:
        log.info("ocr_text_layer", pdf=pdf.name, chars=chars, minimum=minimum, route=TEXT_LAYER)
        return Extraction(text=layer, route=TEXT_LAYER, text_layer_chars=chars)

    log.info("ocr_scanned", pdf=pdf.name, chars=chars, minimum=minimum, route=TEXTRACT)
    if textract is None or s3 is None:
        raise RuntimeError(
            f"{pdf.name} has {chars} characters of text layer, below the {minimum} minimum, so it is a scan "
            "and needs Textract, which reads it from S3. A textract client and an s3 client are both required "
            "and were not both passed. See the module docstring for what the deployment still needs."
        )
    return Extraction(text=textract_text(textract, s3, pdf), route=TEXTRACT, text_layer_chars=chars)


def ocr_bucket() -> str:
    bucket = os.environ.get(BUCKET_ENV, "").strip()
    if not bucket:
        raise RuntimeError(
            f"{BUCKET_ENV} is not set. Textract reads a multi-page PDF from S3 and nowhere else, so a scanned "
            "bulletin cannot be read without a bucket. It is deployment state like the database URLs."
        )
    return bucket


def textract_text(client, s3, pdf: Path) -> str:
    """Upload the bulletin, start one job, read it until it finishes, return its lines.

    The upload is here and not in the connector because Textract's asynchronous API
    reads a multi-page PDF from S3 and from nowhere else: putting the file there is
    part of reading it, not a separate concern the caller should have to know about
    (rule 5 cuts between acquire and normalise, not through the middle of one read).

    Pagination is not a loop over attempts: `GetDocumentTextDetection` returns a page
    of blocks and a `NextToken`, and following it reads the rest of *this* job's one
    result. Nothing is asked for twice.

    Built against the documented response shape and never run. The first real call
    should be recorded as a fixture and this corrected against it.
    """
    key = f"bulletins/{pdf.name}"
    bucket = ocr_bucket()
    s3.put_object(Bucket=bucket, Key=key, Body=pdf.read_bytes(), ContentType="application/pdf")
    log.info("textract_uploaded", pdf=pdf.name, bucket=bucket, key=key, bytes=pdf.stat().st_size)

    started = client.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
    )
    job_id = started["JobId"]
    log.info("textract_started", pdf=pdf.name, job_id=job_id, bucket=bucket, key=key)

    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while True:
        result = client.get_document_text_detection(JobId=job_id)
        status = result["JobStatus"]
        if status != "IN_PROGRESS":
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Textract job {job_id} for {pdf.name} was still IN_PROGRESS after {JOB_TIMEOUT_SECONDS}s"
            )
        time.sleep(POLL_SECONDS)

    if status != "SUCCEEDED":
        raise RuntimeError(f"Textract job {job_id} for {pdf.name} ended {status}: {result.get('StatusMessage', '')}")

    lines = list(text_lines(result))
    token = result.get("NextToken")
    while token:
        page = client.get_document_text_detection(JobId=job_id, NextToken=token)
        lines.extend(text_lines(page))
        token = page.get("NextToken")

    log.info("textract_finished", pdf=pdf.name, job_id=job_id, lines=len(lines))
    return "\n".join(lines)


def text_lines(result: dict) -> list[str]:
    """The LINE blocks of one response page, in the order Textract returned them.

    LINE and not WORD: a bulletin's value is in its lines, and reassembling words into
    lines here would be doing Textract's own job a second time and worse.
    """
    return [block["Text"] for block in result.get("Blocks", []) if block.get("BlockType") == "LINE"]
