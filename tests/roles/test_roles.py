"""The checkpoint.

Not a test of anything the application does: a test that the database refuses the
pipeline the privileges this whole design depends on it not having. It exists
before any code that might need it and it runs on every commit for all 31 steps.

It needs a live database with both migrations applied. It does not skip when one
is absent: a checkpoint test that quietly passes because nothing was there to
test is worse than no test. Run `make up` first.

The fixture rows are created and removed on an owner connection. That is not a
runtime path and nothing under test uses it (rule 11): neither runtime role holds
delete on any table, because the audit log and the approved records are append
only, so a test that commits a fixture row cannot clean it up as either role.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.roles

APPROVED_RECORD_ID = "R999999"
EXPORT_BATCH_ID = "B9999"


def url(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        pytest.fail(f"{env_var} is not set; run 'make up' before 'make test'")
    return value


@pytest.fixture
def owner():
    with psycopg.connect(url("DATABASE_URL_OWNER"), autocommit=True) as conn:
        # A cleanup that has to wait on a row lock is a bug in this file, not
        # something to sit through: fail in five seconds and say so.
        conn.execute("set lock_timeout = '5s'")
        yield conn


@pytest.fixture
def pipeline():
    """A pipeline connection. Everything it does here is rolled back."""
    with psycopg.connect(url("DATABASE_URL_PIPELINE")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def review():
    with psycopg.connect(url("DATABASE_URL_REVIEW")) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def candidate(owner, pipeline, review) -> str:
    """A staged candidate and the rows behind it, committed so both roles see it.

    It takes both role connections so that it is created after them and torn down
    before them, and so the cleanup can release their row locks first. Without
    that the delete waits on a transaction pytest has not closed yet.
    """
    marker = uuid.uuid4().hex[:8]
    source_id = f"test-{marker}"
    content_hash = f"sha256:{marker}"
    candidate_id = f"C{int(marker, 16) % 1_000_000:06d}"

    owner.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min,
                             expected_max, max_consecutive_failures)
        values (%s, 'checkpoint test source', 'GH', 'national', 'en', 'feed', 'api',
                'FeedConnector', 1, 'cleared', false, 1, 50, 3)
        """,
        (source_id,),
    )
    owner.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, 'https://example.invalid/notice', 'raw/test.json', 'application/json')
        """,
        (content_hash, source_id),
    )
    notice_id = owner.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level,
                             language, status)
        values (%s, %s, 'https://example.invalid/notice', 'GIFMIS modernisation', 'GH',
                'national', 'en', 'scored')
        returning id
        """,
        (content_hash, source_id),
    ).fetchone()[0]
    owner.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language,
                                title_en, country, admin_level, summary_en, matched_functions,
                                procurement_type)
        values (%s, %s, 78, 'pending_review', 'West Africa', 'en',
                'GIFMIS modernisation', 'GH', 'national', 'A summary.', '[]'::jsonb, 'software')
        """,
        (candidate_id, notice_id),
    )

    yield candidate_id

    pipeline.rollback()
    review.rollback()
    owner.execute("delete from approved_records where candidate_id = %s", (candidate_id,))
    owner.execute("delete from export_batches where batch_id = %s", (EXPORT_BATCH_ID,))
    owner.execute("delete from candidate_notices where candidate_id = %s", (candidate_id,))
    owner.execute("delete from candidates where id = %s", (candidate_id,))
    owner.execute("delete from notices where content_hash = %s", (content_hash,))
    owner.execute("delete from notices_raw where content_hash = %s", (content_hash,))
    owner.execute("delete from sources where id = %s", (source_id,))


# --- the pipeline is refused -------------------------------------------------


def test_pipeline_cannot_insert_an_approved_record(pipeline, candidate):
    with pytest.raises(errors.InsufficientPrivilege):
        pipeline.execute(
            """
            insert into approved_records (id, candidate_id, record, approved_by)
            values (%s, %s, '{}'::jsonb, 'not a reviewer')
            """,
            (APPROVED_RECORD_ID, candidate),
        )


def test_pipeline_cannot_insert_an_export_batch(pipeline):
    with pytest.raises(errors.InsufficientPrivilege):
        pipeline.execute(
            """
            insert into export_batches (batch_id, operator, row_count, range_from, range_to,
                                        file_path, manifest_path, sha256)
            values (%s, 'not an operator', 0, now(), now(), 'a.csv', 'a.json', 'deadbeef')
            """,
            (EXPORT_BATCH_ID,),
        )


def test_pipeline_cannot_approve_a_candidate(pipeline, candidate):
    with pytest.raises(errors.InsufficientPrivilege):
        pipeline.execute(
            "update candidates set status = 'approved', reviewer = 'someone' where id = %s",
            (candidate,),
        )


def test_pipeline_cannot_reject_a_candidate(pipeline, candidate):
    with pytest.raises(errors.InsufficientPrivilege):
        pipeline.execute(
            """
            update candidates set status = 'rejected', reviewer = 'someone',
                                  rejection_reason = 'not relevant'
            where id = %s
            """,
            (candidate,),
        )


def test_pipeline_cannot_read_an_approved_record(pipeline):
    """No privilege of any kind on that table, select included."""
    with pytest.raises(errors.InsufficientPrivilege):
        pipeline.execute("select count(*) from approved_records")


def test_pipeline_may_still_stage_a_candidate(pipeline, candidate):
    """The pipeline's own job still works. The grant is narrow, not blunt."""
    pipeline.execute("update candidates set status = 'pending_review' where id = %s", (candidate,))

    status = pipeline.execute("select status from candidates where id = %s", (candidate,)).fetchone()[0]
    assert status == "pending_review"


