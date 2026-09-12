"""The weekly metrics job.

The property these tests exist to hold: **a metric either has data behind it or says
plainly that it does not.** Step 21 asks for precision and recall, and the golden set
they would be measured from is exported but unlabelled - two named people label it,
not the agent. So the interesting cases here are the refusals: an unmeasurable metric
that tries to carry a number, a rate taken of nothing, and a precision figure from a
golden run made under a different prompt. Each is refused in Python for the message
and in Postgres for the guarantee, and both are tested.

The arithmetic runs against a stand-in connection that answers the module's queries
in order, so the rates are checked against numbers a person can verify by hand. Two
tests use the live database: one to prove the check constraint is deployed and not
just written, and one to prove the four queries still match the schema they read.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from jinja2 import Environment, FileSystemLoader

from monitor.health.metrics import (
    DEFINITIONS,
    RATE,
    Metric,
    Run,
    Window,
    connector_breaks,
    count,
    dedupe_joins,
    golden_labels,
    last_golden_run,
    latest,
    rate,
    render,
    scorer_quality,
    translation_rejection,
    unhealthy_sources,
    unmeasurable,
)
from monitor.registry.load import REPO

NOW = datetime(2026, 9, 12, 3, 0, tzinfo=UTC)
WINDOW = Window(start=NOW - timedelta(days=7), end=NOW)
PROMPT = "5cb6a4eb2de9"


class Answers:
    """A connection that answers the module's queries in the order they are asked.

    Small enough to read: the queries under test take no decisions on the connection
    beyond execute-then-fetchone, and a stand-in makes the arithmetic checkable
    against numbers written in the test rather than against whatever the database
    holds this morning.
    """

    def __init__(self, *rows: tuple) -> None:
        self.rows = list(rows)
        self.asked: list[str] = []

    def execute(self, sql: str, params: tuple = ()) -> Answers:
        self.asked.append(sql)
        return self

    def fetchone(self) -> tuple:
        return self.rows.pop(0)


def history_row(**overrides) -> dict:
    row = {
        "at": "2026-09-12T02:30:00+00:00",
        "prompt_version": PROMPT,
        "model": "claude-haiku-4-5",
        "threshold": "60",
        "labelled": "150",
        "scored": "150",
        "parked": "0",
        "precision": "0.6400",
        "recall": "0.7500",
        "schema_validity": "0.9933",
        "mean_cost_usd": "0.003",
    }
    row.update(overrides)
    return row


# --- a metric carries a number or a reason, never both and never neither -------


def test_an_unmeasurable_metric_cannot_carry_a_number():
    """The whole judgement of this step. A precision figure derived from nothing is worse than none."""
    with pytest.raises(ValueError, match="carries no number"):
        Metric(key="precision", unit=RATE, measurable=False, note="the golden set is unlabelled", value=0.0)


def test_an_unmeasurable_metric_cannot_carry_a_numerator_either():
    """0 of 30 rows labelled would read on a dashboard as a precision of nought."""
    with pytest.raises(ValueError, match="numerator"):
        Metric(key="recall", unit=RATE, measurable=False, note="unlabelled", numerator=0, denominator=30)


def test_an_unmeasurable_metric_has_to_say_why():
    with pytest.raises(ValueError, match="say why"):
        unmeasurable("crm_duplicate_count", "   ")


def test_a_measurable_metric_without_a_value_is_refused():
    with pytest.raises(ValueError, match="carries a value"):
        Metric(key="export_backlog", unit=RATE, measurable=True, note="")


def test_a_metric_with_no_definition_is_refused():
    """A number on a dashboard with nothing saying what it means is the failure this module is about."""
    with pytest.raises(ValueError, match="not in DEFINITIONS"):
        Metric(key="vibes", unit=RATE, measurable=True, note="", value=1.0)


# --- the arithmetic ------------------------------------------------------------


def test_the_translation_rejection_rate_is_calls_the_validator_refused():
    """832 translate calls, 826 stored translations: six outputs were refused."""
    metric = translation_rejection(Answers((832,), (826,)), WINDOW)

    assert (metric.numerator, metric.denominator) == (6, 832)
    assert metric.value == pytest.approx(6 / 832)
    assert metric.display == "0.7%"


def test_more_translations_than_calls_raises_rather_than_reporting_nought():
    """738 of this database's translations are TED's own English and cost no call.

    If the prompt_version clause that excludes them is ever dropped, accepted exceeds
    calls. Clamping that to nought would report a perfect translation stage; rule 4
    says it raises.
    """
    with pytest.raises(ValueError, match="no model produced"):
        translation_rejection(Answers((832,), (1564,)), WINDOW)


def test_the_break_rate_counts_only_runs_that_finished():
    """22 finished, 4 failed. A run still marked running is in neither count."""
    metric = connector_breaks(Answers((22, 4)), WINDOW)

    assert (metric.numerator, metric.denominator) == (4, 22)
    assert metric.display == "18.2%"


def test_nought_out_of_nought_is_not_nought_percent():
    """No fetch run finished is a different fact from no fetch run failed."""
    metric = connector_breaks(Answers((0, 0)), WINDOW)

    assert not metric.measurable
    assert metric.value is None
    assert "check the schedule" in metric.note


def test_a_count_of_nothing_is_still_a_reading():
    """Nought unhealthy sources is a measurement; nought of nothing is not."""
    metric = unhealthy_sources(Answers((8, 0)))

    assert metric.measurable
    assert (metric.value, metric.of) == (0.0, "0 of 8")


def test_dedupe_joins_count_candidates_holding_more_than_one_notice():
    """Not match_method: the stager links every candidate's first notice as content_hash."""
    metric = dedupe_joins(Answers((146, 3)), WINDOW)

    assert (metric.numerator, metric.denominator) == (3, 146)
    assert metric.display == "2.1%"


