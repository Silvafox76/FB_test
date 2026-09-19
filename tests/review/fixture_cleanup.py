"""Shared plumbing for cleaning up test-created rows: per-test teardown and the
one-per-session sweep for whatever a killed test process left behind.

Every function here runs on an owner connection, never on either runtime role's,
for the same reason `tests/review/conftest.py` and `tests/roles/test_roles.py`
already give: this is test infrastructure removing rows its own fixtures created,
not a runtime path (rule 11), and no `monitor/` or `review/` module imports this
file.

Two markers, and nothing outside them is ever read for identity - only joined to
reach the rows that hang off them:

  - `candidates.id >= 'C900000'` (`monitor.stage.stager.FIXTURE_ID_FLOOR`): the
    reserved block a fixture that hand-picks a candidate id draws from.
  - `sources.id like 'test-%'`: the fixture source-id prefixes measured directly
    from the tests that create them - `test-` (tests/roles/test_roles.py,
    tests/unit/test_stager.py, tests/unit/test_rescore.py), `test-nat-` and
    `test-wb-` (tests/review/conftest.py), `test-exp-` (tests/review/test_export.py),
    `test-val-` (tests/review/test_app.py), `test-no-connector-`
    (tests/unit/test_backfill_values.py). Every one of them starts with "test-"
    and no real source id does (ted, prozorro, fts, worldbank, liberia, ...), so
    this single pattern is the exact union of what was measured, not a guess
    wider than it.

    This marker exists because `stager.run()` and `rescore()` each commit per
    candidate, so a fixture that lets either allocate a candidate id from the
    real, shared sequence - rather than drawing one from the reserved block
    itself, as `tests/unit/test_stager.py::seeded` and
    `tests/unit/test_rescore.py::seeded` both do - leaves an orphan the first
    marker alone cannot find. Only its source can.

A third, narrower step below removes stale `export_batches` rows: batches a killed
export test left behind, named by the operator strings the export tests themselves
use - `csrf-test-` (`operator_name` in `tests/review/test_csrf.py`) and
`Export Operator ` (the `approved` fixture in `tests/review/test_export.py`). A
batch row is never deleted while any `approved_records` row still references it -
`approved_records.export_batch` is a foreign key to it - so this only ever removes
a batch whose file, if any, nothing in the database still points at.
"""

from __future__ import annotations

import structlog

from monitor.stage.stager import FIXTURE_ID_FLOOR

log = structlog.get_logger(__name__)

FIXTURE_SOURCE_PATTERN = "test-%"

# The export tests' own operator-string prefixes (see the docstring above). Not a
# marker used to find candidates or sources - only to find `export_batches` rows
# those tests produced and never cleaned up because the process that created them
# was killed before its fixture's teardown ran.
TEST_OPERATOR_PATTERNS = ("csrf-test-%", "Export Operator %")


def safe_execute(conn, sql: str, params: tuple = (), *, context: str = "") -> int:
    """Run one statement; log and continue rather than let it stop what comes after.

    Teardown runs as code after `yield`, which a killed process never reaches -
    but a process that lives long enough to reach it should still get as much
    cleanup as it can if one statement fails (a lock, a row already gone, an
    order a concurrent run disturbed). Every delete is its own statement and its
    own try/except so a failure on one table does not abandon the tables after it.
    """
    try:
        cursor = conn.execute(sql, params)
        return cursor.rowcount
    except Exception as exc:  # noqa: BLE001 - logged; teardown must never raise here
        log.error("fixture_cleanup_statement_failed", context=context, error=repr(exc))
        return 0


