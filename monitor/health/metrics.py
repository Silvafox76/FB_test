"""The weekly metrics job: what the pilot knows about itself, and what it does not.

BUILD_ORDER step 21 asks for precision, recall, the translation rejection rate and
the connector break rate, written weekly (or on demand via `make metrics`) to a
`metrics` table the review app's metrics page reads, with the export backlog and
D31's duplicate count on the same page because both are week 14 gate numbers.

**Half of those numbers have real data behind them today and half do not, and this
module's job is to be exact about which.** The golden set is exported but
unlabelled - 30 rows, no labels - and step 21 has Matthew and the project lead
label 150 notices by hand, "not by the agent". Precision and recall cannot be
computed from an unlabelled set, and a page that printed a precision figure derived
from nothing would be worse than one that prints "not measurable yet" and why: the
week 14 gate reads these numbers to decide whether the pilot scales, and a number
nobody can trace is a decision made on a guess. So a `Metric` is either measurable
and carries a value, or it is not measurable and carries no number at all and must
carry a reason. `migrations/011_metrics.sql` holds the same invariant as a check
constraint; this file's version gives the better message and the constraint is what
makes it true.

**Nothing is notified** (rule 18). A weekly run writes rows and prints a report. It
sends no mail, posts nothing, and calls nothing that would. The reviewer opens the
page when they open the page.

**No model call is made here.** Precision and recall are read from the last
recorded `make golden` run in `tests/golden/history.csv`, not measured by
re-scoring the set: a weekly job that silently spent 150 scoring calls would be a
cap decision (rule 22) taken by a report. A recorded run is only reported when its
`prompt_version` still matches the prompt the scorer runs today, because a
precision figure measured under a different prompt does not describe this one.

**Two connections, for the same reason `monitor status` uses two.** The reads run
on `monitor_readonly`, which is the role reporting exists for and the only one that
can see `approved_records` for the export backlog; the single insert runs as
`monitor_pipeline`, whose scheduled job this is. Connecting the job as
`monitor_review` to reach the backlog is rule 11's blocking case.

One number step 21's list does not get from this module: the count of rejections a
reviewer marked as a duplicate. The reason text lives in `config/review.yaml` and
matching on it here would put a config string in a `.py` file (rule 6), and it is
not the gate's number anyway - the gate counts duplicates that *reached an export
batch*, which is `crm_duplicate_count` below and is not measurable at all.
"""

from __future__ import annotations

import csv
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import psycopg
import structlog

from monitor.golden import GOLDEN_CSV, HISTORY_CSV, VALID_LABELS
from monitor.score.prompt import prompt_version
from review.export import backlog

log = structlog.get_logger(__name__)

# The reporting window for every rate below. Seven days because step 21 runs this
# weekly; it is the job's cadence, not a tuned number. Nothing is dropped, scored
# or staged by it, so it is not one of the thresholds rule 6 keeps in YAML - and
# `monitor metrics --days` moves it for a longer read (the week 14 gate wants 14).
WINDOW_DAYS = 7

RATE = "rate"
COUNT = "count"
DAYS = "days"


@dataclass(frozen=True)
class Definition:
    """What a metric is called and what it means, for the report and the page."""

    key: str
    label: str
    family: str
    description: str


