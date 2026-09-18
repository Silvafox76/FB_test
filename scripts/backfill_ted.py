"""Fetch TED for a range of publication dates the scheduled pass never reached.

The scheduled pass reads the trailing two days (`LOOKBACK_DAYS` in
`monitor/connectors/ted.py`), which is right for a pipeline that runs every day and
wrong for one that has not: the first TED pass ran on 2026-09-11, so a notice
published on 24 August, such as TED 584175-2026, matched every clause of the
registry's query and was never inside any window (decision 59). This script walks
an explicit range of days, one day per request set, through the same connector,
the same query, the same mapper and the same insert as the scheduled pass, so a
backfilled notice is indistinguishable in the database from a fetched one.

    uv run python scripts/backfill_ted.py --from 2026-08-24 --to 2026-09-08 --dry-run
    uv run python scripts/backfill_ted.py --from 2026-08-24 --to 2026-09-08

Three things it deliberately does not do.

**It writes no `fetch_runs` row and touches no health.** `monitor/schedule.py`
decides whether a source is due from its last attempt, and `source_health` keeps a
median of items per run; a sixteen-day backfill recorded as a run would mark TED as
read today and skew the median for a week. This is a person's one-off, not a pass.

**It does not filter, score or stage.** Those stages read `notices` by status and
run on their own verbs; after this script, `make filter score stage` (or the next
scheduled `monitor run`) takes the backfilled notices through the same path as any
other, under the current prompt version.

**It does not retry and does not catch.** A day that fails raises, its transaction
rolls back, the script exits non-zero and says which day; rerunning with `--from`
set to that day continues, because every notice already held is seen-not-new
(rule 2, rule 3, and the content-hash idempotency `make fetch S=ted` twice proves).

On politeness (rule 21): this is a burst, not a schedule. The days are walked
sequentially on one connection with the identified user agent, each day the
connector's own page size and ceiling, and `PAUSE_SECONDS` between days, so a
sixteen-day range is sixteen day-sized reads a couple of seconds apart, not
sixteen days of passes. Run it once; it is idempotent.

What records that it happened: every notice it inserts carries a `notices_raw.fetched_at`
after its `published_at` by weeks rather than by a day, this script's own
`ted_backfill_day` log lines, and the RUNBOOK entry under "Known gaps". Nothing in
`fetch_runs`, deliberately, for the reason above.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import structlog

from monitor import db
from monitor.connectors.base import TIMEOUT_SECONDS, user_agent
from monitor.connectors.ted import TedConnector
from monitor.fetch import _store_notices, build_connector
from monitor.registry import load_sources

log = structlog.get_logger(__name__)

SOURCE_ID = "ted"
# Between days, not between pages: the connector paces its own pages.
PAUSE_SECONDS = 2.0


@dataclass(frozen=True)
class DayResult:
    day: date
    fetched: int
    seen: int
    new: int


def days_between(start: date, end: date) -> list[date]:
    """Every day from start to end inclusive; raises on a backwards range."""
    if end < start:
        raise ValueError(f"--to {end} is before --from {start}")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def backfill_day(conn, source, connector: TedConnector, mapper, client: httpx.Client, day: date) -> DayResult:
    """One day's window through the real connector, mapper and insert. Not committed here.

    Calls `fetch_raw` directly rather than `fetch()`, so a failure reaches the top as
    the raw httpx or ValueError rather than a ConnectorError naming the source; the
    day is in the traceback's own log line and there is one source, so nothing is lost.
    """
    raw_notices = connector.fetch_raw(client, since=day, until=day)
    seen = new = 0
    for raw in raw_notices:
        mapped, inserted = _store_notices(conn, source, raw, mapper)
        seen += mapped
        new += inserted
    result = DayResult(day=day, fetched=len(raw_notices), seen=seen, new=new)
    log.info("ted_backfill_day", day=day.isoformat(), fetched=result.fetched, seen=seen, new=new)
    return result


def backfill(conn, start: date, end: date, *, dry_run: bool) -> list[DayResult]:
    source = next(s for s in load_sources() if s.id == SOURCE_ID)
    connector, mapper = build_connector(source)
    results: list[DayResult] = []

    with httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": user_agent()}, follow_redirects=True) as client:
        for index, day in enumerate(days_between(start, end)):
            if index:
                time.sleep(PAUSE_SECONDS)
            result = backfill_day(conn, source, connector, mapper, client, day)
            conn.rollback() if dry_run else conn.commit()
            results.append(result)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True, help="first day, YYYY-MM-DD")
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True, help="last day, inclusive")
    parser.add_argument("--dry-run", action="store_true", help="fetch and map every day, commit nothing")
    args = parser.parse_args(argv)

    if args.end > date.today():
        parser.error(f"--to {args.end} is in the future; the scheduled pass covers today")

    conn = db.connect("pipeline")
    try:
        results = backfill(conn, args.start, args.end, dry_run=args.dry_run)
    finally:
        conn.close()

    heading = "DRY RUN, nothing committed" if args.dry_run else "committed"
    print(f"--- backfill_ted ({heading}) {args.start} to {args.end} ---")
    for r in results:
        print(f"{r.day}  fetched={r.fetched:>4}  seen={r.seen:>4}  new={r.new:>4}")
    print(f"total      fetched={sum(r.fetched for r in results):>4}  new={sum(r.new for r in results):>4}")
    if not args.dry_run:
        print("Next: make filter && make score && make stage, or wait for the next scheduled monitor run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
