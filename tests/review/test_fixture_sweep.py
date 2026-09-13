"""Proves the sweep's markers are exact: a real-looking row survives it.

Runs `sweep_fixture_leftovers` for real, on a real owner connection, inside one
transaction that is rolled back at the end - so whatever the sweep does or does
not remove, nothing here is left touched on the database once the test finishes.
It runs against the live database rather than a stub because the property under
test is which rows the deployed schema and the sweep's own SQL actually match,
not what a description of the markers claims.

The two rows planted are the two shapes the sweep must never confuse for its own
markers: a source named like a real connector (`ted`, already seeded and enabled)
and a candidate with an id below `FIXTURE_ID_FLOOR`, which is what every
candidate `stager.run()` allocates for real notices looks like.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from monitor.stage.stager import FIXTURE_ID_FLOOR
from tests.review.fixture_cleanup import sweep_fixture_leftovers

pytestmark = pytest.mark.roles


def url(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        pytest.fail(f"{env_var} is not set; run 'make up' before 'make test'")
    return value


@pytest.fixture
def owner_txn():
    """An owner connection that runs the sweep for real, then rolls everything back."""
    with psycopg.connect(url("DATABASE_URL_OWNER")) as conn:
        yield conn
        conn.rollback()


def test_a_real_source_and_a_low_candidate_are_untouched(owner_txn):
    if owner_txn.execute("select count(*) from sources where id = 'ted'").fetchone()[0] == 0:
        pytest.fail("source 'ted' is not seeded; run 'make up' before this test")

    content_hash = f"sha256:fixture-sweep-safety-{uuid.uuid4().hex}"
    # One below the reserved floor: exactly where a real, sequence-allocated
    # candidate id lives, and exactly what the sweep must not treat as reserved.
    low_candidate_id = f"C{FIXTURE_ID_FLOOR - 1:06d}"

    owner_txn.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, 'ted', 'https://example.invalid/n', 'raw/x.json', 'application/json')
        """,
        (content_hash,),
    )
    notice_id = owner_txn.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level, language, status)
        values (%s, 'ted', 'https://example.invalid/n', 'A real-looking TED notice', 'EU', 'national', 'en', 'scored')
        returning id
        """,
        (content_hash,),
    ).fetchone()[0]
    owner_txn.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                country, admin_level, summary_en, matched_functions, procurement_type)
        values (%s, %s, 80, 'pending_review', 'Europe', 'en', 'A real-looking TED candidate',
                'EU', 'national', 'A summary.', '[]'::jsonb, 'software')
        """,
        (low_candidate_id, notice_id),
    )

    sweep_fixture_leftovers(owner_txn)

    assert owner_txn.execute("select count(*) from sources where id = 'ted'").fetchone()[0] == 1
    assert owner_txn.execute("select count(*) from candidates where id = %s", (low_candidate_id,)).fetchone()[0] == 1
    assert owner_txn.execute("select count(*) from notices where id = %s", (notice_id,)).fetchone()[0] == 1
    raw_count = owner_txn.execute(
        "select count(*) from notices_raw where content_hash = %s", (content_hash,)
    ).fetchone()[0]
    assert raw_count == 1
