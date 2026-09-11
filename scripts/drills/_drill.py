"""Shared scaffolding for the six failure drills, and nothing that belongs to only one of them.

A drill is not a test. `make test` proves a property inside a process pytest controls and
says nothing to a person watching a real system fail. A drill is run by hand against the
live database, prints what it saw, and says PASS or FAIL against the outcome BUILD_ORDER
step 11 documents. The six of them together are the evidence that the failure behaviour
this design claims is the failure behaviour it has.

Four decisions live here because all six drills share them.

**The fixture rows are created and removed on the owner connection.** Neither runtime
role holds delete on any table - `migrations/002_roles.sql` grants select, insert and
update and no delete anywhere, because the audit log and the approved records are append
only by grant - so a drill that commits a fixture row cannot remove it as the pipeline or
as the reviewer. `tests/roles/test_roles.py` and `tests/review/conftest.py` use the owner
for their fixtures for the same reason. The owner is not a runtime path: nothing under
`monitor/` or `review/` can reach `DATABASE_URL_OWNER`, which is what rule 11 is about,
and a drill is no more a runtime path than a migration is.

**`try/finally`, never `try/except` around the thing under test.** Rule 3 forbids
wrapping a connector or the model client and no drill does: each one lets the failure
happen and then reads the system. What the `finally` blocks hold is teardown. A drill that
leaves a source unhealthy, two seeded `model_calls` rows or an export batch behind is
damage rather than a drill, and teardown that runs only when the drill passes is teardown
that runs on the days it matters least.

**Exit codes are three, not two.** 0 is PASS. 1 is FAIL, which is a finding about the
system and not about the drill. 2 is "this drill could not run": no database, an empty
queue, another process holding the state the drill needs. A missing precondition reported
as a PASS is the one outcome worse than a FAIL, so `run` turns `DrillCannotRun` into 2 and
that is the only `except` in this file.

**Output is for a person at a terminal**, so it is `print` and not `structlog`. The
pipeline's own log lines are evidence the drills read, not the voice they speak in.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg

PASSED = 0
FAILED = 1
CANNOT_RUN = 2

# The model credential drill 1 and drill 3 send. Deliberately not key-shaped:
# `.gitleaks.toml` matches `sk-ant-` followed by sixteen characters, and a drill that
# cannot be committed because it carries something that looks like a key is a drill
# nobody runs (rule 20). The API rejects it all the same - verified against
# api.anthropic.com on 2026-09-11, HTTP 401 `authentication_error: invalid x-api-key`.
INVALID_CREDENTIAL = "drill-deliberately-invalid-credential"


class DrillCannotRun(Exception):
    """A precondition is missing, so the drill proved nothing either way (exit 2)."""


def url(env_var: str) -> str:
    value = os.environ.get(env_var)
    if not value:
        raise DrillCannotRun(f"{env_var} is not set; run  set -a && . ./.env && set +a  first")
    return value


@contextmanager
def connection(env_var: str, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """A connection as whichever role that environment variable names."""
    with psycopg.connect(url(env_var), autocommit=autocommit) as conn:
        yield conn


@contextmanager
def owner() -> Iterator[psycopg.Connection]:
    """The fixture connection. Autocommit, because a fixture row has to be visible to
    the pipeline, the reviewer and any subprocess the drill starts.

    A teardown that waits on a row lock is a bug in the drill and not something to sit
    through, so it fails in five seconds and says so.
    """
    with psycopg.connect(url("DATABASE_URL_OWNER"), autocommit=True) as conn:
        conn.execute("set lock_timeout = '5s'")
        yield conn


class Drill:
    """One drill's banner, its checks and its verdict.

    `check` records and prints; it does not stop the drill. Which checks are safe to
    continue past is the drill's own judgement, and most of them are: a drill that stops
    at the first surprise tells a person less than one that reports all six lines.
    """

    def __init__(self, number: int, title: str, proves: str, expected: str) -> None:
        self.number = number
        self.title = title
        self.results: list[tuple[bool, str]] = []

        print(f"\ndrill {number}: {title}")
        print(f"  proves    {proves}")
        print(f"  expected  {expected}")
        print("  " + "-" * 76)

    def check(self, claim: str, ok: bool, detail: str = "") -> bool:
        self.results.append((bool(ok), claim))
        print(f"  [{'ok' if ok else 'NO'}]  {claim}")
        if detail:
            print(f"        {detail}")
        return bool(ok)

    def note(self, text: str) -> None:
        print(f"  note  {text}")

    def show(self, text: str) -> None:
        """Something the system said, quoted back verbatim as evidence."""
        for line in text.splitlines():
            print(f"        | {line}")

    def verdict(self) -> int:
        failed = [claim for ok, claim in self.results if not ok]
        print("  " + "-" * 76)
        if not self.results:
            print(f"  FAIL  drill {self.number}: {self.title} - the drill checked nothing")
            return FAILED
        if failed:
            print(f"  FAIL  drill {self.number}: {self.title}")
            for claim in failed:
                print(f"        not true: {claim}")
            print("        This is a finding about the system, not about the drill.")
            return FAILED
        print(f"  PASS  drill {self.number}: {self.title} ({len(self.results)} checks)")
        return PASSED


def run(drill_main: Callable[[], int]) -> int:
    """Run one drill, turning a missing precondition into exit 2 rather than a traceback."""
    try:
        return drill_main()
    except DrillCannotRun as why:
        print(f"\n  CANNOT RUN: {why}")
        print("  Nothing was proved either way, and nothing was changed.")
        return CANNOT_RUN


# --- the shared fixture -------------------------------------------------------
#
# Drill 4 needs a candidate at pending_review to refuse a decision on; drill 6 needs
# approved records to export. Both need the same thing underneath, and it is the shape
# `tests/review/conftest.py` builds for the same reason: one national source and one
# donor source per candidate, because appendix E's Partners Involved and Funding Source
# both read the cluster and a one-source fixture leaves those columns empty.

STAGED_TITLE = "Supply and implementation of an integrated financial management system"
STAGED_SUMMARY = "The ministry is replacing its IFMIS."
MATCHED_FUNCTION = "budget_execution"


@dataclass
class Fixture:
    """What a drill staged, and what it produced that has to be removed with it.

    `batch_ids` is empty until a drill adds to it. Teardown deletes the approved records
    before the batch rows they point at, so an export batch a drill produced comes out in
    the one place that knows the order the foreign key requires.
    """

    candidate_ids: list[str]
    source_ids: list[str]
    notice_ids: list[str]
    batch_ids: set[str] = field(default_factory=set)


@contextmanager
def staged_candidates(owner_conn: psycopg.Connection, count: int) -> Iterator[Fixture]:
    """`count` candidates at pending_review, each clustered from a national and a donor
    notice, removed on the way out whether the drill passed or failed."""
    marker = uuid.uuid4().hex[:8]
    national = f"drill-nat-{marker}"
    donor = f"drill-wb-{marker}"
    fixture = Fixture(candidate_ids=[], source_ids=[national, donor], notice_ids=[])

    try:
        for source_id, name, admin_level in (
            (national, "Ghana Public Procurement Authority (drill fixture)", "national"),
            (donor, "World Bank procurement notices (drill fixture)", "donor"),
        ):
            owner_conn.execute(
                """
                insert into sources (id, name, country, admin_level, language, stream, access_type,
                                     connector_class, wave, tos_status, enabled, expected_min,
                                     expected_max, max_consecutive_failures)
                values (%s, %s, 'GH', %s, 'en', 'feed', 'api', 'FeedConnector', 1, 'cleared',
                        false, 1, 50, 3)
                """,
                (source_id, name, admin_level),
            )

        for index in range(count):
            candidate_id = f"C{(int(marker, 16) + index) % 1_000_000:06d}"
            cluster: list[str] = []

            for stream_index, source_id in enumerate((national, donor)):
                content_hash = f"sha256:{marker}-{index}-{stream_index}"
                owner_conn.execute(
                    """
                    insert into notices_raw (content_hash, source_id, url, storage_path, mime)
                    values (%s, %s, 'https://example.invalid/notice', 'raw/drill.json', 'application/json')
                    """,
                    (content_hash, source_id),
                )
                notice_id = owner_conn.execute(
                    """
                    insert into notices (content_hash, source_id, url, title, buyer, country,
                                         admin_level, language, status)
                    values (%s, %s, 'https://example.invalid/notice', %s, 'Ministry of Finance',
                            'GH', 'national', 'en', 'scored')
                    returning id
                    """,
                    (content_hash, source_id, STAGED_TITLE),
                ).fetchone()[0]
                cluster.append(notice_id)
                fixture.notice_ids.append(notice_id)

            owner_conn.execute(
                """
                insert into candidates (id, primary_notice_id, score, status, region, language,
                                        title_en, buyer, country, admin_level, summary_en,
                                        matched_functions, system_names, procurement_type,
                                        estimated_value_usd, eligibility_flags, deadline_at)
                values (%s, %s, 78, 'pending_review', 'West Africa', 'en', %s,
                        'Ministry of Finance', 'GH', 'national', %s, %s::jsonb, %s, 'system',
                        4200000, %s, '2026-11-30T17:00:00Z')
                """,
                (
                    candidate_id,
                    cluster[0],
                    STAGED_TITLE,
                    STAGED_SUMMARY,
                    f'[{{"function_id": "{MATCHED_FUNCTION}", "evidence": "financial management system"}}]',
                    ["IFMIS", "GIFMIS"],
                    [],
                ),
            )
            for notice_id in cluster:
                owner_conn.execute(
                    """
                    insert into candidate_notices (candidate_id, notice_id, match_method, match_score)
                    values (%s, %s, 'content_hash', 100)
                    """,
                    (candidate_id, notice_id),
                )
            fixture.candidate_ids.append(candidate_id)

        yield fixture
    finally:
        remove(owner_conn, fixture)


def remove(owner_conn: psycopg.Connection, fixture: Fixture) -> None:
    """Every row the fixture made, in foreign-key order, children first."""
    candidates = fixture.candidate_ids
    records = [
        row[0]
        for row in owner_conn.execute(
            "select id from approved_records where candidate_id = any(%s::text[])", (candidates,)
        ).fetchall()
    ]

    owner_conn.execute("delete from events where entity_id = any(%s::text[])", (candidates + records,))
    owner_conn.execute("update candidates set approved_record_id = null where id = any(%s::text[])", (candidates,))
    owner_conn.execute("delete from approved_records where id = any(%s::text[])", (records,))
    owner_conn.execute("delete from export_batches where batch_id = any(%s::text[])", (sorted(fixture.batch_ids),))
    owner_conn.execute("delete from candidate_notices where candidate_id = any(%s::text[])", (candidates,))
    owner_conn.execute("delete from candidates where id = any(%s::text[])", (candidates,))
    owner_conn.execute("delete from notices where id = any(%s::uuid[])", (fixture.notice_ids,))
    owner_conn.execute("delete from notices_raw where source_id = any(%s::text[])", (fixture.source_ids,))
    owner_conn.execute("delete from sources where id = any(%s::text[])", (fixture.source_ids,))


# --- the score stage's fixture ------------------------------------------------
#
# Drill 1 and drill 3 both need the score stage to have exactly one call to attempt.
# Reading whatever happens to be at `filtered_in` makes the drill depend on when it is
# run - the queue is empty on a quiet morning and full after a fetch - so each of them
# seeds one notice and removes it. `fetched_at` is set well back so the scorer's
# `order by fetched_at` reaches this notice first and the drill is watching its own
# fixture rather than a real one.

NOTICE_TITLE = "Supply and implementation of an integrated financial management information system"
NOTICE_BODY = (
    "The Ministry of Finance invites bids for the supply, configuration and implementation "
    "of an integrated financial management information system covering budget preparation, "
    "budget execution, commitment control and treasury management, replacing the existing "
    "GIFMIS installation. This notice is a drill fixture and names no real procurement."
)
NOTICE_FETCHED_AT = "2026-01-01T00:00:00Z"


@contextmanager
def filtered_in_notice(owner_conn: psycopg.Connection) -> Iterator[str]:
    """One notice at `filtered_in` with its source and raw row, removed on the way out.

    Teardown deletes any `scores` row as well. Nothing in either drill writes one, but a
    scheduled run that overlapped the drill would have, and a fixture that cannot be
    removed because something else touched it is worse than a fixture that says so.
    """
    marker = uuid.uuid4().hex[:8]
    source_id = f"drill-score-{marker}"
    content_hash = f"sha256:{marker}"
    notice_id = None

    try:
        owner_conn.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, 'Score stage drill fixture', 'GH', 'national', 'en', 'feed', 'api',
                    'FeedConnector', 1, 'cleared', false, 1, 50, 3)
            """,
            (source_id,),
        )
        owner_conn.execute(
            """
            insert into notices_raw (content_hash, source_id, url, storage_path, mime)
            values (%s, %s, 'https://example.invalid/notice', 'raw/drill.json', 'application/json')
            """,
            (content_hash, source_id),
        )
        notice_id = owner_conn.execute(
            """
            insert into notices (content_hash, source_id, url, title, buyer, country, admin_level,
                                 published_at, language, language_confidence, cpv_codes, body,
                                 filter_result, status, fetched_at)
            values (%s, %s, 'https://example.invalid/notice', %s, 'Ministry of Finance', 'GH',
                    'national', now(), 'en', 1.0, %s, %s, 'lexicon: drill fixture',
                    'filtered_in', %s)
            returning id
            """,
            (content_hash, source_id, NOTICE_TITLE, ["72212440"], NOTICE_BODY, NOTICE_FETCHED_AT),
        ).fetchone()[0]

        yield str(notice_id)
    finally:
        # Guarded because an insert that failed halfway leaves the later rows unmade, and
        # a teardown that raises on its own first statement removes nothing.
        if notice_id is not None:
            owner_conn.execute("delete from events where entity_id = %s", (str(notice_id),))
            owner_conn.execute("delete from scores where notice_id = %s", (notice_id,))
            owner_conn.execute("delete from translations where notice_id = %s", (notice_id,))
            owner_conn.execute("delete from notices where id = %s", (notice_id,))
        owner_conn.execute("delete from notices_raw where source_id = %s", (source_id,))
        owner_conn.execute("delete from sources where id = %s", (source_id,))


