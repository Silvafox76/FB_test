"""Put a day of rates in `fx_rates`, and read the newest usable day back.

The table is append-only in practice. A rate for a (currency, date) already held
is not overwritten, because a figure already stamped on a candidate has to stay
reproducible: re-fetching a day and getting a revised number would silently change
what an earlier record means.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import structlog

from monitor.fx.config import FxConfig
from monitor.fx.convert import RateTable
from monitor.fx.nbu import Rate

log = structlog.get_logger(__name__)

INSERT = """
insert into fx_rates (currency, rate_date, uah_per_unit, source)
values (%s, %s, %s, %s)
on conflict (source, currency, rate_date) do nothing
"""

# `rate_date <= today`: the newest rate for a day that has HAPPENED. NBU publishes
# the next banking day's rate the afternoon before, and a row for a day that has
# not arrived is real data that is simply not yet the right day to stamp on a
# record. This is the definition of "newest", not a fallback from one day to
# another (rule 1): there is exactly one answer for any given `today`.
NEWEST_DATE = "select max(rate_date) from fx_rates where source = %s and rate_date <= %s"

DAY = "select currency, uah_per_unit from fx_rates where source = %s and rate_date = %s"


class StaleRatesError(Exception):
    """The newest rates held are too old to stamp on a record."""


def store(conn: psycopg.Connection, rates: list[Rate], config: FxConfig) -> int:
    """Insert a day. Returns how many rows were new; re-running a day inserts none."""
    written = 0
    with conn.transaction():
        for rate in rates:
            cursor = conn.execute(
                INSERT,
                (rate.currency, rate.rate_date, rate.uah_per_unit, config.publisher.id),
            )
            written += cursor.rowcount

    log.info(
        "fx.stored",
        publisher=config.publisher.id,
        rate_date=str(rates[0].rate_date),
        seen=len(rates),
        written=written,
    )
    return written


def latest(conn: psycopg.Connection, config: FxConfig, *, today: date | None = None) -> RateTable:
    """The newest day of rates held, refused if it is too old to be honest.

    `today` is an argument rather than read from the clock inside, so a test can
    place the staleness boundary exactly rather than depending on when it runs.

    Raises when there are no rates for any day up to `today`, and when the newest
    such day is older than `max_rate_age_days`. A row dated after `today` is never
    chosen, so the age can never be negative and a candidate is never stamped
    with a rate from a day that has not happened. Both are failure states rather than a reason to fall back
    to an older rate or to skip the conversion quietly (rules 1 and 4): a figure on
    a reviewer's screen carrying last month's rate is exactly the kind of number
    that looks current and is not.
    """
    today = today or datetime.now(UTC).date()
    publisher = config.publisher.id

    row = conn.execute(NEWEST_DATE, (publisher, today)).fetchone()
    newest = row[0] if row else None
    if newest is None:
        raise StaleRatesError(f"no rates held from {publisher}; run `monitor fx` before staging")

    age = (today - newest).days
    if age > config.max_rate_age_days:
        raise StaleRatesError(
            f"newest {publisher} rates are from {newest}, {age} days old, "
            f"past the {config.max_rate_age_days}-day tolerance in config/fx.yaml"
        )

    rates: dict[str, Decimal] = {currency: amount for currency, amount in conn.execute(DAY, (publisher, newest))}
    return RateTable(rate_date=newest, base=config.publisher.base_currency, uah_per_unit=rates)
