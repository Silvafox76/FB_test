"""The route decision, which is the whole of what `monitor/normalise/ocr.py` decides.

Two real PDFs in `tests/unit/fixtures/`, written by hand so no dependency is added to
produce them, and both read by the real `pdftotext`:

  - `bulletin_digital.pdf` is forty rows of real text in a real font, each row kept
    under 115 characters so it fits inside the 612pt page: poppler's `-layout` drops
    text positioned off the page, so a fixture whose lines ran off the edge would be
    testing the PDF writer rather than pdftotext. 4,078 characters of text layer as
    the module measures it, measured 2026-09-12.
  - `bulletin_scanned.pdf` is an image XObject and no font at all, which is what a
    scan of a printed bulletin is. Nought characters, measured the same day.

Those two numbers straddle the 2,000-character threshold in `config/thresholds.yaml`
by a wide margin, which is the point: on a real bulletin the two kinds are not close
together, and the threshold exists for the awkward third case — a scan the publisher
has already auto-OCR'd badly — rather than to separate these two.

The Textract side is exercised against a fake client that returns the documented
response shape. That is weaker than a recorded fixture and it is deliberately not
dressed up as more: the module has never made a real Textract call, so what these
tests hold is the *control flow* around it — one job started, polled until it leaves
IN_PROGRESS, paginated to the end, failures raised rather than swallowed — and not the
response shape itself. The first real call should record a fixture and replace the
fake here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from monitor.normalise.ocr import (
    TEXT_LAYER,
    TEXTRACT,
    Extraction,
    extract,
    text_layer,
    text_layer_minimum,
    text_lines,
    textract_text,
)

FIXTURES = Path(__file__).parent / "fixtures"
DIGITAL = FIXTURES / "bulletin_digital.pdf"
SCANNED = FIXTURES / "bulletin_scanned.pdf"


# --- the measurement ---------------------------------------------------------------


def test_the_threshold_comes_from_config_and_not_from_the_module():
    """Rule 6: a tuning number lives in YAML with a version hash, not in a .py file."""
    assert text_layer_minimum() == 2000


def test_a_born_digital_bulletin_has_a_text_layer():
    assert len(text_layer(DIGITAL).strip()) > text_layer_minimum()


def test_a_scanned_bulletin_has_none():
    """Not 'a small one'. An image-only page yields nothing at all to read."""
    assert text_layer(SCANNED).strip() == ""


def test_the_layout_flag_keeps_the_columns_apart():
    """A bulletin is a table; without -layout the columns interleave into one line."""
    extracted = text_layer(DIGITAL)
    assert "AVIS N2601" in extracted
    assert "2026-10-01" in extracted  # the deadline column survived as its own text


def test_an_ascii_apostrophe_comes_back_as_a_typographic_one():
    """Measured, and it matters beyond this module (step 19).

    The PDF's content stream carries U+0027. Helvetica's StandardEncoding maps that
    codepoint to `quoteright`, so poppler returns U+2019 and every French elision in a
    bulletin — `d\u2019un`, `l\u2019Etat`, `march\u00e9s d\u2019\u00e9tat` — arrives with a
    character that is not the one anybody types.

    The first reading of this was wrong and the correction is the useful part. The
    guess was that the lexicon is ASCII and the PDFs are typographic. Measured: the
    lexicon writes its two elided phrases with U+2019, and real French notices use
    *both* forms — 400 of 584 BOAMP notices carry an ASCII apostrophe and 119 carry a
    typographic one, with 83 notices across the corpus carrying both inside one
    document. So there is no "correct" side to write the lexicon on.

    The fix is therefore one canonical form folded on both sides before matching, in
    `monitor/filter/lexicon.py`, rather than a spelling choice in the lexicon. Measured
    delta on the 707 French notices held today: nought, because neither elided phrase
    currently matches anything in either form. It is a latent defect fixed before step
    19 adds the elided phrases that would have hit it, not a miss being repaired.
    """
    extracted = text_layer(DIGITAL)
    assert "\u2019" in extracted
    assert "d\u2019un systeme" in extracted
    assert "'" not in extracted


# --- the route ---------------------------------------------------------------------


def test_a_text_layer_is_read_directly_and_no_client_is_touched():
    """A born-digital issue never reaches Textract, so it costs nothing to read."""
    result = extract(DIGITAL)  # no clients passed at all

    assert isinstance(result, Extraction)
    assert result.route == TEXT_LAYER
    assert result.text_layer_chars == 4078
    assert "AVIS N2601" in result.text


def test_a_scan_without_a_client_says_what_is_missing_rather_than_returning_empty():
    """Rule 4. The alternative is a bulletin that reads as nought notices, silently."""
    with pytest.raises(RuntimeError) as raised:
        extract(SCANNED)

    message = str(raised.value)
    assert "scan" in message
    assert "Textract" in message
    assert "0 characters" in message  # it says what it measured, not just that it failed


def test_the_route_taken_is_recorded_on_the_result():
    """So that a page of output that reads oddly is a known kind of investigation."""
    assert extract(DIGITAL).route in {TEXT_LAYER, TEXTRACT}
    assert extract(DIGITAL).route == TEXT_LAYER


# --- Textract's control flow, against a fake -----------------------------------------


class FakeTextract:
    """Returns the documented response shape. Records every call it was asked to make."""

    def __init__(self, pages: list[dict]) -> None:
        self.pages = pages
        self.started: list[dict] = []
        self.gets: list[dict] = []

    def start_document_text_detection(self, **kwargs):
        self.started.append(kwargs)
        return {"JobId": "job-0001"}

    def get_document_text_detection(self, **kwargs):
        self.gets.append(kwargs)
        return self.pages[min(len(self.gets), len(self.pages)) - 1]


class FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {}


def page(*texts, status="SUCCEEDED", token=None):
    body = {
        "JobStatus": status,
        "Blocks": (
            [{"BlockType": "PAGE"}]
            + [{"BlockType": "LINE", "Text": text} for text in texts]
            + [{"BlockType": "WORD", "Text": "ignored"}]
        ),
    }
    if token:
        body["NextToken"] = token
    return body


@pytest.fixture
def bucket(monkeypatch):
    monkeypatch.setenv("MONITOR_OCR_BUCKET", "monitor-ocr-test")


def test_only_line_blocks_are_read():
    """WORD blocks would be reassembling into lines a second time and worse."""
    assert text_lines(page("AVIS 2601", "Date limite 2026-10-01")) == ["AVIS 2601", "Date limite 2026-10-01"]


def test_the_bulletin_is_uploaded_before_the_job_starts(bucket):
    """Textract's async API reads a multi-page PDF from S3 and from nowhere else."""
    textract, s3 = FakeTextract([page("AVIS 2601")]), FakeS3()

    textract_text(textract, s3, SCANNED)

    assert len(s3.puts) == 1
    assert s3.puts[0]["Bucket"] == "monitor-ocr-test"
    assert s3.puts[0]["Key"].endswith("bulletin_scanned.pdf")
    assert s3.puts[0]["Body"] == SCANNED.read_bytes()
    assert textract.started[0]["DocumentLocation"]["S3Object"]["Bucket"] == "monitor-ocr-test"