# Display order within each family. The families themselves are ordered below.
DEFINITIONS: tuple[Definition, ...] = (
    Definition(
        "export_backlog",
        "Export backlog",
        "gate",
        "Approved records that have not left in a batch. Work a reviewer has already done that "
        "nobody can act on until someone produces a file.",
    ),
    Definition(
        "export_backlog_age_days",
        "Oldest unexported record",
        "gate",
        "How long the record at the front of the backlog has waited. This is ExportBacklogAgeDays, "
        "the metric the export-backlog-age alarm in infra/terraform/observability.tf is declared "
        "against; the threshold is the alarm's and is not repeated here.",
    ),
    Definition(
        "crm_duplicate_count",
        "Duplicates of an existing Opportunity",
        "gate",
        "Approved records that turned out to duplicate a live CRM Opportunity: the D31 gate number, "
        "and how often the Monitor surfaced something BD already had. It decides whether a read-only "
        "CRM search is the first integration built after week 14.",
    ),
    Definition(
        "precision",
        "Precision at the staging threshold",
        "quality",
        "Of what the scorer would stage, how much a person called relevant. From the last recorded "
        "make golden run at the prompt the scorer runs today.",
    ),
    Definition(
        "recall",
        "Recall",
        "quality",
        "Of what a person called relevant, how much the scorer would stage. Same source as precision.",
    ),
    Definition(
        "golden_set_labelled",
        "Golden set labelled",
        "quality",
        "Rows of tests/golden/golden.csv carrying a human label. Step 21 wants 150 rows labelled by "
        "Matthew and the project lead; nothing else in this table can move until they are.",
    ),
    Definition(
        "translation_rejection_rate",
        "Translation rejection rate",
        "health",
        "Translate outputs the JSON schema refused, over translate calls in the window. A refused "
        "output gets exactly one retry (rule 2), so a rejection here is not always a parked notice.",
    ),
    Definition(
        "connector_break_rate",
        "Connector break rate",
        "health",
        "Fetch runs that failed, over runs that finished in the window. A run still marked running "
        "is in neither count.",
    ),
    Definition(
        "sources_not_healthy",
        "Enabled sources not healthy",
        "health",
        "Sources in watch or unhealthy right now. It sits beside the break rate because a source "
        "that answers 200 with nothing is a break that never fails a run (rule 4).",
    ),
    Definition(
        "dedupe_join_rate",
        "Candidates clustering more than one notice",
        "health",
        "How often dedupe joined two notices into one candidate: the D4 depth measurement step 29 "
        "decides on. Not the same thing as a duplicate of a CRM record.",
    ),
)

BY_KEY = {definition.key: definition for definition in DEFINITIONS}

# Attention first: the numbers the week 14 gate reads, then the scorer, then the
# plumbing. Within a family an unmeasurable metric sorts first, because a missing
# number is what needs a person.
FAMILIES: tuple[tuple[str, str], ...] = (
    ("gate", "Week 14 gate numbers"),
    ("quality", "Scorer quality"),
    ("health", "Pipeline health"),
)


def _formatted(unit: str, value: float) -> str:
    """One number, in the shape its unit is read in. Shared by the value and its predecessor."""
    if unit == RATE:
        return f"{value:.1%}"
    if unit == DAYS:
        return f"{value:.1f} days"
    return f"{value:,.0f}"


@dataclass(frozen=True)
class Metric:
    """One reading. Either a number, or a reason there is none - never both, never neither.

    `numerator` and `denominator` are what the value was computed from, so a rate can
    be read as "6 of 832" rather than taken on trust. For a `count` the numerator is
    the count itself and the denominator is the population it came out of, when there
    is one. Both are empty on a `days` reading and on an unmeasurable one.

    `previous_value` and `previous_at` are filled by `latest()` from the newest
    earlier reading of the same metric, and are empty on a freshly collected Metric
    because nothing has been read back yet.
    """

    key: str
    unit: str
    measurable: bool
    note: str
    value: float | None = None
    numerator: int | None = None
    denominator: int | None = None
    previous_value: float | None = None
    previous_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.key not in BY_KEY:
            raise ValueError(f"{self.key!r} is not in DEFINITIONS; a metric with no definition cannot be rendered")
        if self.measurable and self.value is None:
            raise ValueError(f"{self.key}: a measurable metric carries a value")
        if not self.measurable and self.value is not None:
            raise ValueError(
                f"{self.key}: an unmeasurable metric carries no number. A figure derived from nothing is "
                "worse than saying plainly that there is none."
            )
        if not self.measurable and (self.numerator is not None or self.denominator is not None):
            raise ValueError(f"{self.key}: an unmeasurable metric carries no numerator or denominator either")
        if not self.measurable and not self.note.strip():
            raise ValueError(f"{self.key}: an unmeasurable metric has to say why")

    @property
    def definition(self) -> Definition:
        return BY_KEY[self.key]

    @property
    def label(self) -> str:
        return self.definition.label

    @property
    def family(self) -> str:
        return self.definition.family

    @property
    def description(self) -> str:
        return self.definition.description

    @property
    def display(self) -> str:
        """The number as it is read: a percentage, a count, or days."""
        return "not measurable" if not self.measurable else _formatted(self.unit, self.value or 0.0)

    @property
    def of(self) -> str:
        """What the value was counted out of, or an empty string when it stands alone."""
        if self.numerator is None or self.denominator is None:
            return ""
        return f"{self.numerator:,} of {self.denominator:,}"

    @property
    def moved(self) -> str:
        """How this reading compares with the last one, or an empty string when it is the first."""
        if not self.measurable or self.previous_value is None or self.previous_at is None:
            return ""
        direction = "unchanged from" if self.previous_value == self.value else "was"
        return f"{direction} {_formatted(self.unit, self.previous_value)} on {self.previous_at:%Y-%m-%d}"


