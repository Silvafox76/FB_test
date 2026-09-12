"""The golden set: what says whether a prompt change helped or hurt.

A hundred and fifty notices with a human label, re-scored on demand. It answers one
question that nothing else in the pipeline can: did the last change to the prompt,
the lexicon or the thresholds make the scorer better or worse?

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

# BUILD_ORDER step 7 names notice_id, title and label. The other three are added.
#
# `url` because a person labelling a hundred and fifty notices from a title alone is
# being asked to guess, and the whole value of the set is that the label is considered.
#
# `source_id` and `language` because the set's coverage is a property worth checking
# and, without them, checking it means querying the database the file was drawn from.
# That matters beyond convenience: on 2026-09-12 a leftover row from a `tests/roles`
# fixture - source `test-fb5a50ae`, committed by a fixture whose setup had aborted
# partway - was drawn into the set, and was *guaranteed* to be drawn, because the
# stratifier gives every source at least one seat. Nothing in the file said so. With
# these two columns the contamination is visible in the artefact and a test can refuse
# it. They also give the labeller context they would otherwise have to go and find.
COLUMNS = ("notice_id", "source_id", "language", "title", "url", "label")
REQUIRED_COLUMNS = ("notice_id", "title", "label")

RELEVANT = "relevant"
NOT_RELEVANT = "not"
VALID_LABELS = (RELEVANT, NOT_RELEVANT)


# One row per notice. `translations` is keyed on (notice_id, prompt_version), so a
# TED notice carries both its own rendering and the machine translation, and a plain
# join returns it twice - the same defect found in the scorer and the stager on
# 2026-09-12, and this was its third instance. The old 30-row set escaped it only
# because it took the oldest rows, which predate the translator. At 150 it would not.
#
# Stratified, not "the oldest N". `order by n.fetched_at limit N` took one correlated
# slice - the first notices fetched, from one source, before the translator had run.
# A set drawn that way cannot say anything about the sources, languages or score
# bands it happens to exclude. These bucket by source and take a deterministic sample
# within each, so every source is represented in proportion and the selection is
# reproducible without storing a seed.
SELECT_PASSED = """
    with rendering as (
        select distinct on (t.notice_id) t.notice_id, t.title_en
        from translations t
        order by t.notice_id, t.created_at desc, t.prompt_version
    ),
    ranked as (
        select n.id, n.source_id, n.language, n.title, n.url,
               coalesce(r.title_en, '') as title_en,
               row_number() over (partition by n.source_id order by md5(n.id::text)) as seat,
               count(*) over (partition by n.source_id) as in_source,
               count(*) over () as pool
        from notices n
        left join rendering r on r.notice_id = n.id
        where n.status in ('filtered_in', 'scored')
    )
    select id, source_id, language, title, url, title_en
    from ranked
    where seat <= greatest(1, ceil(%s::numeric * in_source / pool))
    order by md5(id::text)
    limit %s
"""

SELECT_DROPPED = """
    with rendering as (
        select distinct on (t.notice_id) t.notice_id, t.title_en
        from translations t
        order by t.notice_id, t.created_at desc, t.prompt_version
    ),
    ranked as (
        select n.id, n.source_id, n.language, n.title, n.url,
               coalesce(r.title_en, '') as title_en,
               row_number() over (partition by n.source_id order by md5(n.id::text)) as seat,
               count(*) over (partition by n.source_id) as in_source,
               count(*) over () as pool
        from notices n
        left join rendering r on r.notice_id = n.id
        where n.status = 'filtered_out'
    )
    select id, source_id, language, title, url, title_en
    from ranked
    where seat <= greatest(1, ceil(%s::numeric * in_source / pool))
    order by md5(id::text)
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


def _thresholds() -> dict:
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


def stage_threshold() -> int:
    return int(_thresholds()["stage_threshold"])


def set_size() -> int:
    """How many notices a person is asked to label. 150 at step 21, 30 before it."""
    return int(_thresholds()["golden_set_size"])


def passed_share() -> int:
    """How many of the set come from notices the free filter let through.

    The rest are drawn from `filtered_out`, and that is the point of the split rather
    than a detail of it: a set drawn only from survivors measures the scorer and can
    say nothing about the filter, because a relevant notice the filter wrongly dropped
    is not eligible to appear in it. See config/thresholds.yaml for the measurement
    that made this necessary.
    """
    return int(_thresholds()["golden_passed_share"])


def dropped_share() -> int:
    return set_size() - passed_share()


def _refuse_to_discard_labels() -> None:
    """Never overwrite a set somebody has started labelling.

    `export` opens the file with "w". Before this check, running `monitor golden
    --export` on a labelled set destroyed it without a word - and that is the one
    irreplaceable thing in the repository. Every other artefact here can be rebuilt
    from the database or the registry; the labels are hours of a named person's
    judgement and exist nowhere else. At 30 rows that was an afternoon's annoyance.
    At 150 it is the measurement the week 14 gate depends on.

    It refuses on the first label rather than on a majority, because a half-labelled
    set is exactly the state a person is in when they are most likely to re-run the
    export to "refresh" it. Moving the file is the deliberate act; there is no --force
    (rule 1), because a flag that destroys the labels is the same defect with a
    confirmation step in front of it.
    """
    if not GOLDEN_CSV.exists():
        return

    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        labelled = [row for row in csv.DictReader(handle) if (row.get("label") or "").strip()]

    if labelled:
        raise RuntimeError(
            f"{GOLDEN_CSV} already has {len(labelled)} label(s) and exporting "
            "would overwrite them. Those labels are a person's work and are not reproducible. "
            "Move the file somewhere safe first if you really do want a fresh draw."
        )


def export(conn: psycopg.Connection, size: int | None = None) -> int:
    """Write the unlabelled golden set and stop. The label column is left empty.

    No `source_id` argument any more, and that is the substantive change. It defaulted
    to "ted", so the set measured the scorer on European above-threshold notices and
    said nothing about the donor feeds, the French and Ukrainian corpus, or any West
    African source. A golden set that covers one source measures one source.

    Two draws, straddling the free filter, proportional to each source's share of its
    side. See `golden_passed_share` in config/thresholds.yaml for why the dropped side
    is in here at all.
    """
    _refuse_to_discard_labels()

    size = set_size() if size is None else size
    passed_target = round(size * passed_share() / set_size())
    dropped_target = size - passed_target

    passed = conn.execute(SELECT_PASSED, (passed_target, passed_target)).fetchall()
    dropped = conn.execute(SELECT_DROPPED, (dropped_target, dropped_target)).fetchall()
    rows = list(passed) + list(dropped)

    if not passed:
        raise RuntimeError("no notices at filtered_in or scored; run 'make fetch' and 'make filter' first")
    if len(rows) < size:
        # Loud rather than silently short (rule 4). A set smaller than asked for still
        # produces a precision number, and nothing downstream would say it was thin.
        raise RuntimeError(
            f"asked for {size} notices and the corpus yielded {len(rows)} "
            f"({len(passed)} passed the filter, {len(dropped)} dropped). "
            "Fetch more before exporting, or pass a smaller size deliberately."
        )

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with GOLDEN_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for notice_id, source_id, language, title, url, title_en in rows:
            writer.writerow([str(notice_id), source_id, language, title_en or title, url, ""])

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