def test_a_job_is_started_once_and_polled_until_it_finishes(bucket, monkeypatch):
    """Polling one started job is not a retry (rule 2); nothing is asked for twice."""
    monkeypatch.setattr("monitor.normalise.ocr.POLL_SECONDS", 0)
    textract = FakeTextract([page(status="IN_PROGRESS"), page(status="IN_PROGRESS"), page("AVIS 2601")])

    assert textract_text(textract, FakeS3(), SCANNED) == "AVIS 2601"
    assert len(textract.started) == 1  # started once, whatever the polling did
    assert len(textract.gets) == 3


def test_every_page_of_one_result_is_read(bucket):
    """NextToken walks the rest of this job's result, which is not a second attempt."""
    textract = FakeTextract([page("AVIS 2601", token="more"), page("AVIS 2602")])

    assert textract_text(textract, FakeS3(), SCANNED) == "AVIS 2601\nAVIS 2602"
    assert textract.gets[1]["NextToken"] == "more"


def test_a_failed_job_raises_and_is_not_started_again(bucket):
    """Rule 2 and rule 4: it fails loudly and the bulletin is not resubmitted."""
    textract = FakeTextract([{"JobStatus": "FAILED", "StatusMessage": "UnsupportedDocumentException"}])

    with pytest.raises(RuntimeError, match="FAILED"):
        textract_text(textract, FakeS3(), SCANNED)

    assert len(textract.started) == 1


def test_a_missing_bucket_says_so_before_anything_is_uploaded(monkeypatch):
    monkeypatch.delenv("MONITOR_OCR_BUCKET", raising=False)
    s3 = FakeS3()

    with pytest.raises(RuntimeError, match="MONITOR_OCR_BUCKET"):
        textract_text(FakeTextract([page("AVIS 2601")]), s3, SCANNED)

    assert s3.puts == []
