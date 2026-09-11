"""The golden set: what says whether a prompt change helped or hurt.

Thirty notices with a human label, re-scored on demand. It answers one question
that nothing else in the pipeline can: did the last change to the prompt, the
lexicon or the thresholds make the scorer better or worse?

Two rules about this file that are not style preferences.

**The labels are human.** BUILD_ORDER step 7 says to export the notices and stop,
and that the human labels them by hand. A golden set labelled by the same family of
model it is meant to measure measures nothing: it would score well against its own
opinion and a regression would look like agreement. The export writes an empty
label column and the harness refuses to run until a person has filled it in.

**The numbers are reported, never tuned toward.** `eval-harness` is read-only on
prompts and thresholds for the same reason. If precision comes back at 30 percent
that is the number; the decision about what to change is a person's, taken with the
number in front of them.

Precision and recall are measured at the staging threshold from
`config/thresholds.yaml`, because that is the threshold that decides what a
reviewer actually sees.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anthropic
import psycopg
import structlog
import yaml

from monitor import caps
from monitor.registry.load import CONFIG_DIR, REPO
from monitor.score.client import MODEL, SchemaError, score_notice
from monitor.score.prompt import prompt_version
from monitor.score.run import SELECT_FILTERED_IN, _notice_from

log = structlog.get_logger(__name__)

GOLDEN_DIR = REPO / "tests" / "golden"
GOLDEN_CSV = GOLDEN_DIR / "golden.csv"
HISTORY_CSV = GOLDEN_DIR / "history.csv"

# BUILD_ORDER step 7 names the first three. `url` is added because a person
# labelling thirty notices from a title alone is being asked to guess, and the
# whole value of the set is that the label is considered.
COLUMNS = ("notice_id", "title", "url", "label")
REQUIRED_COLUMNS = ("notice_id", "title", "label")

RELEVANT = "relevant"
NOT_RELEVANT = "not"
VALID_LABELS = (RELEVANT, NOT_RELEVANT)

SET_SIZE = 30

# The notices worth labelling are the ones the scorer will actually be given:
# everything the free filter passed, whether or not it has been scored yet.
SELECT_FOR_EXPORT = """
    select n.id, n.title, n.url, coalesce(t.title_en, '')
    from notices n
    left join translations t on t.notice_id = n.id
    where n.source_id = %s and n.status in ('filtered_in', 'scored')
    order by n.fetched_at
    limit %s
