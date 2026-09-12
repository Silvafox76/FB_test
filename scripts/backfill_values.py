"""One-off backfill for the published-value columns migrations 012 and 013 added.

Commit 8a29fe7 taught the pipeline to carry a notice's contract value in its own
currency (`notices.estimated_value`, `notices.value_currency`) and to derive a USD
figure for a candidate at staging time from a stored rate
(`candidates.estimated_value`, `value_currency`, `estimated_value_usd`, `value_rate`,
`value_rate_date`). Every notice normalised before that change carries null on both
notice columns, because the mapper that reads a source's value field did not exist
yet when they were stored - and every candidate built from one of those notices
carries null too. The raw payload for each notice is still on disk
(`storage/<source_id>/<content_hash>.json`), so this script re-runs each one through
its source's own mapper (`monitor.fetch.CONNECTORS`) and fills the gap.

Two passes, one transaction each:

  1. notices  - read the stored payload, map it, write `estimated_value` and
     `value_currency` from what the mapper's `Notice` carries.
  2. candidates - read each candidate's primary notice (as pass 1 left it, whether
     or not pass 1's own writes were committed - see the note on `--dry-run` below)
     and convert with exactly the stager's own code: `monitor.fx.config.load()`,
     `monitor.fx.store.latest()`, `monitor.fx.convert.convert()`. No arithmetic is
     reimplemented here.

Rule 9 (the original text is the record) is why this reads the stored payload
rather than trusting anything already in Postgres, and rule 1 (no fallbacks) is why
a payload that is missing or a mapper that raises is logged and the row is skipped
rather than guessed at - a run's counts at the end say exactly how many of each.

Connects only as `monitor_pipeline` (rule 11: never owner, never review) and never
references `approved_records` or `events` (rule 12): every column this script
writes is a pre-review, derived field, upstream of anything a reviewer's decision
has touched.

    uv run python scripts/backfill_values.py --dry-run
    uv run python scripts/backfill_values.py
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import structlog

from monitor import db
from monitor.fetch import CONNECTORS
from monitor.fx.config import load as load_fx
from monitor.fx.convert import convert
from monitor.fx.store import latest

log = structlog.get_logger(__name__)

REPO = Path(__file__).resolve().parent.parent
DEFAULT_STORAGE = REPO / "storage"

SELECT_NOTICES = """
    select id, source_id, content_hash, estimated_value, value_currency
    from notices
    order by id
"""

# The `is distinct from` guard is what makes a second run a no-op: a row already
# holding the value this run would write is left untouched, so `updated` counts
# actual changes rather than every row this pass looked at.
UPDATE_NOTICE = """
    update notices
       set estimated_value = %(value)s, value_currency = %(currency)s
     where id = %(id)s
       and (estimated_value is distinct from %(value)s or value_currency is distinct from %(currency)s)
"""

SELECT_CANDIDATES = """
    select id, primary_notice_id
    from candidates
    order by id
"""

UPDATE_CANDIDATE = """
    update candidates
       set estimated_value = %(value)s,
           value_currency = %(currency)s,
           estimated_value_usd = %(usd)s,
           value_rate = %(rate)s,
           value_rate_date = %(rate_date)s
     where id = %(id)s
       and (estimated_value is distinct from %(value)s
            or value_currency is distinct from %(currency)s
            or estimated_value_usd is distinct from %(usd)s
            or value_rate is distinct from %(rate)s
            or value_rate_date is distinct from %(rate_date)s)
"""


@dataclass(frozen=True)
class NoticeCounts:
    examined: int = 0
    updated: int = 0
    skipped_no_connector: int = 0
    skipped_no_payload: int = 0
    skipped_mapper_error: int = 0


@dataclass(frozen=True)
class CandidateCounts:
    examined: int = 0
    updated: int = 0
    converted: int = 0
    no_rate: int = 0
    no_value: int = 0


def payload_path(storage_root: Path, source_id: str, content_hash: str) -> Path:
    """Where `monitor.fetch.store_payload` wrote this notice's raw response."""
    return storage_root / source_id / f"{content_hash}.json"


