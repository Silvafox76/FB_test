"""Fixtures for the review app's tests.

A live database with every migration applied, the two runtime roles, and one staged
candidate clustered from a national source and a donor one. Two sources on purpose:
appendix E's Partners Involved and Funding Source both read the cluster, so a
one-source fixture would leave the interesting columns empty and the tests would
pass without testing them.

The fixture rows are created and removed on an owner connection, because neither
runtime role holds delete on any table: the audit log and the approved records are
append only by grant, so a committed fixture row cannot be cleaned up as the
reviewer.
"""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest

from monitor.stage.stager import FIXTURE_ID_FLOOR
from tests.review.fixture_cleanup import safe_execute


def url(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        pytest.fail(f"{env_var} is not set; run 'make up' before 'make test'")
    return value


@pytest.fixture
def owner():
    with psycopg.connect(url("DATABASE_URL_OWNER"), autocommit=True) as conn:
        conn.execute("set lock_timeout = '5s'")
        yield conn


@pytest.fixture
def review():
    """A reviewer connection. Unlike the roles tests, what it writes here commits."""
    with psycopg.connect(url("DATABASE_URL_REVIEW")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def staged(owner, review):
    """A pending_review candidate, its two notices and the two sources behind them."""
    marker = uuid.uuid4().hex[:8]
    # Reserved block, not the whole six-digit space: see FIXTURE_ID_FLOOR. This
    # teardown deletes by candidate id, so an id the stager could also allocate
    # would make it destructive.
    candidate_id = f"C{FIXTURE_ID_FLOOR + int(marker, 16) % (1_000_000 - FIXTURE_ID_FLOOR):06d}"
    national = f"test-nat-{marker}"
    donor = f"test-wb-{marker}"

    for source_id, name, admin_level in (
        (national, "Ghana Public Procurement Authority", "national"),
        (donor, "World Bank procurement notices", "donor"),
    ):
        owner.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, %s, 'GH', %s, 'en', 'feed', 'api', 'FeedConnector', 1, 'cleared',
                    false, 1, 50, 3)
            """,
            (source_id, name, admin_level),
        )

    notice_ids = []
    for index, source_id in enumerate((national, donor)):
        content_hash = f"sha256:{marker}{index}"
        owner.execute(
            """
            insert into notices_raw (content_hash, source_id, url, storage_path, mime)
            values (%s, %s, 'https://example.invalid/notice', 'raw/test.json', 'application/json')
            """,
            (content_hash, source_id),
        )
        notice_ids.append(
            owner.execute(
                """
                insert into notices (content_hash, source_id, url, title, country, admin_level,
                                     language, status)
                values (%s, %s, 'https://example.invalid/notice', 'GIFMIS modernisation', 'GH',
                        'national', 'en', 'scored')
                returning id
                """,
                (content_hash, source_id),
            ).fetchone()[0]
        )

    owner.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                buyer, country, admin_level, summary_en, matched_functions,
                                system_names, procurement_type, estimated_value, value_currency,
                                estimated_value_usd, value_rate, value_rate_date,
                                eligibility_flags, deadline_at)
        -- Published in USD and converted through the identity rate, which is what a
        -- donor-funded Ghanaian tender quoted in dollars actually looks like. The
        -- rate and its date are set because migration 012 refuses a USD figure
        -- without them: an unattributed number is the thing that change removed.
        values (%s, %s, 78, 'pending_review', 'West Africa', 'en',
                'Supply and implementation of an integrated financial management system',
                'Ministry of Finance', 'GH', 'national', 'Ghana is replacing its IFMIS.',
                %s::jsonb, %s, 'system', 4200000, 'USD', 4200000, 1.0, '2026-09-14',
                %s, '2026-11-30T17:00:00Z')
        """,
        (
            candidate_id,
            notice_ids[0],
            json.dumps([{"function_id": "budget_execution", "evidence": "integrated financial management system"}]),
            ["IFMIS", "GIFMIS"],
            [],
        ),
    )
    for notice_id in notice_ids:
        owner.execute(
            """
            insert into candidate_notices (candidate_id, notice_id, match_method, match_score)
            values (%s, %s, 'content_hash', 100)
            """,
            (candidate_id, notice_id),
        )

    yield candidate_id

    # Each statement below is independent (`safe_execute` logs and moves on
    # rather than raising), because a killed process never reaches this code at
    # all: what a live process CAN still do, if one delete hits a lock or an
    # unexpected row, is make sure that failure does not stop the rest of the
    # teardown from running too. Order is still FK order; robustness is not a
    # reason to guess at it.
    review.rollback()
    safe_execute(owner, "delete from events where entity_id = %s", (candidate_id,), context="staged: events")
    safe_execute(
        owner,
        "delete from events where entity_id in (select id from approved_records where candidate_id = %s)",
        (candidate_id,),
        context="staged: approved_record events",
    )
    safe_execute(
        owner,
        "update candidates set approved_record_id = null where id = %s",
        (candidate_id,),
        context="staged: clear approved_record_id",
    )
    safe_execute(
        owner,
        "delete from approved_records where candidate_id = %s",
        (candidate_id,),
        context="staged: approved_records",
    )
    safe_execute(
        owner,
        "delete from candidate_notices where candidate_id = %s",
        (candidate_id,),
        context="staged: candidate_notices",
    )
    safe_execute(owner, "delete from candidates where id = %s", (candidate_id,), context="staged: candidates")
    for notice_id in notice_ids:
        safe_execute(owner, "delete from notices where id = %s", (notice_id,), context="staged: notices")
    safe_execute(
        owner,
        "delete from notices_raw where source_id in (%s, %s)",
        (national, donor),
        context="staged: notices_raw",
    )
    safe_execute(owner, "delete from sources where id in (%s, %s)", (national, donor), context="staged: sources")