def test_a_rate_reports_what_it_was_taken_of():
    assert rate("connector_break_rate", 4, 22, note="", empty="").of == "4 of 22"


# --- precision and recall, and the three ways they are not available -----------


def test_an_unlabelled_golden_set_leaves_precision_and_recall_unmeasurable():
    precision, recall = scorer_quality(labelled=0, total=30, history=None, current_prompt=PROMPT)

    assert not precision.measurable and not recall.measurable
    assert "0 of 30" in precision.note
    assert "Matthew" in precision.note, "the note has to say who labels it, because no code change will"


def test_a_partly_labelled_set_is_not_good_enough():
    precision, _ = scorer_quality(labelled=149, total=150, history=history_row(), current_prompt=PROMPT)

    assert not precision.measurable
    assert "149 of 150" in precision.note


def test_a_labelled_set_with_no_recorded_golden_run_says_to_run_it():
    precision, _ = scorer_quality(labelled=150, total=150, history=None, current_prompt=PROMPT)

    assert not precision.measurable
    assert "never recorded a run" in precision.note


def test_a_golden_run_at_a_different_prompt_is_not_reported_as_todays_precision():
    """Otherwise a prompt change is credited with the precision of the prompt before it."""
    history = history_row(prompt_version="0000deadbeef")

    precision, recall = scorer_quality(labelled=150, total=150, history=history, current_prompt=PROMPT)

    assert not precision.measurable and not recall.measurable
    assert "0000deadbeef" in precision.note and PROMPT in precision.note


def test_a_golden_run_at_this_prompt_is_reported_with_its_date_and_version():
    precision, recall = scorer_quality(labelled=150, total=150, history=history_row(), current_prompt=PROMPT)

    assert (precision.value, recall.value) == (0.64, 0.75)
    assert precision.display == "64.0%"
    assert "2026-09-12" in precision.note and PROMPT in precision.note