def backfill_notices(conn, storage_root: Path, connectors: dict | None = None) -> tuple[NoticeCounts, dict]:
    """Pass 1. Returns counts and every notice id's post-pass (value, currency).

    That map is what pass 2 reads from, rather than candidates re-selecting
    `notices` from the database: under `--dry-run` this pass's writes are rolled
    back, and a fresh select would see the old null columns again. A notice this
    pass could not update (no connector, no payload, a raising mapper) keeps
    whatever `notices` already held, which is null on every row today but need not
    stay that way if this script is ever run again after a partial fix.
    """
    connectors = CONNECTORS if connectors is None else connectors
    examined = updated = skipped_no_connector = skipped_no_payload = skipped_mapper_error = 0
    notice_values: dict = {}

    for notice_id, source_id, content_hash, existing_value, existing_currency in conn.execute(
        SELECT_NOTICES
    ).fetchall():
        examined += 1
        bound = log.bind(source_id=source_id, content_hash=content_hash)
        notice_values[notice_id] = (existing_value, existing_currency)

        if source_id not in connectors:
            # The disabled sources named in monitor/fetch.py's CONNECTORS comment.
            # Not a failure of anything: there is no mapper to run them through.
            skipped_no_connector += 1
            bound.debug("backfill_no_connector")
            continue

        _connector_class, mapper = connectors[source_id]
        path = payload_path(storage_root, source_id, content_hash)
        if not path.exists():
            # Some sources were re-fetched under a different hash since this notice
            # was stored; the payload that produced it is simply gone. Logged and
            # skipped, not an error (rule 4 is about silent success, not this).
            bound.warning("backfill_no_payload", path=str(path))
            skipped_no_payload += 1
            continue

        document = json.loads(path.read_text(encoding="utf-8"))
        try:
            mapped = mapper(document)
        except Exception as cause:  # noqa: BLE001 - logged with source and hash, never hidden
            bound.error("backfill_mapper_error", error=repr(cause))
            skipped_mapper_error += 1
            continue

        value = mapped.notice.estimated_value
        currency = mapped.notice.value_currency
        notice_values[notice_id] = (value, currency)

        cursor = conn.execute(UPDATE_NOTICE, {"id": notice_id, "value": value, "currency": currency})
        if cursor.rowcount:
            updated += 1
            bound.debug("backfill_notice_updated", value=str(value), currency=currency)

    counts = NoticeCounts(examined, updated, skipped_no_connector, skipped_no_payload, skipped_mapper_error)
    log.info(
        "backfill_notices_pass",
        examined=counts.examined,
        updated=counts.updated,
        skipped_no_connector=counts.skipped_no_connector,
        skipped_no_payload=counts.skipped_no_payload,
        skipped_mapper_error=counts.skipped_mapper_error,
    )
    return counts, notice_values


def backfill_candidates(conn, notice_values: dict) -> CandidateCounts:
    """Pass 2. Carry each candidate's primary notice value, then convert it.

    The rate table is loaded once, the same way `monitor.stage.stager.run` loads
    it, and `monitor.fx.convert.convert` is the only place the arithmetic happens.
    `convert` itself raises when the rate table cannot cover the target currency at
    all (rule 4); it is not caught here, on purpose - a broken rate table is a
    reason to stop the whole pass, not a reason to keep guessing at candidates.
    """
    fx = load_fx()
    rates = latest(conn, fx)

    examined = updated = converted_count = no_rate = no_value = 0

    for candidate_id, primary_notice_id in conn.execute(SELECT_CANDIDATES).fetchall():
        examined += 1
        value, currency = notice_values.get(primary_notice_id, (None, None))

        usd = rate = rate_date = None
        if value is None or currency is None:
            # Mirrors monitor.stage.stager._converted: the notice states no value,
            # which is a different fact from "a value in a currency no rate covers".
            no_value += 1
        else:
            result = convert(value, currency, rates, fx)
            if result is None:
                no_rate += 1
            else:
                converted_count += 1
                usd, rate, rate_date = result.amount, result.rate, result.rate_date

        cursor = conn.execute(
            UPDATE_CANDIDATE,
            {
                "id": candidate_id,
                "value": value,
                "currency": currency,
                "usd": usd,
                "rate": rate,
                "rate_date": rate_date,
            },
        )
        if cursor.rowcount:
            updated += 1

    counts = CandidateCounts(examined, updated, converted_count, no_rate, no_value)
    log.info(
        "backfill_candidates_pass",
        examined=counts.examined,
        updated=counts.updated,
        converted=counts.converted,
        no_rate=counts.no_rate,
        no_value=counts.no_value,
    )
    return counts


def _report(notices: NoticeCounts, candidates: CandidateCounts, *, dry_run: bool) -> None:
    heading = "DRY RUN, nothing committed" if dry_run else "committed"
    print(f"--- backfill_values ({heading}) ---")
    print(
        "notices    "
        f"examined={notices.examined} updated={notices.updated} "
        f"skipped_no_connector={notices.skipped_no_connector} "
        f"skipped_no_payload={notices.skipped_no_payload} "
        f"skipped_mapper_error={notices.skipped_mapper_error}"
    )
    print(
        "candidates "
        f"examined={candidates.examined} updated={candidates.updated} "
        f"converted={candidates.converted} no_rate={candidates.no_rate} no_value={candidates.no_value}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="do everything but commit")
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=DEFAULT_STORAGE,
        help="root of the storage/<source_id>/<content_hash>.json layout (default: repo storage/)",
    )
    args = parser.parse_args(argv)

    conn = db.connect("pipeline")
    try:
        notice_counts, notice_values = backfill_notices(conn, args.storage_root)
        conn.rollback() if args.dry_run else conn.commit()

        candidate_counts = backfill_candidates(conn, notice_values)
        conn.rollback() if args.dry_run else conn.commit()
    finally:
        conn.close()

    _report(notice_counts, candidate_counts, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