def rate(key: str, numerator: int, denominator: int, *, note: str, empty: str) -> Metric:
    """A rate, or an unmeasurable metric when there was nothing to divide by.

    Nought out of nought is not nought percent. `empty` says what the absence means -
    "no translate call in the window" is a different fact from "every translation was
    accepted" and the page has to be able to tell them apart.
    """
    if denominator <= 0:
        return Metric(key=key, unit=RATE, measurable=False, note=empty)
    return Metric(
        key=key,
        unit=RATE,
        measurable=True,
        note=note,
        value=numerator / denominator,
        numerator=numerator,
        denominator=denominator,
    )


def count(key: str, counted: int, *, note: str, population: int | None = None) -> Metric:
    """A count. Always measurable: nought things counted is a reading, not an absence."""
    return Metric(
        key=key,
        unit=COUNT,
        measurable=True,
        note=note,
        value=float(counted),
        numerator=counted,
        denominator=population,
    )


def days(key: str, value: float, *, note: str) -> Metric:
    return Metric(key=key, unit=DAYS, measurable=True, note=note, value=value)


def unmeasurable(key: str, why: str) -> Metric:
    return Metric(key=key, unit=COUNT, measurable=False, note=why)


@dataclass(frozen=True)
class Window:
    """The half-open interval every rate below is measured over."""

    start: datetime
    end: datetime

    @property
    def days(self) -> int:
        return round((self.end - self.start).total_seconds() / 86400)


@dataclass(frozen=True)
class Run:
    """One metrics run: when it read, what window it read over, and what it found."""

    run_id: uuid.UUID
    at: datetime
    window: Window
    metrics: tuple[Metric, ...] = field(default_factory=tuple)

    @property
    def unmeasurable(self) -> tuple[Metric, ...]:
        return tuple(metric for metric in self.metrics if not metric.measurable)

    def families(self) -> list[tuple[str, list[Metric]]]:
        """The metrics grouped for display, unmeasurable first inside each family.

        A missing number is what needs a person, so it sits at the top of its section
        rather than in whatever order the definitions happen to run.
        """
        order = {definition.key: position for position, definition in enumerate(DEFINITIONS)}
        grouped = []
        for family, title in FAMILIES:
            members = [metric for metric in self.metrics if metric.family == family]
            members.sort(key=lambda metric: (metric.measurable, order[metric.key]))
            if members:
                grouped.append((title, members))
        return grouped


# --- what each metric is read from -------------------------------------------

# Translate calls and the translations that survived validation, at the prompt
# versions those calls used. The `in` clause is what keeps `source-native` out:
# 738 of the translations in this database were written by the TED mapper from the
# notice's own English (monitor/fetch.py's SOURCE_NATIVE_PROMPT_VERSION) and cost no
# call at all, so counting every translations row against the call count would put
# the rejection rate below zero.
TRANSLATE_CALLS = """
    select count(*) from model_calls
    where purpose = 'translate' and at >= %s and at < %s
"""

TRANSLATIONS_ACCEPTED = """
    select count(*) from translations
    where created_at >= %s and created_at < %s
      and prompt_version in (
          select distinct prompt_version from model_calls
          where purpose = 'translate' and at >= %s and at < %s
      )
"""

FETCH_RUNS = """
    select count(*) filter (where status in ('ok', 'failed')),
           count(*) filter (where status = 'failed')
    from fetch_runs
    where started_at >= %s and started_at < %s
"""

SOURCE_STATES = """
    select count(*) filter (where s.enabled),
           count(*) filter (where s.enabled and coalesce(h.state, 'unknown') <> 'healthy')
    from sources s
    left join source_health h on h.source_id = s.id
"""

# Candidates and how many of them hold more than one notice. Counted per candidate
# rather than by `candidate_notices.match_method`, because the first notice of every
# candidate is linked as `content_hash` with score 100 by the stager - the seed row
# and a genuine content-hash join are written identically, so the method column
# cannot tell a cluster from a singleton. The notice count can.
CANDIDATE_CLUSTERS = """
    select count(*), count(*) filter (where notices > 1)
    from (
        select c.id, count(cn.notice_id) as notices
        from candidates c
        left join candidate_notices cn on cn.candidate_id = c.id
        where c.created_at >= %s and c.created_at < %s
        group by c.id
    ) clusters
"""