def test_golden_labels_count_only_labels_from_the_vocabulary(tmp_path):
    """'maybe' is not a label. A row carrying one is not labelled."""
    path = tmp_path / "golden.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("notice_id", "title", "url", "label"))
        writer.writerows(
            [
                ("id-1", "A", "https://x.invalid", "relevant"),
                ("id-2", "B", "https://x.invalid", " NOT "),
                ("id-3", "C", "https://x.invalid", "maybe"),
                ("id-4", "D", "https://x.invalid", ""),
            ]
        )

    assert golden_labels(path) == (2, 4)


def test_a_missing_golden_set_is_nought_of_nought(tmp_path):
    assert golden_labels(tmp_path / "nothing.csv") == (0, 0)


def test_the_last_golden_run_is_the_last_line_of_the_history(tmp_path):
    """The history is append-only, so the current numbers are the last row and not the first."""
    path = tmp_path / "history.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(history_row()))
        writer.writeheader()
        writer.writerow(history_row(prompt_version="aaaaaaaaaaaa"))
        writer.writerow(history_row(prompt_version="bbbbbbbbbbbb"))

    assert last_golden_run(path)["prompt_version"] == "bbbbbbbbbbbb"
    assert last_golden_run(tmp_path / "nothing.csv") is None


# --- how the run is grouped and printed ----------------------------------------


def run_with(*metrics: Metric) -> Run:
    return Run(run_id="11111111-1111-1111-1111-111111111111", at=NOW, window=WINDOW, metrics=metrics)


def test_a_missing_number_sorts_to_the_top_of_its_section():
    """What needs a person comes first; it is not waiting behind the numbers that are fine."""
    run = run_with(
        count("export_backlog", 0, note="nothing waiting"),
        unmeasurable("crm_duplicate_count", "no CRM scope exists under D31"),
    )

    (title, members) = run.families()[0]
    assert title == "Week 14 gate numbers"
    assert [metric.key for metric in members] == ["crm_duplicate_count", "export_backlog"]


def test_the_report_prints_the_reason_beside_the_metric_with_no_number():
    run = run_with(unmeasurable("crm_duplicate_count", "no CRM scope exists under D31"))

    printed = render(run)

    assert "not measurable" in printed
    assert "no CRM scope exists under D31" in printed
    assert "1 of 1 numbers measured" not in printed
    assert "0 of 1 numbers measured, 1 not measurable" in printed


def test_every_definition_names_a_family_that_is_rendered():
    """A metric in a family the page does not group would be collected and never shown."""
    families = {"gate", "quality", "health"}

    assert {definition.family for definition in DEFINITIONS} <= families


def test_a_label_fits_the_column_the_report_prints_it_in():
    """The text report aligns on 46 characters; a longer label pushes its own number out of line."""
    too_long = [definition.label for definition in DEFINITIONS if len(definition.label) > 46]

    assert not too_long


# --- the page -----------------------------------------------------------------


def page(run: Run | None) -> str:
    """Render review/templates/metrics.html the way the app renders it.

    Through Jinja rather than through the app, because what is being checked is the
    template: that a metric with no number renders as words and a reason, and that
    neither state silently renders as an empty cell.
    """
    templates = Environment(loader=FileSystemLoader(REPO / "review" / "templates"), autoescape=True)
    return templates.get_template("metrics.html").render(run=run, request=None)


def test_the_page_shows_a_measured_number_and_what_it_was_taken_of():
    html = page(run_with(rate("connector_break_rate", 4, 22, note="4 of 22 fetch runs failed", empty="")))

    assert "18.2%" in html
    assert "4 of 22" in html
    assert "4 of 22 fetch runs failed" in html


def test_the_page_gives_a_metric_with_no_number_words_and_a_reason_instead():
    """Not a blank cell and not a nought: the reason is the reading."""
    html = page(run_with(unmeasurable("precision", "the golden set is unlabelled: 0 of 30 rows carry a label")))

    assert "not measurable" in html
    assert "0 of 30 rows carry a label" in html
    assert "score big" not in html, "an unmeasurable metric must not render in the form a number renders in"