# --- the reviewer is allowed -------------------------------------------------


def test_review_can_approve_insert_and_export(review, candidate):
    """The same three operations, as the reviewer, all succeed and are rolled back."""
    review.execute(
        "update candidates set status = 'approved', reviewer = 'Ryan Dear' where id = %s",
        (candidate,),
    )
    review.execute(
        """
        insert into approved_records (id, candidate_id, record, approved_by)
        values (%s, %s, '{"Opportunity Name": "GIFMIS modernisation"}'::jsonb, 'Ryan Dear')
        """,
        (APPROVED_RECORD_ID, candidate),
    )
    review.execute(
        """
        insert into export_batches (batch_id, operator, row_count, range_from, range_to,
                                    file_path, manifest_path, sha256)
        values (%s, 'Named Operator', 1, now(), now(),
                'exports/B9999.csv', 'exports/B9999.manifest.json', 'deadbeef')
        """,
        (EXPORT_BATCH_ID,),
    )
    review.execute(
        "update approved_records set exported_at = now(), export_batch = %s where id = %s",
        (EXPORT_BATCH_ID, APPROVED_RECORD_ID),
    )

    approved_by = review.execute(
        "select approved_by from approved_records where id = %s", (APPROVED_RECORD_ID,)
    ).fetchone()[0]
    assert approved_by == "Ryan Dear"

    review.rollback()
    remaining = review.execute("select count(*) from approved_records where id = %s", (APPROVED_RECORD_ID,)).fetchone()[
        0
    ]
    assert remaining == 0


def test_review_cannot_rewrite_an_approved_record(review, candidate):
    """Update on approved_records is two columns, not the payload."""
    review.execute(
        """
        insert into approved_records (id, candidate_id, record, approved_by)
        values (%s, %s, '{}'::jsonb, 'Ryan Dear')
        """,
        (APPROVED_RECORD_ID, candidate),
    )
    with pytest.raises(errors.InsufficientPrivilege):
        review.execute(
            "update approved_records set record = %s::jsonb where id = %s",
            ('{"tampered": true}', APPROVED_RECORD_ID),
        )


def test_review_cannot_approve_without_a_reviewer(review, candidate):
    """Rule 13 in the database: no named reviewer, no decision."""
    with pytest.raises(errors.CheckViolation):
        review.execute(
            "update candidates set status = 'approved', reviewer = '  ' where id = %s",
            (candidate,),
        )


def test_readonly_cannot_write_anything(candidate):
    """The reporting role and the ops-analyst agent read and never write."""
    with psycopg.connect(url("DATABASE_URL_READONLY")) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("update candidates set score = 1 where id = %s", (candidate,))
        conn.rollback()
        assert conn.execute("select count(*) from candidates").fetchone()[0] >= 1