def translation_rejection(conn: psycopg.Connection, window: Window) -> Metric:
    """Translate outputs the schema refused, over translate calls.

    Measured by difference, because nothing records a rejected output directly: every
    call is logged to `model_calls` including the ones that failed validation, and a
    `translations` row exists only for a call whose output validated. Calls minus rows
    is what the validator threw away.

    The one blind spot, said out loud: `monitor/translate/run.py` inserts with `on
    conflict (notice_id, prompt_version) do nothing`, so re-translating a notice at the
    same prompt version would spend a call, write no row, and read here as a rejection.
    Nothing in the pipeline re-translates today - the query that feeds it takes notices
    at `detected`/`needs translation` - so the difference is the validator's.
    """
    calls = conn.execute(TRANSLATE_CALLS, (window.start, window.end)).fetchone()[0]
    accepted = conn.execute(TRANSLATIONS_ACCEPTED, (window.start, window.end, window.start, window.end)).fetchone()[0]

    if accepted > calls:
        # Not clamped to nought. More accepted translations than calls means the count
        # is including rows that cost no call - the 738 source-native rows, if the
        # prompt_version clause is ever dropped - and a rejection rate computed from
        # that is wrong in a direction nobody would notice (rule 4).
        raise ValueError(
            f"{accepted} translations but only {calls} translate calls between {window.start:%Y-%m-%d} and "
            f"{window.end:%Y-%m-%d}: the accepted count is including translations no model produced"
        )

    rejected = calls - accepted
    return rate(
        "translation_rejection_rate",
        rejected,
        calls,
        note=f"{rejected} of {calls} translate outputs failed validation in the last {window.days} days",
        empty=f"no translate call in the last {window.days} days, so there is nothing to take a rate of",
    )


def connector_breaks(conn: psycopg.Connection, window: Window) -> Metric:
    """Fetch runs that failed, over runs that finished."""
    finished, failed = conn.execute(FETCH_RUNS, (window.start, window.end)).fetchone()
    return rate(
        "connector_break_rate",
        failed,
        finished,
        note=f"{failed} of {finished} fetch runs failed in the last {window.days} days",
        empty=f"no fetch run finished in the last {window.days} days; check the schedule before reading this as calm",
    )


def unhealthy_sources(conn: psycopg.Connection) -> Metric:
    """Enabled sources in watch or unhealthy right now, out of the enabled ones."""
    enabled, not_healthy = conn.execute(SOURCE_STATES).fetchone()
    return count(
        "sources_not_healthy",
        not_healthy,
        population=enabled,
        note="state as source_health holds it now, not over the window",
    )


def dedupe_joins(conn: psycopg.Connection, window: Window) -> Metric:
    """Candidates holding more than one notice, over candidates created in the window."""
    candidates, clustered = conn.execute(CANDIDATE_CLUSTERS, (window.start, window.end)).fetchone()
    return rate(
        "dedupe_join_rate",
        clustered,
        candidates,
        note=f"{clustered} of {candidates} candidates created in the last {window.days} days hold more than one notice",
        empty=f"no candidate was created in the last {window.days} days",
    )


def export_backlog(conn: psycopg.Connection) -> tuple[Metric, Metric]:
    """The backlog as a count and as the age of its oldest record.

    Read through `review.export.backlog`, which is the one reading of this (rule 1):
    the queue page, the export page, `monitor status` and this job all ask the same
    function, so they cannot disagree about how much work is waiting.

    `Backlog.age_days` is `ExportBacklogAgeDays`, which the export-backlog-age alarm in
    `infra/terraform/observability.tf` is declared against. This module computes and
    stores it; it does not publish it to CloudWatch, and nothing else does either yet.
    """
    waiting = backlog(conn)
    oldest = waiting.oldest
    waited = (
        f"oldest is {oldest.record_id}, approved by {oldest.approved_by} on {oldest.approved_at:%Y-%m-%d}"
        if oldest
        else "every approved record has been exported"
    )
    return (
        count("export_backlog", waiting.count, note=waited),
        days(
            "export_backlog_age_days",
            waiting.age_days,
            note="ExportBacklogAgeDays; the alarm holds the threshold, not this row",
        ),
    )


