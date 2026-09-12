"""Fetch one day of rates from the publisher named in `config/fx.yaml`.

Acquire only. It parses what came back into `Rate` rows and hands them on; it does
not store them, convert with them or decide whether they are usable.

No retries and no backoff (rule 2): this runs on a schedule, and a day the feed is
down is a day with no new rates, which staging then reports honestly through the
staleness tolerance rather than papering over. No `try/except` around the client
(rule 3) - a failure raises with the publisher id attached and nothing else.

Recorded against a real response on 2026-09-12 rather than built from
documentation: the feed returns a bare JSON array of objects shaped
`{"r030": 840, "txt": "Долар США", "rate": 44.5483, "cc": "USD",
"exchangedate": "14.09.2026", "special": "N"}`. Three things about that shape
drive the parsing below. `exchangedate` is DD.MM.YYYY, not ISO, so it is parsed
by an explicit format rather than by `date.fromisoformat`. `rate` arrives as a
JSON number, so it is read through `str()` into `Decimal` - going via float would
put binary rounding error into a figure that ends up on a record. And the row has
exactly those six keys: a row with a key this module has never seen is the feed
changing shape, and rule 4 says that raises rather than being read past.

THE DATE IS ASKED FOR, NOT ACCEPTED. That recorded response is stamped
14.09.2026 and was fetched on the 12th: NBU sets the next banking day's official
rate the afternoon before, and from then on the undated endpoint serves it. So
`fetch` asks for the run's own UTC date and `parse` refuses a response dated
anything else. A weekend day returns the Friday rate under the weekend's own
date, which is what it should be stamped as.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import structlog

from monitor.connectors.base import TIMEOUT_SECONDS, user_agent
from monitor.fx.config import FxConfig

log = structlog.get_logger(__name__)

# The publisher's own date format in the RESPONSE. Its own, not ISO, and not
# negotiable. (The date in the REQUEST is a different format and lives in config.)
DATE_FORMAT = "%d.%m.%Y"

# Every key a row carries in the recorded response. A superset is a changed feed.
ROW_KEYS = frozenset({"r030", "txt", "rate", "cc", "exchangedate", "special"})


@dataclass(frozen=True)
class Rate:
    currency: str
    rate_date: date
    uah_per_unit: Decimal


class FxFetchError(Exception):
    """The rate publisher failed. Carries its id, the way ConnectorError does."""

    def __init__(self, publisher_id: str, message: str) -> None:
        super().__init__(f"{publisher_id}: {message}")
        self.publisher_id = publisher_id


def parse(document: object, config: FxConfig, *, asked_for: date) -> list[Rate]:
    """The feed's JSON to Rate rows. Unknown shape, or the wrong day, raises (rule 4)."""
    publisher = config.publisher.id
    if not isinstance(document, list):
        raise FxFetchError(publisher, f"expected a JSON array, got {type(document).__name__}")
    floor = config.publisher.minimum_rows
    if len(document) < floor:
        raise FxFetchError(publisher, f"{len(document)} rows, fewer than the {floor} a healthy day carries")

    rates: list[Rate] = []
    for row in document:
        if not isinstance(row, dict):
            raise FxFetchError(publisher, f"expected an object per row, got {type(row).__name__}")
        missing = {"cc", "rate", "exchangedate"} - set(row)
        if missing:
            raise FxFetchError(publisher, f"row is missing {', '.join(sorted(missing))}")
        unknown = set(row) - ROW_KEYS
        if unknown:
            raise FxFetchError(publisher, f"row carries {', '.join(sorted(unknown))}, which this feed has never had")

        currency = str(row["cc"]).strip().upper()
        # Every one of the 45 rows in the recorded response is a three-letter
        # alphabetic ISO 4217 code, metals and the IMF's XDR included (XAU, XAG,
        # XPT, XPD, XDR are codes, not exceptions to the format). So a row that is
        # not one is the feed changing shape, and rule 4 says that raises rather
        # than being skipped past. Those five are stored like any other row: they
        # cost a handful of bytes, they satisfy the column's own format check, and
        # no tender is ever denominated in them, so nothing downstream asks.
        if len(currency) != len("USD") or not currency.isalpha():
            raise FxFetchError(publisher, f"{currency!r} is not a three-letter code")

        amount = Decimal(str(row["rate"]))
        if amount <= 0:
            raise FxFetchError(publisher, f"{currency} quoted at {amount}, which is not a rate")

        rates.append(
            Rate(
                currency=currency,
                rate_date=datetime.strptime(str(row["exchangedate"]).strip(), DATE_FORMAT).date(),
                uah_per_unit=amount,
            )
        )

    quoted = {rate.currency for rate in rates}
    if config.target_currency not in quoted:
        raise FxFetchError(publisher, f"no {config.target_currency} row, so the day converts nothing")
    if config.pegged_to_eur and "EUR" not in quoted:
        raise FxFetchError(publisher, "no EUR row, so the pegged currencies convert nothing")

    dates = {rate.rate_date for rate in rates}
    if len(dates) != 1:
        raise FxFetchError(publisher, f"one response carries {len(dates)} different dates: {sorted(dates)}")
    if dates != {asked_for}:
        raise FxFetchError(publisher, f"asked for {asked_for} and the response is dated {dates.pop()}")

    return rates


def fetch(config: FxConfig, *, today: date | None = None) -> list[Rate]:
    """One polite pass at the publisher for one named day (rule 21: identified, once per schedule).

    `today` is an argument so a test can fix the day; the CLI leaves it to the
    clock. It is the run's UTC date, which is the date every schedule in this
    repository is written in.
    """
    asked_for = today or datetime.now(UTC).date()
    url = config.publisher.url.format(date=asked_for.strftime(config.publisher.query_date_format))
    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        rates = parse(response.json(), config, asked_for=asked_for)

    log.info(
        "fx.fetched",
        publisher=config.publisher.id,
        rate_date=str(rates[0].rate_date),
        currencies=len(rates),
    )
    return rates
