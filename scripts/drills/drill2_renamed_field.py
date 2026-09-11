"""Drill 2: a source renames a field. That source goes unhealthy; the others do not notice.

    uv run python scripts/drills/drill2_renamed_field.py

**What it proves.** Rule 4 at its sharpest edge and the isolation `monitor/fetch.py` was
built for. A renamed field is a real change in a source, so the parser raises rather than
reading around it (`monitor/connectors/worldbank.py` checks the fields it needs are
present); `fetch_source` turns that into one failed `fetch_runs` row and one source's
health, and no other source's row moves. Rule 2 as well: three failed runs are three
requests, not three requests with retries behind them.

**How the failure is injected.** The committed fixture,
`tests/contract/fixtures/worldbank.json`, is copied into a temporary directory and one
required field is renamed in the copy - `contact_organization`, which the mapper reads on
all 100 recorded rows, becomes `buyer_organization`, the same rename
`tests/contract/test_worldbank.py` makes in memory. The copy is served over loopback and
the registry's own source is used with `api_url` pointed at it, so the whole path runs:
the real `httpx` client, the real parser, the real `ConnectorError`, the real health
transition. The committed fixture is never written to.

**Why a loopback server is not an inbound network path.** Rule 17 forbids a listener in
the deployed system: no webhook receiver, no public endpoint, the review app on localhost
or the SSM tunnel. This binds to 127.0.0.1 on a kernel-assigned port, serves one file to
one client in this process, and is closed before the drill prints its verdict. It is the
same loopback the review app binds to, for two seconds, and there is no way to reach it
from off the host. It is worth saying out loud because the alternative - injecting the
payload by monkeypatching the connector - would test the drill's own plumbing instead of
the connector's.

**The ladder, and why three runs.** `monitor/health/source_health.py` moves a source to
`watch` at two consecutive failures and to `unhealthy` at the source's own
`max_consecutive_failures`, which is 3 for the World Bank. One failure is not yet a sick
source and the drill would be lying if it claimed otherwise, so it runs the broken fetch
until the state the runbook promises actually arrives, and prints the ladder.

Note the rule that module carries: zero yield is counted on `items_seen` and not on
`items_new`. This drill exercises the other counter - a parse failure is a *failed* run,
not a zero-yield one - and the two are separate on purpose.

**What it changes and how it puts it back.** It writes three `fetch_runs` rows and moves
`source_health` for one source. On the way out, whether it passed or failed, it deletes
exactly the `fetch_runs` rows it created and restores the source's health row to the
values it read at the start, then reads it back and checks. Nothing else is touched: a
failed run rolls back before it records itself, so no notice is stored.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple

from _drill import Drill, DrillCannotRun, connection, owner, run

from monitor.fetch import fetch_source
from monitor.health.source_health import WATCH_THRESHOLD
from monitor.models import Source
from monitor.registry.load import load_sources

REPO = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURE = REPO / "tests" / "contract" / "fixtures" / "worldbank.json"

SOURCE_ID = "worldbank"
# Required by the parser on all 100 recorded rows, so renaming it is a source change the
# connector must refuse rather than read around.
FIELD = "contact_organization"
RENAMED_TO = "buyer_organization"

HEALTH = """
    select source_id, last_success_at, consecutive_failures, median_items,
           last_zero_yield_at, zero_yield_runs, state
    from source_health
    order by source_id