def crm_duplicates() -> Metric:
    """D31's duplicate count, which no query against these tables can produce.

    The pipeline has no CRM scope and makes no duplicate check: D31 superseded D30's
    read scope, and the reviewer checking Zoho by hand is the only duplicate control
    there is. The number the gate wants - approved records that turned out to duplicate
    a live Opportunity - is known only to the person who imports the batch and finds
    one, and it reaches this table when a person records it, not before.

    This is not a gap to be closed with an approximation. It is proven to matter: the
    Ghana GIFMIS tender the Monitor would surface already has a live Opportunity from
    December 2025, and this count is what decides whether a read-only CRM search is the
    first integration built after week 14.
    """
    return unmeasurable(
        "crm_duplicate_count",
        "Not measurable from the pipeline's tables. Under D31 the Monitor holds no CRM scope and runs no "
        "duplicate check; the reviewer checks Zoho by hand before approving, and a duplicate that reaches "
        "an export batch is a finding a person logs at the import. It arrives at the gate from those "
        "findings and from BD, joined on monitor_candidate_id.",
    )


def golden_labels(path: Path = GOLDEN_CSV) -> tuple[int, int]:
    """Rows of the golden set, and how many carry a label a person wrote.

    The file is read here rather than through `monitor.golden.read_labels`, which
    refuses on an unlabelled set by raising: that refusal is right for the harness and
    is exactly the state this metric exists to report. The label vocabulary still comes
    from `monitor.golden`, so there is one definition of what a label is.
    """
    if not path.exists():
        return 0, 0

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labelled = [row for row in rows if (row.get("label") or "").strip().lower() in VALID_LABELS]
    return len(labelled), len(rows)


def last_golden_run(path: Path = HISTORY_CSV) -> dict | None:
    """The last row of `tests/golden/history.csv`, or None if `make golden` has never run."""
    if not path.exists():
        return None

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-1] if rows else None


def scorer_quality(labelled: int, total: int, history: dict | None, current_prompt: str) -> list[Metric]:
    """Precision and recall, or the reason there are none.

    Three states, and the page has to be able to tell them apart:

      1. the set is not fully labelled - nothing can be measured, and the fix is two
         named people with an afternoon, not a code change;
      2. it is labelled but `make golden` has not run since, or ran under a different
         prompt - the number exists but does not describe the prompt in use, and
         reporting it anyway is how a prompt change gets credited with someone else's
         precision;
      3. it is labelled and the last run is at this prompt version - report it, with
         the date and version it came from.
    """
    if total == 0:
        why = (
            "tests/golden/golden.csv does not exist. Run 'make golden-export', then two people label it: "
            "step 21 wants 150 notices labelled by Matthew and the project lead, not by the agent."
        )
    elif labelled < total:
        why = (
            f"the golden set is unlabelled: {labelled} of {total} rows carry a label. Step 21 wants 150 "
            "notices labelled by Matthew and the project lead, not by the agent, and a set labelled by the "
            "model it measures would score well against its own opinion."
        )
    elif history is None:
        why = f"the {total}-row golden set is labelled but 'make golden' has never recorded a run. Run it."
    elif history["prompt_version"] != current_prompt:
        why = (
            f"the last golden run was at prompt_version {history['prompt_version']} on "
            f"{history['at'][:10]}; the scorer now runs {current_prompt}, so that number does not describe "
            "the prompt in use. Run 'make golden' again."
        )
    else:
        measured = (
            f"from the make golden run of {history['at'][:10]} at prompt_version {current_prompt}, "
            f"{history['labelled']} labelled notices at threshold {history['threshold']}"
        )
        return [
            Metric(key="precision", unit=RATE, measurable=True, note=measured, value=float(history["precision"])),
            Metric(key="recall", unit=RATE, measurable=True, note=measured, value=float(history["recall"])),
        ]

    return [unmeasurable("precision", why), unmeasurable("recall", why)]