# --- keeping other work out of the way ----------------------------------------
#
# Drill 1 and drill 3 read counts a scheduled `monitor run` moves under them: model calls
# today, notices by status, scores. A drill that reported FAIL because something else was
# working would be a finding about the drill, so both of them check for a run in flight
# first and check again afterwards that the counts they read are theirs to read. Exit 2
# says the claim was not tested; it never says it was tested and false.


def require_quiet_pipeline(conn: psycopg.Connection) -> None:
    """Refuse to start while another process holds a pipeline connection."""
    pids = [
        row[0]
        for row in conn.execute(
            """
            select pid from pg_stat_activity
            where datname = current_database()
              and usename = 'monitor_pipeline'
              and pid <> pg_backend_pid()
            order by pid
            """
        ).fetchall()
    ]
    if pids:
        raise DrillCannotRun(
            f"another pipeline run is in flight: {len(pids)} monitor_pipeline connection(s) open "
            f"(backend pids {', '.join(str(pid) for pid in pids)}). Wait for it to finish, or stop "
            "the hourly timer, then run this drill. It reads counts a concurrent run moves."
        )


def require_no_concurrent_calls(before: int, after: int) -> None:
    """Refuse to claim 'nothing was billed' if someone else billed something meanwhile."""
    if after != before:
        raise DrillCannotRun(
            f"{after - before} model_calls row(s) appeared while this drill ran, so the calls the "
            "run did or did not make cannot be told apart from another run's. The checks above "
            "still stand; this one is untested. Run the drills on a quiet system."
        )