"""
RESTORE_HEALTH = """
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
"""


class Attempt(NamedTuple):
    """What one broken run did, read back off the health row it wrote."""

    number: int
    error: str
    failures: int
    state: str


class FixtureHandler(BaseHTTPRequestHandler):
    """Answers any GET with the mutated fixture. The query string is ignored: what the
    connector asks for is tested by the contract test, and what this drill needs is the
    one page it would have got back."""

    payload = b"{}"

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        self.wfile.write(self.payload)

    def log_message(self, fmt: str, *args: object) -> None:
        """Silent. The drill says what happened; the fixture server has nothing to add."""


def keep_loopback_direct() -> None:
    """Make sure the fixture request goes to loopback rather than to an egress proxy.

    A host with HTTP_PROXY set and 127.0.0.1 missing from NO_PROXY would send the
    connector's request to the proxy, which would fail for a reason that has nothing to do
    with the renamed field. Adding loopback to the no-proxy list does not change where
    anything else goes.
    """
    for name in ("NO_PROXY", "no_proxy"):
        current = [part for part in os.environ.get(name, "").split(",") if part]
        for host in ("127.0.0.1", "localhost"):
            if host not in current:
                current.append(host)
        os.environ[name] = ",".join(current)


def mutated_fixture(directory: Path) -> Path:
    """The committed fixture, copied, with one required field renamed in the copy."""
    copy = directory / COMMITTED_FIXTURE.name
    shutil.copyfile(COMMITTED_FIXTURE, copy)

    document = json.loads(copy.read_text(encoding="utf-8"))
    for row in document["procnotices"]:
        if FIELD in row:
            row[RENAMED_TO] = row.pop(FIELD)
    copy.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return copy


def registry_source() -> Source:
    for source in load_sources():
        if source.id == SOURCE_ID:
            return source
    raise DrillCannotRun(f"no source {SOURCE_ID!r} in sources/; the registry is what this drill breaks")


def health_rows(conn) -> dict[str, tuple]:
    return {row[0]: tuple(row) for row in conn.execute(HEALTH).fetchall()}


def run_ids(conn) -> set[str]:
    rows = conn.execute("select id from fetch_runs where source_id = %s", (SOURCE_ID,)).fetchall()
    return {str(row[0]) for row in rows}


def main() -> int:
    source = registry_source()
    drill = Drill(
        2,
        f"a renamed field puts {SOURCE_ID} unhealthy and leaves every other source alone",
        "rule 4 (a parse failure is a failure, not an empty success) and per-source isolation",
        f"{source.max_consecutive_failures} failed runs -> state unhealthy; no other health row moves",
    )
    keep_loopback_direct()

    with owner() as owner_conn, connection("DATABASE_URL_READONLY") as reader:
        health_before = health_rows(reader)
        runs_before = run_ids(reader)
        notices_before = reader.execute("select count(*) from notices where source_id = %s", (SOURCE_ID,)).fetchone()[0]
        other_runs_before = dict(
            reader.execute("select source_id, count(*) from fetch_runs group by source_id").fetchall()
        )

        directory = Path(tempfile.mkdtemp(prefix="drill2-"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        try:
            copy = mutated_fixture(directory)
            FixtureHandler.payload = copy.read_bytes()
            threading.Thread(target=server.serve_forever, daemon=True).start()
            served = f"http://127.0.0.1:{server.server_port}/procnotices"
            drill.note(f"fixture copy at {copy}, {FIELD} renamed to {RENAMED_TO} in the copy only")
            drill.note(f"serving it at {served}; the committed fixture is untouched")

            # Revalidated rather than mutated in place: the drill crosses a module boundary
            # with this model, so it goes through Source like everything else (rule 4).
            broken = Source.model_validate(source.model_dump() | {"api_url": served})

            ladder: list[Attempt] = []
            with connection("DATABASE_URL_PIPELINE") as pipeline:
                for number in range(1, source.max_consecutive_failures + 1):
                    result = fetch_source(pipeline, broken)
                    row = health_rows(pipeline)[SOURCE_ID]
                    ladder.append(Attempt(number=number, error=result.error, failures=row[2], state=row[6]))

            for attempt in ladder:
                drill.note(f"run {attempt.number}: consecutive_failures {attempt.failures}, state {attempt.state}")

            drill.check(
                "every run failed, and failed naming the field that moved",
                all(FIELD in attempt.error for attempt in ladder),
                ladder[0].error,
            )
            drill.check(
                "the failure counter counted every run and nothing else",
                [attempt.failures for attempt in ladder] == list(range(1, len(ladder) + 1)),
                f"consecutive_failures {[attempt.failures for attempt in ladder]}",
            )
            expected_states = [
                "unhealthy"
                if attempt.number >= source.max_consecutive_failures
                else ("watch" if attempt.number >= WATCH_THRESHOLD else "healthy")
                for attempt in ladder
            ]
            drill.check(
                f"the state reached unhealthy at {source.max_consecutive_failures} failures, by way of watch",
                [attempt.state for attempt in ladder] == expected_states,
                f"{[attempt.state for attempt in ladder]} (expected {expected_states})",
            )

            with connection("DATABASE_URL_READONLY") as reader2:
                health_after = health_rows(reader2)
                notices_after = reader2.execute(
                    "select count(*) from notices where source_id = %s", (SOURCE_ID,)
                ).fetchone()[0]
                other_runs_after = dict(
                    reader2.execute("select source_id, count(*) from fetch_runs group by source_id").fetchall()
                )

            others_before = {key: value for key, value in health_before.items() if key != SOURCE_ID}
            others_after = {key: value for key, value in health_after.items() if key != SOURCE_ID}
            drill.check(
                "every other source's health row is exactly as it was",
                others_before == others_after,
                ", ".join(f"{key} {value[6]}" for key, value in sorted(others_after.items())) or "no other source",
            )
            drill.check(
                "no other source ran at all: one broken source does not touch the rest",
                {key: value for key, value in other_runs_after.items() if key != SOURCE_ID}
                == {key: value for key, value in other_runs_before.items() if key != SOURCE_ID},
                f"fetch_runs per source now {other_runs_after}",
            )
            drill.check(
                "the failed runs stored no notice",
                notices_after == notices_before,
                f"{SOURCE_ID} notices {notices_after}, unchanged",
            )
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(directory, ignore_errors=True)

            owner_conn.execute(
                "delete from fetch_runs where source_id = %s and id::text <> all(%s::text[])",
                (SOURCE_ID, sorted(runs_before)),
            )
            was = health_before.get(SOURCE_ID)
            if was is None:
                owner_conn.execute("delete from source_health where source_id = %s", (SOURCE_ID,))
            else:
                owner_conn.execute(RESTORE_HEALTH, was)

            restored = health_rows(owner_conn).get(SOURCE_ID)
            drill.check(
                "the drill put the source back as it found it",
                restored == was and run_ids(owner_conn) == runs_before,
                f"health row {restored}",
            )

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
