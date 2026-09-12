"""The sixth page: the numbers the week 14 gate reads, and the ones with no data yet.

`tests/unit/test_metrics.py` covers the arithmetic - what each number counts, what
window it counts over, and when it declines to produce a figure. This file covers
the part a reviewer sees, and it exists because the page's whole job is one that a
green test suite could otherwise miss: HALF THE NUMBERS ON IT HAVE NO DATA BEHIND
THEM, and the page has to say so in words rather than render a nought.

A nought is the specific failure worth testing against. Precision with no labelled
golden set, and a duplicate count the Monitor architecturally cannot see, would both
render as `0` under any templating that treats a missing value as falsy - and `0`
duplicates and `0%` precision are both readings a person would act on. The page
must never produce either from an absence.

The other property here is rule 5. The page computes nothing: `make metrics` writes
a row and this reads the newest one. A page that measured on request would put the
pipeline's arithmetic in the review app and give two answers to the same question
depending on which was looked at last.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from monitor.health.metrics import Metric, Run, Window, latest, write
from review.app import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def stored(owner):
    """Write one metrics run as the pipeline does, and remove it afterwards.

    Written on the owner connection rather than as `monitor_pipeline` only because
    the teardown needs delete, which no runtime role holds on any table - `metrics`
    is append only by grant like the audit log. The INSERT itself is the pipeline's
    own `write()`, so what is exercised is the real write path and not a fixture's
    idea of one.
    """
    written = []

    def make(*metrics: Metric, at: datetime | None = None) -> Run:
        end = at or datetime.now(UTC)
        run = Run(
            run_id=uuid.uuid4(),
            at=end,
            window=Window(start=end - timedelta(days=7), end=end),
            metrics=metrics,
        )
        write(owner, run)
        written.append(run.run_id)
        return run

    yield make

    for run_id in written:
        owner.execute("delete from metrics where run_id = %s", (run_id,))


def measured(key: str, value: float, *, unit: str = "rate", **kwargs) -> Metric:
    return Metric(key=key, measurable=True, value=value, unit=unit, note="measured", **kwargs)


def absent(key: str, why: str) -> Metric:
    return Metric(key=key, measurable=False, value=None, unit="rate", note=why)


# --- the page renders what the job wrote, and nothing else --------------------


def test_the_page_renders_the_newest_run(client, stored):
    stored(measured("translation_rejection_rate", 0.007, numerator=6, denominator=832))

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "0.7%" in response.text


def test_a_newer_run_replaces_an_older_one_rather_than_adding_to_it(client, stored):
    """`metrics` is append only, so every run is still there; the page shows one.

    Both runs are stamped AHEAD of now rather than behind it, and that is what makes
    the test independent of whatever the table already holds. The page shows the
    newest run and, beside each figure, the newest measurable reading of the same
    metric before it - so a pair written into the past would find a real run in
    between and compare against that instead. Writing them into the future puts them
    adjacent to each other with nothing able to land between.
    """
    soon = datetime.now(UTC) + timedelta(hours=1)
    stored(measured("connector_break_rate", 0.50, numerator=2, denominator=4), at=soon)
    stored(measured("connector_break_rate", 0.182, numerator=4, denominator=22), at=soon + timedelta(hours=1))

    body = client.get("/metrics").text

    assert "18.2%" in body
    # The older reading is not gone from the page and should not be: it is the
    # comparison beside the current one, which is what makes this a dashboard rather
    # than a snapshot. 18.2% means something different after a week at 50%.
    assert "was 50.0%" in body


def test_the_page_says_so_when_the_job_has_never_run(client, monkeypatch):
    """The empty state, reached by making `latest` return None rather than by
    emptying the table.

    The first draft of this test ran `delete from metrics` on the owner connection
    to produce the empty state, and it worked - it also destroyed a real metrics run
    on the way past, which was noticed only because a run written eleven minutes
    earlier had disappeared. `metrics` is append only by grant precisely because it
    is a series: every weekly row is one the week 14 gate reads, and none of them can
    be recomputed once the window has passed. A test that empties it to check a
    template branch trades the thing being measured for the measurement.

    So the absence is simulated at the boundary the page actually depends on. This
    tests strictly more than the delete did, because it also pins WHERE the page gets
    its numbers: if the route ever measured on request instead of reading the stored
    run, patching `latest` would not empty the page and this would fail.
    """
    monkeypatch.setattr("review.app.latest", lambda conn: None)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "No metrics run has been recorded" in response.text
    assert "make metrics" in response.text


def test_the_page_computes_nothing_of_its_own(client, stored):
    """Rule 5. The page renders the stored row and nothing it worked out itself.

    The figure asserted here is impossible: a 200% rejection rate cannot be measured
    from any state of the database. If the page recomputed anything, it would render
    the real 0.7% and this would fail. Two places computing one number is two answers,
    depending on which was read last.
    """
    stored(measured("translation_rejection_rate", 2.0, numerator=2, denominator=1))

    assert "200.0%" in client.get("/metrics").text


# --- the absences, which are the point of the page ---------------------------


def test_a_number_with_no_data_says_so_instead_of_rendering_nought(client, stored):
    """The failure this page exists to avoid.

    `0%` precision and `0` duplicates are both readings a person would act on, and
    both are what a missing value renders as under templating that treats absence as
    falsy. An unmeasurable metric has `value=None`, and the page must print words.
    """
    stored(absent("precision", "the golden set is unlabelled: 0 of 150 rows carry a label"))

    body = client.get("/metrics").text

    assert "not measurable" in body
    assert "the golden set is unlabelled" in body


def test_the_reason_travels_with_the_number_through_the_database(client, stored):
    """Stored on the row, not reconstructed at render time from the key.

    The note says what is missing and who can supply it. A page that regenerated it
    would describe today's reason beside a figure measured weeks ago.
    """
    # No apostrophe in the asserted fragment: Jinja escapes one to `&#39;`, and a
    # test that failed on that would be reporting on the template engine.
    why = "Under D31 the Monitor holds no CRM scope and runs no duplicate check"
    stored(absent("crm_duplicate_count", why))

    assert why in client.get("/metrics").text


def test_the_attention_band_counts_and_names_every_missing_number(client, stored):
    """What a person has to unblock, at the top, before any section is read."""
    stored(
        absent("precision", "the golden set is unlabelled"),
        absent("recall", "the golden set is unlabelled"),
        measured("connector_break_rate", 0.182, numerator=4, denominator=22),
    )

    body = client.get("/metrics").text

    assert "2 of 3 numbers are not measurable yet" in body


def test_a_fully_measured_run_shows_no_attention_band(client, stored):
    """The band is a signal, so it has to be absent when there is nothing to signal."""
    stored(measured("connector_break_rate", 0.182, numerator=4, denominator=22))

    assert "numbers are not measurable yet" not in client.get("/metrics").text


def test_missing_numbers_sort_above_measured_ones_inside_a_section(client, stored):
    """A reviewer scans this page. What needs a person comes first in its family."""
    run = stored(
        measured("golden_set_labelled", 0.0, numerator=0, denominator=150),
        absent("precision", "the golden set is unlabelled"),
    )

    _, members = next((title, members) for title, members in run.families() if len(members) > 1)
    assert [metric.measurable for metric in members] == [False, True]


# --- the role it reads as ----------------------------------------------------


def test_the_page_reads_as_the_reviewer_and_the_row_is_visible_to_that_role(stored, owner):
    """Rule 11. The write is the pipeline's; the read is the review app's.

    Asserted end to end rather than by reading a grant file: the row goes in through
    the real `write()` and comes back through the real `latest()` on a review
    connection, which is the pair the page depends on.
    """
    import monitor.db as db

    run = stored(measured("export_backlog", 0, unit="count", numerator=0, denominator=0))

    with db.connect("review") as conn:
        read_back = latest(conn)

    assert read_back is not None
    assert read_back.run_id == run.run_id