"""


class NotLabelled(Exception):
    """The golden set has no labels yet. A person fills them in; the agent does not."""


@dataclass(frozen=True)
class GoldenResult:
    labelled: int
    scored: int
    parked: int
    true_positive: int
    false_positive: int
    false_negative: int
    true_negative: int
    cost_usd: float
    prompt_version: str
    threshold: int

    @property
    def precision(self) -> float:
        """Of what the scorer would stage, how much a person called relevant."""
        staged = self.true_positive + self.false_positive
        return self.true_positive / staged if staged else 0.0

    @property
    def recall(self) -> float:
        """Of what a person called relevant, how much the scorer would stage."""
        relevant = self.true_positive + self.false_negative
        return self.true_positive / relevant if relevant else 0.0

    @property
    def schema_validity(self) -> float:
        attempted = self.scored + self.parked
        return self.scored / attempted if attempted else 0.0

    @property
    def mean_cost_usd(self) -> float:
        return self.cost_usd / self.scored if self.scored else 0.0


def stage_threshold() -> int:
    thresholds = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    return int(thresholds["stage_threshold"])


def export(conn: psycopg.Connection, source_id: str = "ted", size: int = SET_SIZE) -> int:
    """Write the unlabelled golden set and stop. The label column is left empty."""
    rows = conn.execute(SELECT_FOR_EXPORT, (source_id, size)).fetchall()
    if not rows:
        raise RuntimeError(
            f"no notices at filtered_in or scored for {source_id!r}; run 'make fetch' and 'make filter' first"
        )

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with GOLDEN_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for notice_id, title, url, title_en in rows:
            writer.writerow([str(notice_id), title_en or title, url, ""])

    log.info("golden_exported", path=str(GOLDEN_CSV.relative_to(REPO)), rows=len(rows))
    return len(rows)


def read_labels(path: Path = GOLDEN_CSV) -> dict[str, str]:
    """notice_id -> label. Raises if nobody has labelled it yet."""
    if not path.exists():
        raise NotLabelled(f"{path} does not exist; run 'monitor golden --export' first")

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    missing = [column for column in REQUIRED_COLUMNS if not rows or column not in rows[0]]
    if missing:
        raise NotLabelled(f"{path.name}: missing column(s) {missing}")

    labels = {row["notice_id"]: (row["label"] or "").strip().lower() for row in rows}
    unlabelled = [notice_id for notice_id, label in labels.items() if not label]
    if unlabelled:
        raise NotLabelled(
            f"{path.name}: {len(unlabelled)} of {len(labels)} rows have no label. "
            "A person labels them relevant or not; the pipeline does not label its own golden set."
        )

    wrong = {label for label in labels.values() if label not in VALID_LABELS}
    if wrong:
        raise NotLabelled(f"{path.name}: label(s) {sorted(wrong)} are not one of {VALID_LABELS}")

    return labels


def run(conn: psycopg.Connection, client: anthropic.Anthropic, path: Path = GOLDEN_CSV) -> GoldenResult:
    """Re-score the labelled set and report. Reports; never tunes."""
    labels = read_labels(path)
    threshold = stage_threshold()
    version = prompt_version()

    rows = conn.execute(
        SELECT_FILTERED_IN.replace("where n.status = 'filtered_in'", "where n.id = any(%s)"), (list(labels),)
    ).fetchall()
    found = {str(row[0]) for row in rows}
    if missing := set(labels) - found:
        raise RuntimeError(f"{len(missing)} golden notice(s) are not in the database: {sorted(missing)[:3]}")

    tp = fp = fn = tn = scored = parked = 0
    cost = 0.0

    for row in rows:
        notice_id, title_en, body_en = str(row[0]), row[18], row[19]
        notice = _notice_from(row)
        label = labels[notice_id]

        try:
            result = score_notice(conn, client, notice, title_en=title_en, body_en=body_en, prompt_version=version)
        except SchemaError:
            parked += 1
            log.warning("golden_schema_failure", notice_id=notice_id)
            continue
        except caps.CapExceeded:
            log.error("golden_cap_exceeded", scored=scored)
            raise

        scored += 1
        cost += result.cost_usd
        would_stage = result.score.relevance >= threshold
        is_relevant = label == RELEVANT
        if would_stage and is_relevant:
            tp += 1
        elif would_stage:
            fp += 1
        elif is_relevant:
            fn += 1
        else:
            tn += 1

    return GoldenResult(
        labelled=len(labels),
        scored=scored,
        parked=parked,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        true_negative=tn,
        cost_usd=cost,
        prompt_version=version,
        threshold=threshold,
    )


HISTORY_COLUMNS = (
    "at",
    "prompt_version",
    "model",
    "threshold",
    "labelled",
    "scored",
    "parked",
    "precision",
    "recall",
    "schema_validity",
    "mean_cost_usd",
)


def append_history(result: GoldenResult, path: Path = HISTORY_CSV) -> None:
    """One line per run, never edited. The trend is the point, not the last number."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(HISTORY_COLUMNS)
        writer.writerow(
            [
                datetime.now(UTC).isoformat(timespec="seconds"),
                result.prompt_version,
                MODEL,
                result.threshold,
                result.labelled,
                result.scored,
                result.parked,
                f"{result.precision:.4f}",
                f"{result.recall:.4f}",
                f"{result.schema_validity:.4f}",
                f"{result.mean_cost_usd:.6f}",
            ]
        )


def render(result: GoldenResult) -> str:
    return "\n".join(
        [
            f"golden set: {result.labelled} labelled, {result.scored} scored, {result.parked} parked",
            f"  precision at {result.threshold}   {result.precision:.1%}"
            f"   ({result.true_positive} staged and relevant, {result.false_positive} staged and not)",
            f"  recall               {result.recall:.1%}"
            f"   ({result.false_negative} relevant notices the scorer would not stage)",
            f"  schema validity      {result.schema_validity:.1%}",
            f"  mean cost per notice USD {result.mean_cost_usd:.5f}",
            f"  prompt_version       {result.prompt_version}",
        ]
    )
