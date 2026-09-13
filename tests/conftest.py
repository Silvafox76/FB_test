"""Shared fixtures.

`db_conn` is a pipeline connection whose work is rolled back, so a test that
writes model_calls rows leaves none behind.

The session-start sweep lives here rather than only in `tests/review/conftest.py`
because it is not only the review app's fixtures that need it. `stager.run()`
(`tests/unit/test_stager.py::seeded`) and `rescore()`
(`tests/unit/test_rescore.py::seeded`) both commit per candidate, so a process
killed after that commit but before the fixture's own owner-connection teardown
runs leaves rows no later `db_conn` rollback can touch. And it is not only that
fixture's own test that then sees the damage: `tests/unit/test_stager.py`'s dedupe
join and `tests/unit/test_backfill_values.py`'s idempotency scan both read across
the whole `candidates` or `notices` table, so an orphan left by one killed run
shows up as a wrong answer in a completely different test, possibly minutes or
days later. Sweeping once here, before any test in the session runs, is what
catches that - per-test teardown, even made robust, only ever runs for a process
that lives long enough to reach it.
"""

from __future__ import annotations

import os

import psycopg
import pytest
import structlog

from tests.review.fixture_cleanup import sweep_fixture_leftovers

log = structlog.get_logger(__name__)


@pytest.fixture
def db_conn():
    url = os.environ.get("DATABASE_URL_PIPELINE")
    if not url:
        pytest.fail("DATABASE_URL_PIPELINE is not set; run 'make up' before 'make test'")
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()


@pytest.fixture(scope="session", autouse=True)
def _sweep_fixture_leftovers_once():
    """Remove whatever a killed test process left behind, once, before any test runs.

    Connects as owner, the same as `tests/review/conftest.py` and
    `tests/roles/test_roles.py` already do and for the same reason (rule 11): this
    is test infrastructure cleaning up its own fixtures' rows, not a runtime path,
    and no module under `monitor/` or `review/` imports
    `tests.review.fixture_cleanup`. It touches only the two markers that module
    documents - `candidates.id >= FIXTURE_ID_FLOOR` and `sources.id like
    'test-%'` - and nothing outside them; that property is what
    `tests/review/test_fixture_sweep.py` proves against the live database.

    Known hazard, observed directly rather than theorised: this sweep is safe
    against a killed run's leftovers but not against a live one's in-progress
    rows. Two `pytest` invocations against the same database at the same time
    each fire this fixture at their own session start, and each sweep matches
    every row currently satisfying the two markers, including a `test-%` source
    or a `C9xxxxx` candidate the OTHER invocation's fixture committed seconds
    earlier and has not torn down yet. The result is a fixture in a still-running
    test disappearing out from under it - a `ForeignKeyViolation` on
    `approved_records`, most visibly - with a stack trace that looks like a bug in
    whatever test happened to be mid-flight, not in this sweep. It is not a defect
    in the sweep; it is what a sweep-at-session-start design does under concurrent
    runs, and the repo's convention is one `make test` at a time. No locking, retry
    or opt-out is added for it - see the file's own history for why - so the fix,
    for now, is knowing this paragraph exists the next time a fixture vanishes for
    no reason a single invocation's logs can explain.
    """
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        pytest.fail("DATABASE_URL_OWNER is not set; run 'make up' before 'make test'")
    with psycopg.connect(url, autocommit=True) as conn:
        removed = sweep_fixture_leftovers(conn)
    log.info("fixture_sweep_session_start", **removed)
