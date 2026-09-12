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

from monitor.stage.stager import FIXTURE_ID_FLOOR

pytestmark = pytest.mark.roles

APPROVED_RECORD_ID = "R999999"
EXPORT_BATCH_ID = "B9999"

# The top of the `C[0-9]{6}` range the schema allows, reserved for fixtures.
#
# `candidates.id` is constrained to six digits and the stager allocates them from
# `candidate_id_seq`, which starts at 1 and had reached C000815 by 2026-09-12. A
# fixture that drew its id uniformly from the whole range therefore collided with a
# real staged candidate about once every 7,000 draws - ten draws per run, so roughly
# one run in 700, which is what a full suite did once on that date: an ERROR in setup
# on `test_review_can_approve_insert_and_export` that would not reproduce.
#
# The error was harmless (the insert fails before `yield`, so the teardown that would
# have deleted the real row never runs) and it is still worth removing, for two
# reasons. The odds grow with the table: at 50,000 candidates it is one run in two.
# And this is the checkpoint file - the one test that has to be believed on every
# commit for all 31 steps - so an error in it that has nothing to do with rule 11
# teaches the reader to discount the next one.
#
# Drawing from 900000-999999 puts the fixture where the sequence will not arrive, the
# same reservation `APPROVED_RECORD_ID` and `EXPORT_BATCH_ID` above already use. The
# floor is imported from the allocator that has to respect it rather than repeated
# here, and `monitor/stage/stager.py` raises if the sequence ever reaches it.
FIXTURE_ID_CEILING = 1_000_000


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
    span = FIXTURE_ID_CEILING - FIXTURE_ID_FLOOR
    candidate_id = f"C{FIXTURE_ID_FLOOR + int(marker, 16) % span:06d}"

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


# --- the fixture cannot reach a real candidate -------------------------------


def test_the_fixture_id_is_outside_the_range_the_stager_allocates(owner, candidate):
    """The teardown deletes by id, so an id the pipeline could also hold is a hazard.

    Not a test of the system: a test that this file cannot damage the database it
    runs against. `candidate` tears down with `delete from candidates where id = %s`,
    which is correct for a row this file created and destructive for one the stager
    created. Keeping the two id spaces disjoint is what makes that delete safe, so
    the disjointness is asserted rather than assumed.
    """
    assert int(candidate[1:]) >= FIXTURE_ID_FLOOR

    reached = owner.execute("select last_value from candidate_id_seq").fetchone()[0]
    assert reached < FIXTURE_ID_FLOOR, (
        f"candidate_id_seq has reached {reached}, inside the block reserved for fixtures; "
        "widen the id format or move the reservation before this file deletes a real row"
    )


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