def collect(conn: psycopg.Connection, *, window_days: int = WINDOW_DAYS) -> Run:
    """Read every metric. Selects only; writes nothing, calls no model, notifies nobody.

    The clock and the run id both come from the database in one read, so the window is
    in the same frame as the rows being counted and the run's identity is issued where
    every other id in this system is issued.
    """
    run_id, now = conn.execute("select gen_random_uuid(), now()").fetchone()
    window = Window(start=now - timedelta(days=window_days), end=now)

    labelled, total = golden_labels()
    backlog_count, backlog_age = export_backlog(conn)

    metrics = [
        backlog_count,
        backlog_age,
        crm_duplicates(),
        *scorer_quality(labelled, total, last_golden_run(), prompt_version()),
        rate(
            "golden_set_labelled",
            labelled,
            total,
            note=f"{labelled} of {total} rows labelled; step 21 wants 150",
            empty="tests/golden/golden.csv does not exist; run 'make golden-export'",
        ),
        translation_rejection(conn, window),
        connector_breaks(conn, window),
        unhealthy_sources(conn),
        dedupe_joins(conn, window),
    ]

    run = Run(run_id=run_id, at=now, window=window, metrics=tuple(metrics))
    log.info(
        "metrics_collected",
        run_id=str(run_id),
        window_days=window.days,
        measured=len(run.metrics) - len(run.unmeasurable),
        not_measurable=len(run.unmeasurable),
    )
    return run


# --- the row the page reads ---------------------------------------------------

INSERT_METRIC = """
    insert into metrics (run_id, at, window_from, window_to, metric, measurable,
                         value, numerator, denominator, unit, note)
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

LATEST_RUN = "select run_id, at, window_from, window_to from metrics order by at desc limit 1"

# Each metric of the newest run, and the newest measurable reading of the same metric
# before it. The comparison is what makes this a dashboard rather than a snapshot: a
# rejection rate of 3 percent means something different after a week at 0.7.
LATEST_METRICS = """
    select m.metric, m.unit, m.measurable, m.value, m.numerator, m.denominator, m.note,
           previous.value, previous.at
    from metrics m
    left join lateral (
        select earlier.value, earlier.at
        from metrics earlier
        where earlier.metric = m.metric and earlier.at < m.at and earlier.measurable
        order by earlier.at desc
        limit 1
    ) previous on true
    where m.run_id = %s
"""


def write(conn: psycopg.Connection, run: Run) -> None:
    """Store one run. One transaction: a half-written run would read as a metric that vanished."""
    with conn.transaction():
        for metric in run.metrics:
            conn.execute(
                INSERT_METRIC,
                (
                    run.run_id,
                    run.at,
                    run.window.start,
                    run.window.end,
                    metric.key,
                    metric.measurable,
                    metric.value,
                    metric.numerator,
                    metric.denominator,
                    metric.unit,
                    metric.note,
                ),
            )
    log.info("metrics_written", run_id=str(run.run_id), metrics=len(run.metrics))


def latest(conn: psycopg.Connection) -> Run | None:
    """The newest run as the page reads it, or None when the job has never run.

    A stored metric whose key is no longer in DEFINITIONS raises here, out of `Metric`
    itself, rather than being skipped: the two containers could be on different
    versions, and a page that quietly rendered nine of ten numbers would be the exact
    failure this module exists to prevent. The table is append only, so renaming a
    metric key is a decision with a consequence, and this is the consequence.
    """
    header = conn.execute(LATEST_RUN).fetchone()
    if header is None:
        return None

    run_id, at, window_from, window_to = header
    metrics = []
    for key, unit, measurable, value, numerator, denominator, note, previous, previous_at in conn.execute(
        LATEST_METRICS, (run_id,)
    ).fetchall():
        metrics.append(
            Metric(
                key=key,
                unit=unit,
                measurable=measurable,
                note=note,
                value=float(value) if value is not None else None,
                numerator=numerator,
                denominator=denominator,
                previous_value=float(previous) if previous is not None else None,
                previous_at=previous_at,
            )
        )

    return Run(run_id=run_id, at=at, window=Window(start=window_from, end=window_to), metrics=tuple(metrics))


def render(run: Run) -> str:
    """The report `make metrics` prints. Plain text; this is read in a terminal."""
    lines = [
        f"metrics run {run.at:%Y-%m-%d %H:%M} UTC, window {run.window.start:%Y-%m-%d} to "
        f"{run.window.end:%Y-%m-%d} ({run.window.days} days)",
        f"{len(run.metrics) - len(run.unmeasurable)} of {len(run.metrics)} numbers measured, "
        f"{len(run.unmeasurable)} not measurable and saying why",
    ]

    for title, members in run.families():
        lines.append("")
        lines.append(title)
        for metric in members:
            trailing = f"  ({metric.of})" if metric.of else ""
            lines.append(f"  {metric.label:<46} {metric.display}{trailing}")
            lines.append(textwrap.fill(metric.note, width=96, initial_indent=" " * 6, subsequent_indent=" " * 6))

    return "\n".join(lines)