def test_the_page_names_what_is_not_measurable_before_anything_else():
    html = page(
        run_with(
            count("export_backlog", 0, note="nothing waiting"),
            unmeasurable("crm_duplicate_count", "no CRM scope exists under D31"),
        )
    )

    assert "1 of 2 numbers are not measurable yet" in html
    assert html.index("Duplicates of an existing Opportunity") < html.index("Export backlog")


def test_the_page_before_the_job_has_ever_run_says_how_to_run_it():
    html = page(None)

    assert "make metrics" in html
    assert "No metrics run has been recorded" in html


# --- the same invariant, in the database ---------------------------------------


INSERT = """
    insert into metrics (run_id, at, window_from, window_to, metric, measurable,
                         value, numerator, denominator, unit, note)
    values (gen_random_uuid(), now(), now() - interval '7 days', now(), %s, %s, %s, %s, %s, %s, %s)
"""


def test_the_database_refuses_an_unmeasurable_metric_that_carries_a_number(db_conn):
    """Rule 11's habit: the loader gives the message, the constraint is what makes it true."""
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(INSERT, ("precision", False, 0.0, None, None, "rate", "the golden set is unlabelled"))
    db_conn.rollback()


def test_the_database_refuses_an_unmeasurable_metric_with_no_reason(db_conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        db_conn.execute(INSERT, ("precision", False, None, None, None, "rate", ""))
    db_conn.rollback()


def test_the_four_windowed_queries_still_match_the_schema(db_conn):
    """Runs the real SQL against the real database. Asserts shape, not this morning's numbers.

    The stand-in above checks the arithmetic; this checks that the columns still exist
    and that the accepted-translation count is still bounded by the call count, which is
    what the source-native exclusion buys.
    """
    window = Window(
        start=db_conn.execute("select now() - interval '30 days'").fetchone()[0],
        end=db_conn.execute("select now()").fetchone()[0],
    )

    for metric in (
        translation_rejection(db_conn, window),
        connector_breaks(db_conn, window),
        dedupe_joins(db_conn, window),
        unhealthy_sources(db_conn),
    ):
        if metric.measurable and metric.denominator is not None:
            assert 0 <= metric.numerator <= metric.denominator, f"{metric.key} is outside its own denominator"


def test_a_stored_metric_with_no_definition_stops_the_page_rather_than_being_dropped(db_conn):
    """The pipeline container and the review container can be on different versions.

    A page that quietly rendered nine of ten numbers would be the failure this module
    is about, so an unknown key is loud.
    """
    db_conn.execute(INSERT, ("vibes", True, 1.0, 1, 1, "rate", "written by a version this one does not know"))

    with pytest.raises(ValueError, match="not in DEFINITIONS"):
        latest(db_conn)
    db_conn.rollback()


def test_the_page_reads_the_newest_run_and_what_the_metric_read_before_it(db_conn):
    """The comparison is what makes the page a dashboard: 3 percent means something after a week at 0.7.

    The two runs are dated an hour apart around `now()` so the newest is unambiguous
    while another lane is writing to the same database. Both are rolled back.
    """
    dated = """
        insert into metrics (run_id, at, window_from, window_to, metric, measurable,
                             value, numerator, denominator, unit, note)
        values (%s, now() + %s::interval, now() - interval '7 days', now(), %s, true, %s, %s, %s, 'rate', %s)
    """
    older, newer = "22222222-2222-2222-2222-222222222222", "33333333-3333-3333-3333-333333333333"
    db_conn.execute(dated, (older, "30 minutes", "connector_break_rate", 0.05, 1, 20, "last week"))
    db_conn.execute(dated, (newer, "60 minutes", "connector_break_rate", 0.20, 4, 20, "this week"))

    run = latest(db_conn)
    db_conn.rollback()

    assert str(run.run_id) == newer
    (metric,) = run.metrics
    assert metric.display == "20.0%"
    assert metric.moved.startswith("was 5.0% on")
