"""Source health after a run.

Rule 4's sharpest edge lives here: a source that normally yields and yields
nothing is in a failure state, not an empty success. A portal that silently
changes its markup returns HTTP 200 and zero rows, and without this the pipeline
would report a clean run every morning while quietly seeing nothing.

The transition rules, from BUILD_ORDER step 4:

  - a successful run with new items resets the failure and zero-yield counters;
  - a failed run increments consecutive_failures;
  - a run that yields nothing new on a source whose expected_min is above zero
    increments zero_yield_runs;
  - either counter at 2 puts the source in `watch`, and at the source's own
    `max_consecutive_failures` it is `unhealthy`.

`next_health` is pure: it takes the current row and the run outcome and returns
the next row. The database write is separate so the rules can be tested without
one, and so there is one place to read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

WATCH_THRESHOLD = 2

State = str  # healthy | watch | unhealthy | unknown


@dataclass(frozen=True)
class Health:
    """A `source_health` row."""

    source_id: str
    last_success_at: datetime | None = None
    consecutive_failures: int = 0
    median_items: float | None = None
    last_zero_yield_at: datetime | None = None
    zero_yield_runs: int = 0
    state: State = "unknown"


@dataclass(frozen=True)
class RunOutcome:
    """What one fetch run did. `failed` and `items_new` are independent."""

    at: datetime
    failed: bool
    items_seen: int
    items_new: int


def next_health(current: Health, outcome: RunOutcome, expected_min: int, max_consecutive_failures: int) -> Health:
    """The health row after this run. Pure; no database, no clock."""
    if outcome.failed:
        failures = current.consecutive_failures + 1
        return Health(
            source_id=current.source_id,
            last_success_at=current.last_success_at,
            consecutive_failures=failures,
            median_items=current.median_items,
            last_zero_yield_at=current.last_zero_yield_at,
            zero_yield_runs=current.zero_yield_runs,
            state=_state(failures, current.zero_yield_runs, max_consecutive_failures),
        )

    # A run that reached the source and parsed it is a success for the failure
    # counter even when it brought back nothing: the source answered.
    zero_yield = expected_min > 0 and outcome.items_new == 0
    zero_yield_runs = current.zero_yield_runs + 1 if zero_yield else 0

    return Health(
        source_id=current.source_id,
        last_success_at=outcome.at,
        consecutive_failures=0,
        median_items=current.median_items,
        last_zero_yield_at=outcome.at if zero_yield else current.last_zero_yield_at,
        zero_yield_runs=zero_yield_runs,
        state=_state(0, zero_yield_runs, max_consecutive_failures),
    )


def _state(failures: int, zero_yield_runs: int, max_consecutive_failures: int) -> State:
    worst = max(failures, zero_yield_runs)
    if worst >= max_consecutive_failures:
        return "unhealthy"
    if worst >= WATCH_THRESHOLD:
        return "watch"
    return "healthy"


def read(conn: psycopg.Connection, source_id: str) -> Health:
    """The current row, or an unknown-state row for a source that has never run."""
    row = conn.execute(
        """
        select last_success_at, consecutive_failures, median_items,
               last_zero_yield_at, zero_yield_runs, state
        from source_health where source_id = %s
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        return Health(source_id=source_id)
    return Health(source_id, *row)


def write(conn: psycopg.Connection, health: Health) -> None:
    conn.execute(
        """
        insert into source_health (source_id, last_success_at, consecutive_failures, median_items,
                                   last_zero_yield_at, zero_yield_runs, state)
        values (%s, %s, %s, %s, %s, %s, %s)
        on conflict (source_id) do update set
            last_success_at = excluded.last_success_at,
            consecutive_failures = excluded.consecutive_failures,
            median_items = excluded.median_items,
            last_zero_yield_at = excluded.last_zero_yield_at,
            zero_yield_runs = excluded.zero_yield_runs,
            state = excluded.state
        """,
        (
            health.source_id,
            health.last_success_at,
            health.consecutive_failures,
            health.median_items,
            health.last_zero_yield_at,
            health.zero_yield_runs,
            health.state,
        ),
    )