def sweep_fixture_leftovers(conn) -> dict[str, int]:
    """Delete rows a fixture left behind because its process was killed before
    the code after `yield` ran, in FK order, touching only the two markers above.

    Every statement runs through `safe_execute`, so one failure does not stop the
    rest. Returns the row count removed per table, for logging.
    """
    reserved_floor = f"C{FIXTURE_ID_FLOOR:06d}"

    candidate_ids = {
        row[0] for row in conn.execute("select id from candidates where id >= %s", (reserved_floor,)).fetchall()
    }
    source_ids = {
        row[0] for row in conn.execute("select id from sources where id like %s", (FIXTURE_SOURCE_PATTERN,)).fetchall()
    }

    if source_ids:
        sources_list = list(source_ids)
        candidate_ids |= {
            row[0]
            for row in conn.execute(
                """
                select c.id from candidates c
                join notices n on n.id = c.primary_notice_id
                where n.source_id = any(%s::text[])
                """,
                (sources_list,),
            ).fetchall()
        }
        candidate_ids |= {
            row[0]
            for row in conn.execute(
                """
                select cn.candidate_id from candidate_notices cn
                join notices n on n.id = cn.notice_id
                where n.source_id = any(%s::text[])
                """,
                (sources_list,),
            ).fetchall()
        }

    ids = list(candidate_ids)
    sources = list(source_ids)

    removed: dict[str, int] = {}

    # 1. events, both the candidate's own and any approved-record event that
    #    names it, before anything the events reference is deleted.
    removed["events"] = safe_execute(
        conn,
        """
        delete from events
        where (entity_type = 'candidate' and entity_id = any(%(ids)s::text[]))
           or (entity_type = 'approved_record' and entity_id in (
                 select id from approved_records where candidate_id = any(%(ids)s::text[])))
        """,
        {"ids": ids},
        context="events",
    )
    # 2. candidates.approved_record_id -> approved_records.id: null it before the
    #    approved_records row can be deleted, or the delete violates that FK.
    safe_execute(
        conn,
        "update candidates set approved_record_id = null where id = any(%s::text[])",
        (ids,),
        context="clear approved_record_id",
    )
    # 3. approved_records.candidate_id -> candidates.id: delete before candidates.
    removed["approved_records"] = safe_execute(
        conn,
        "delete from approved_records where candidate_id = any(%s::text[])",
        (ids,),
        context="approved_records",
    )
    # 4. candidate_notices references both candidates and notices: delete before either.
    removed["candidate_notices"] = safe_execute(
        conn,
        "delete from candidate_notices where candidate_id = any(%s::text[])",
        (ids,),
        context="candidate_notices",
    )
    # 5. candidates.primary_notice_id -> notices.id and candidates.detected_run ->
    #    fetch_runs.id: delete candidates before either of those.
    removed["candidates"] = safe_execute(
        conn, "delete from candidates where id = any(%s::text[])", (ids,), context="candidates"
    )
    # 5b. scores.notice_id and translations.notice_id both -> notices.id.
    # tests/unit/test_stager.py::seeded and tests/unit/test_rescore.py::seeded
    # both write one of these per notice they build (that is how a real, live
    # candidate id ends up carrying a fixture-source notice in the first place -
    # `stager.run()`/`rescore()` read `scores`/`translations` to build it), so
    # notices from a fixture source cannot be deleted before these are, or every
    # delete below it fails on this FK and the source is left behind forever.
    # Neither table is itself a marker: only notices already selected by the
    # source-id marker feed this query.
    removed["scores"] = safe_execute(
        conn,
        "delete from scores where notice_id in (select id from notices where source_id = any(%s::text[]))",
        (sources,),
        context="scores",
    )
    removed["translations"] = safe_execute(
        conn,
        "delete from translations where notice_id in (select id from notices where source_id = any(%s::text[]))",
        (sources,),
        context="translations",
    )
    # 6. notices.source_id -> sources.id and notices.content_hash -> notices_raw:
    #    delete notices before notices_raw and before sources.
    removed["notices"] = safe_execute(
        conn, "delete from notices where source_id = any(%s::text[])", (sources,), context="notices"
    )
    # 7. notices_raw.source_id -> sources.id: delete before sources.
    removed["notices_raw"] = safe_execute(
        conn, "delete from notices_raw where source_id = any(%s::text[])", (sources,), context="notices_raw"
    )
    # 8 and 9. source_health and fetch_runs both reference sources.id (and
    #    fetch_runs is candidates.detected_run's target, already clear by step 5).
    removed["source_health"] = safe_execute(
        conn, "delete from source_health where source_id = any(%s::text[])", (sources,), context="source_health"
    )
    removed["fetch_runs"] = safe_execute(
        conn, "delete from fetch_runs where source_id = any(%s::text[])", (sources,), context="fetch_runs"
    )
    # 10. sources last: nothing above still references it.
    removed["sources"] = safe_execute(
        conn, "delete from sources where id = any(%s::text[])", (sources,), context="sources"
    )

    # 11. export_batches: a batch a killed export test never got to tear down. Named by
    #     operator prefix, never by anything the candidate/source markers above found, and
    #     only when no approved_records row still names it (the foreign key would refuse
    #     the delete anyway, but checking here means a batch a live record still needs is
    #     never even attempted).
    removed["export_batches"] = safe_execute(
        conn,
        """
        delete from export_batches eb
        where (eb.operator like any(%(patterns)s::text[]))
          and not exists (select 1 from approved_records ar where ar.export_batch = eb.batch_id)
        """,
        {"patterns": list(TEST_OPERATOR_PATTERNS)},
        context="export_batches",
    )

    log.info("fixture_sweep", candidates_found=len(ids), sources_found=len(sources), **removed)
    return removed
