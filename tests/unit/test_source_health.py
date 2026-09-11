"""Source health transitions.

The rule worth the test: a run that reaches a source and parses it but brings
back nothing new, on a source that normally yields, is a failure state. A portal
that silently changes its markup answers 200 with zero rows, and without this the
morning report reads clean while the pipeline sees nothing (rule 4).
"""

from __future__ import annotations

from datetime import UTC, datetime

from monitor.health.source_health import Health, RunOutcome, next_health

AT = datetime(2026, 9, 11, 6, 0, tzinfo=UTC)
MAX_FAILURES = 3


def outcome(*, failed=False, seen=12, new=12, at=AT) -> RunOutcome:
    return RunOutcome(at=at, failed=failed, items_seen=seen, items_new=new)


def healthy_source(**overrides) -> Health:
    return Health(source_id="ted", **overrides)


def test_a_good_run_is_healthy_and_stamps_the_success():
    health = next_health(healthy_source(), outcome(), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert health.state == "healthy"
    assert health.last_success_at == AT
    assert health.consecutive_failures == 0


def test_a_good_run_resets_a_failure_streak():
    previous = healthy_source(consecutive_failures=2, state="watch")

    health = next_health(previous, outcome(), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert (health.consecutive_failures, health.state) == (0, "healthy")


def test_failures_accumulate_to_watch_then_unhealthy():
    health = healthy_source()
    states = []
    for _ in range(MAX_FAILURES):
        health = next_health(health, outcome(failed=True), expected_min=5, max_consecutive_failures=MAX_FAILURES)
        states.append(health.state)

    assert states == ["healthy", "watch", "unhealthy"]
    assert health.consecutive_failures == MAX_FAILURES


def test_a_failed_run_does_not_move_the_last_success():
    previous = healthy_source(last_success_at=AT)

    health = next_health(previous, outcome(failed=True), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert health.last_success_at == AT


def test_zero_yield_on_a_yielding_source_is_a_failure_state():
    """200 OK and nothing seen at all is how a silently broken parser looks."""
    first = next_health(healthy_source(), outcome(seen=0, new=0), expected_min=5, max_consecutive_failures=MAX_FAILURES)
    second = next_health(first, outcome(seen=0, new=0), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert (first.zero_yield_runs, first.state) == (1, "healthy")
    assert (second.zero_yield_runs, second.state) == (2, "watch")
    assert second.last_zero_yield_at == AT


def test_zero_yield_reaches_unhealthy_at_the_sources_own_limit():
    health = healthy_source()
    for _ in range(MAX_FAILURES):
        health = next_health(health, outcome(seen=0, new=0), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert health.state == "unhealthy"


def test_zero_yield_is_not_a_failure_on_a_source_that_may_publish_nothing():
    """expected_min of 0 says quiet days are normal for this source."""
    health = next_health(
        healthy_source(), outcome(seen=0, new=0), expected_min=0, max_consecutive_failures=MAX_FAILURES
    )

    assert (health.zero_yield_runs, health.state) == (0, "healthy")


def test_seen_but_nothing_new_is_a_healthy_run():
    """The re-read case, and the reason the counter is on items_seen.

    TED's query looks back two days, so every run after the first on a given day
    sees over a thousand notices and takes none of them. Counting that as zero
    yield put a working source at unhealthy within three hours, which is what
    step 9's acceptance caught.
    """
    health = next_health(
        healthy_source(), outcome(seen=1449, new=0), expected_min=50, max_consecutive_failures=MAX_FAILURES
    )

    assert health.zero_yield_runs == 0
    assert health.state == "healthy"
    assert health.last_success_at == AT


def test_repeated_re_reads_never_reach_unhealthy():
    """The bug this fixed: three hourly runs over an unchanged window."""
    health = healthy_source()
    for _ in range(5):
        health = next_health(health, outcome(seen=1449, new=0), expected_min=50, max_consecutive_failures=MAX_FAILURES)

    assert health.state == "healthy"


def test_a_good_run_clears_a_zero_yield_streak():
    previous = healthy_source(zero_yield_runs=2, state="watch")

    health = next_health(previous, outcome(), expected_min=5, max_consecutive_failures=MAX_FAILURES)

    assert (health.zero_yield_runs, health.state) == (0, "healthy")
