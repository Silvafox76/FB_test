"""`monitor status` counts what passed the filter, not what is still waiting to be scored.

Until 2026-09-13 the "passed" column counted `status = 'filtered_in'`, a status a
notice holds only between the filter and the scorer. Once everything was scored the
line read "considered 2357, passed 0, drop rate 100.0%" against 146 scored notices.
This pins the count to every status a notice can hold after passing.
"""

from __future__ import annotations

import uuid

import pytest

from monitor.health.status import SourceStatus, collect

pytestmark = pytest.mark.roles


def _notice(conn, source_id: str, status: str) -> None:
    content_hash = f"sha256:status-{uuid.uuid4().hex}"
    conn.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, 'https://example.invalid/n', 'raw/x.json', 'application/json')
        """,
        (content_hash, source_id),
    )
    conn.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level, language, status)
        values (%s, %s, 'https://example.invalid/n', 'status test', 'LR', 'national', 'en', %s)
        """,
        (content_hash, source_id, status),
    )


def test_passed_counts_every_status_after_the_filter(db_conn):
    source_id = f"test-status-{uuid.uuid4().hex[:8]}"
    db_conn.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min, expected_max,
                             max_consecutive_failures)
        values (%s, 'status test', 'LR', 'national', 'en', 'national', 'api', 'FeedConnector', 1,
                'reviewed_ok', false, 1, 10, 3)
        """,
        (source_id,),
    )
    for status in ("filtered_in", "scored", "parked", "filtered_out", "filtered_out", "detected"):
        _notice(db_conn, source_id, status)

    status = next(s for s in collect(db_conn) if s.source_id == source_id)

    assert status.passed == 3
    assert status.dropped == 2
    assert status.considered == 5
    assert status.drop_rate == pytest.approx(0.4)


def test_drop_rate_has_no_denominator_before_the_filter_has_run():
    status = SourceStatus(
        source_id="x",
        enabled=True,
        state="unknown",
        last_success_at=None,
        consecutive_failures=0,
        notices=3,
        passed=0,
        dropped=0,
        needs_translation=0,
        unfiltered=3,
    )
    assert status.considered == 0
    assert status.drop_rate == 0.0
