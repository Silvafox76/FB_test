"""World Bank procurement notices, search.worldbank.org API v2.

Recorded from a real call on 2026-09-11. The fixture is
`tests/contract/fixtures/worldbank.json` and this parser is written against the
fields that are in it.

**The one thing this connector exists to defend against: this API ignores query
parameters it does not recognise, in silence, and answers with the whole corpus.**
Probed on 2026-09-11: `countrycode`, `count_exact`, `noticetype`, `strdate`,
`enddate`, `fromdate`, `noticedatefrom`, `daterange`, `date_range` and
`noticedate_range` are all accepted with a 200 and all return the unfiltered
418,561 rows. Only `noticedate=10-Sep-2026` is refused, with a 400. A connector
that trusted its own query here would read a page of Contract Awards from
anywhere on earth and report a healthy run. So the parser checks that every row
that came back is inside what was asked for - `notice_type` in `NOTICE_TYPES`,
`project_ctry_name` in the registry's `covers` - and raises when it is not. That
is rule 4 against a measured hazard rather than a general nervousness.

The same silence is why the page's ordering is checked too. There is no
server-side date filter at all, so the window is `srt=noticedate&order=desc` plus
a client-side cut at the first row older than the lookback. That cut is only
sound while the sort holds: `srt` being ignored one day would look exactly like a
quiet source, because the walk would stop at the first old row on an unsorted page
and report two or three notices instead of thirteen. Verified strictly monotonic
across a full 1,000-row response on 2026-09-11, and asserted per page here.

Three more measured facts that shaped the request:

  1. `rows` caps at 1,000 (`rows=2000` returns 1,000) and `os` is the offset.
  2. `^` is the OR separator inside a value; `project_ctry_name` and `notice_type`
     match exactly, so the Bank's own country spellings are what must be sent -
     "Gambia, The", "Cote d'Ivoire" without the accent - and they are neither ISO
     codes nor the names in `monitor/normalise/codes.py`.
  3. Contract Award is 308,619 of the 418,561 rows and is a tender already
     decided. It is excluded at the query for the same reason step 4 excludes
     TED's award types: nothing is read and then thrown away. The three wanted
     types return 105,719 rows, 12,728 of them in the 19 covered countries.

`order=asc` surfaces rows whose `noticedate` is null; descending keeps them at the
far end of the corpus, which is the second reason the sort is this way round.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# 13 notices arrived in the two days before the fixture was recorded, so 100 rows
# is roughly two weeks of headroom in one request: the walk stops at the first row
# older than the window and the rest of the page is never looked at. The API's
# maximum is 1,000, which would be 400 KB of HTML bodies fetched to use 3% of it.
PAGE_SIZE = 100
LOOKBACK_DAYS = 2

# The absolute bound on requests per run, above the registry's expected_max. A
# burst that filled page after page with in-window rows still stops here.
MAX_PAGES = 10

# The three notice types that are still an opportunity. Counts over the whole
# corpus on 2026-09-11: Invitation for Bids 45,434, Request for Expression of
# Interest 60,285, General Procurement Notice 3,454. Contract Award (308,619) is a
# decided tender and is not asked for.
NOTICE_TYPES = (
    "Invitation for Bids",
    "Request for Expression of Interest",
    "General Procurement Notice",
)

# The Bank's own spelling of each covered country, keyed by the ISO code the
# registry uses. This is the source's vocabulary, not configuration: rule 6 puts
# keywords, thresholds, weights and placeholders in YAML because a person tunes
# them, and nobody tunes "Gambia, The" - it is what the API answers to, and the
# day it changes this connector must fail rather than be re-tuned. It sits beside
# `monitor/normalise/codes.py`'s alpha-3 table for the same reason, and not *in*
# it because the mapping is this source's and no other's. It is also not in
# sources/worldbank.yaml: `Source` forbids unknown fields, so a `country_names`
# block there would have to be added to the model every source is validated
# against, which puts one source's vocabulary in all of their contracts.
#
# All 19 verified against the live API on 2026-09-11; every one returns rows.
BANK_COUNTRY_NAMES = {
    # West Africa
    "BJ": "Benin",
    "BF": "Burkina Faso",
    "CI": "Cote d'Ivoire",  # the Bank writes it unaccented
    "GM": "Gambia, The",
    "GH": "Ghana",
    "LR": "Liberia",
    "ML": "Mali",
    "MR": "Mauritania",
    "NE": "Niger",
    "NG": "Nigeria",
    "SN": "Senegal",
    "SL": "Sierra Leone",
    "TG": "Togo",
    # Ukraine and the Western Balkans
    "UA": "Ukraine",
    "AL": "Albania",
    "BA": "Bosnia and Herzegovina",
    "XK": "Kosovo",
    "ME": "Montenegro",
    "MK": "North Macedonia",
}

ISO2_BY_BANK_NAME = {name: code for code, name in BANK_COUNTRY_NAMES.items()}

# `%b` reads the C locale's month abbreviations, so a host with LC_TIME set would
# parse "10-Sep-2026" differently or not at all. The window cut depends on this
# date, so it is spelled out rather than left to the environment.
MONTHS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

# The container, and the fields that must be present or the notice cannot be
# mapped at all. Deliberately absent from this list, with the count from the
# recorded page of 100: `bid_description` (90), `bid_reference_no` (90),
# `procurement_group` (90) and the `submission_deadline_*` pair (91). Those are
# missing on every General Procurement Notice, which announces a project's
# procurement programme rather than one bid, so requiring them would fail a run on
# a notice that is simply a different kind of notice. What is required is what the
# filter check and the mapper read on all 100.
CONTAINER = "procnotices"
REQUIRED_FIELDS = (
    "id",
    "notice_type",
    "noticedate",
    "notice_lang_name",
    "project_ctry_name",
    "project_name",
    "notice_text",
    "contact_organization",
)

# The public page for one notice. Verified 2026-09-11.
NOTICE_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"


class WorldBankConnector(FeedConnector):
    """One sorted page, cut at the window, checked against what was asked for."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: there is no CPV code anywhere in this source, so every notice
        # reaches the lexicon stage. Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest notice date this run keeps. Daily schedule, two-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def params(self, offset: int) -> dict:
        return {
            "format": "json",
            "rows": PAGE_SIZE,
            "os": offset,
            "srt": "noticedate",
            "order": "desc",
            "project_ctry_name": country_query(self.source.covers),
            "notice_type": type_query(),
        }

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        wanted_countries = frozenset(country_names(self.source.covers))
        raw_notices: list[RawNotice] = []
        reached_window_end = False

        for page in range(MAX_PAGES):
            response = client.get(self.source.api_url, params=self.params(offset=page * PAGE_SIZE))
            response.raise_for_status()
            document = response.json()

            rows = parse_notices(document, countries=wanted_countries)
            in_window = within_window(rows, cutoff)
            reached_window_end = len(in_window) < len(rows)

            raw_notices.extend(
                self.raw_notice(
                    url=NOTICE_URL.format(id=row["id"]),
                    payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
                for row in in_window
            )

            if reached_window_end or len(rows) < PAGE_SIZE:
                break
            if len(raw_notices) >= self.source.expected_max:
                log.warning(
                    "worldbank_ceiling_reached",
                    fetched=len(raw_notices),
                    ceiling=self.source.expected_max,
                    detail="raise expected_items_per_run in sources/worldbank.yaml",
                )
                break
        else:
            log.warning("worldbank_max_pages_reached", fetched=len(raw_notices), max_pages=MAX_PAGES)

        if not reached_window_end:
            # The walk ran out of pages or rows before it ran out of window, so the
            # oldest notice in the window may not have been read. Said out loud
            # rather than reported as a complete pass.
            log.info("worldbank_window_not_closed", fetched=len(raw_notices), cutoff=cutoff.isoformat())

        log.info("worldbank_fetch", notices=len(raw_notices), cutoff=cutoff.isoformat())
        return raw_notices


def country_query(covers: list[str]) -> str:
    """The `project_ctry_name` value for the registry's covered countries.

    `^` is the OR separator. An ISO code with no Bank spelling raises: sending 18
    of 19 countries would read as a quiet day in the nineteenth.

    **A name containing an apostrophe is dropped unless it comes first**, which is
    why the order here is not the registry's. Measured against the live API on
    2026-09-12, and it is worth stating as counts because the failure is silent:

        Cote d'Ivoire alone          5,996
        Ghana alone                  4,452
        "Cote d'Ivoire^Ghana"       10,448   both
        "Ghana^Cote d'Ivoire"        4,452   Ghana only

    Every other shape joins correctly in any position, including multi-word names
    and one containing a comma ("Gambia, The"), so this is the apostrophe and not
    word count or punctuation generally. No escaping works: doubling it, escaping
    it, quoting the value and substituting a curly apostrophe or the accented
    "Côte d'Ivoire" all return Ghana alone.

    So the ordering is the workaround, and it was costing the pilot one of its
    thirteen West African countries on every run - 5,996 notices' worth of corpus,
    reported as a healthy fetch. The connector's existing guard could not catch it:
    it checks that nothing UNWANTED comes back, and a country that silently returns
    nothing is indistinguishable from a country having a quiet day.

    Only one apostrophe can be first, so a second one raises rather than being
    quietly dropped the way the first was.
    """
    names = country_names(covers)
    with_apostrophe = [name for name in names if "'" in name]

    if len(with_apostrophe) > 1:
        raise ValueError(
            f"more than one covered country name contains an apostrophe: {with_apostrophe}. "
            "Only the first survives this API's OR join, so the rest would yield nothing and "
            "report a healthy run. Split the query per country in "
            "monitor/connectors/worldbank.py before adding another."
        )

    return "^".join(with_apostrophe + [name for name in names if "'" not in name])


def country_names(covers: list[str]) -> list[str]:
    missing = [code for code in covers if code not in BANK_COUNTRY_NAMES]
    if missing:
        raise ValueError(
            f"no World Bank country name for {missing}; add it to BANK_COUNTRY_NAMES in monitor/connectors/worldbank.py"
        )
    return [BANK_COUNTRY_NAMES[code] for code in covers]


def type_query() -> str:
    """The `notice_type` value: the three types that are still an opportunity."""
    return "^".join(NOTICE_TYPES)


def parse_notices(document: dict, *, countries: frozenset[str]) -> list[dict]:
    """The notices of one response, checked for fields, filter and order.

    Three checks, in the order a failure is cheapest to explain:

      1. The container and the fields the mapper reads, as every other connector
         does, so a renamed field raises instead of yielding fewer notices.
      2. That the server honoured the filter. It ignores unknown parameters in
         silence, so a row outside the asked-for types or countries means the
         query stopped filtering, and continuing would pour the whole corpus into
         the pipeline.
      3. That the page is sorted newest first, which is what makes the client-side
         window cut sound. `srt` could be ignored as quietly as the date
         parameters are, and the failure would look like a slow news day.
    """
    if CONTAINER not in document:
        raise ValueError(f"World Bank response has no {CONTAINER!r} key; got {sorted(document)}")

    rows = document[CONTAINER]
    for position, row in enumerate(rows):
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"World Bank notice {position} ({row.get('id', '?')}) is missing {missing}")

        if row["notice_type"] not in NOTICE_TYPES:
            raise ValueError(
                f"World Bank notice {row['id']} is a {row['notice_type']!r}, which was not asked for; "
                "the notice_type filter was ignored and the response is the unfiltered corpus"
            )
        if row["project_ctry_name"] not in countries:
            raise ValueError(
                f"World Bank notice {row['id']} is for {row['project_ctry_name']!r}, which is not covered; "
                "the project_ctry_name filter was ignored and the response is the unfiltered corpus"
            )

    dates = [notice_date(row) for row in rows]
    for position, (older, newer) in enumerate(zip(dates[1:], dates, strict=False)):
        if older > newer:
            raise ValueError(
                f"World Bank rows are not sorted by noticedate descending: row {position + 1} "
                f"({older.isoformat()}) is newer than row {position} ({newer.isoformat()}); "
                "the srt parameter was ignored and the window cut would under-read"
            )
    return rows


def within_window(rows: list[dict], cutoff: date) -> list[dict]:
    """The rows from the top of a descending page down to the first older one.

    Everything below that first old row is older still, which is the property the
    sort check above defends.
    """
    kept: list[dict] = []
    for row in rows:
        if notice_date(row) < cutoff:
            break
        kept.append(row)
    return kept


def notice_date(row: dict) -> date:
    """`noticedate`, which is "10-Sep-2026". Raises on anything else.

    The window cut and `published_at` both read this, so an unparseable value is a
    changed API rather than a notice to skip.
    """
    raw = (row.get("noticedate") or "").strip()
    parts = raw.split("-")
    if len(parts) != 3 or parts[1] not in MONTHS:
        raise ValueError(f"World Bank notice {row.get('id', '?')} has noticedate {raw!r}, not d-Mon-yyyy")
    try:
        return datetime(int(parts[2]), MONTHS[parts[1]], int(parts[0])).date()
    except ValueError as cause:
        raise ValueError(f"World Bank notice {row.get('id', '?')} has noticedate {raw!r}: {cause}") from cause
