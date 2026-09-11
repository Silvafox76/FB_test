"""Tenders Electronic Daily, TED Search API v3.

Recorded from a real call on 2026-09-11. The fixture is
`tests/contract/fixtures/ted.json` and this parser is written against the fields
that are in it, not against documentation.

The exact request, as recorded:

    POST https://api.ted.europa.eu/v3/notices/search
    {"query": "(classification-cpv=48* OR classification-cpv=72* OR "
              "classification-cpv=79*) AND publication-date>=20260909",
     "fields": [...FIELDS below...], "limit": 50, "page": 1}

    -> 200, 1449 matching notices, 50 returned.

Four things the live response settled that a guess would have got wrong:

  1. `fields` is mandatory and every name is validated server side against 1,830
     eForms business-term names. A wrong name fails the whole request with a 400
     listing all of them; it does not silently return less.
  2. `notice-title`, `description-proc` and `buyer-name` are objects keyed by a
     three-letter language code. TED translates the title into all 24 EU
     languages, so `eng` is on the title of every recorded notice. It does not
     translate the description: 48 of the 50 carry exactly one language there, and
     only 3 have English at all. So the English title is free and the English body
     is not, and TED still needs the step 14 translation stage for its bodies.
     The original-language value is what is stored either way (rule 9).
  3. Codes are three letters: `ESP` for the country, `SPA` for the language. The
     registry, the geography weights and the lexicons are all two-letter.
  4. Dates are `2026-09-09+02:00`: a date with an offset and no time at all. The
     per-lot deadline is a list, one entry per lot, and it is absent entirely on
     the notices that have no tender deadline.

`notice-type` is carried through untouched. Roughly half of any TED page is
`can-standard`, an award notice for a tender that is already decided; dropping
those is the filter's decision at step 5, not this module's (rule 5).
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

API_URL = "https://api.ted.europa.eu/v3/notices/search"

# 250 is the API's maximum, verified on 2026-09-11: limit=300 is rejected with
# "Value (300) of parameter 'limit' exceeds maximum allowed value (250)".
PAGE_SIZE = 250
LOOKBACK_DAYS = 2

# A run stops here even if the source says there is more, so a query that suddenly
# matches a hundred thousand notices cannot pull them all in one pass. The real
# ceiling per source is its registry `expected_max`; this is the absolute one.
MAX_PAGES = 40

# Verified against the live API on 2026-09-11. See the module docstring.
FIELDS = [
    "publication-number",
    "notice-title",
    "description-proc",
    "buyer-name",
    "buyer-country",
    "buyer-legal-type",
    "classification-cpv",
    "publication-date",
    "deadline-receipt-tender-date-lot",
    "estimated-value-proc",
    "estimated-value-cur-proc",
    "official-language",
    "notice-type",
    "links",
]

# Every field the parser reads on every notice. A response missing one of these is
# a changed API, and the contract test asserts that it raises rather than yielding
# zero rows (rule 4). `deadline-receipt-tender-date-lot`, `buyer-legal-type` and
# the estimated value are deliberately not here: the recorded response has them on
# 16, 46 and 21 of 50 notices, so their absence is normal.
REQUIRED_FIELDS = (
    "publication-number",
    "notice-title",
    "description-proc",
    "buyer-name",
    "buyer-country",
    "classification-cpv",
    "publication-date",
    "official-language",
    "notice-type",
    "links",
)


class TedConnector(FeedConnector):
    """Every notice the query matches, paged.

    Reading one page was the original shape and it was wrong: the recorded query
    matched 1,449 notices and the connector took the first 50, in an order the API
    does not document. That is a 3.5% sample of its own query, so nothing
    downstream could claim recall, and a real opportunity published on a busy day
    was as likely to be missed as seen.

    Paging is not a retry (rule 2) and not a fallback (rule 1): each page is a
    distinct request for a distinct slice, made once, and any failure raises. The
    pass is still one polite pass per schedule (rule 21) - sequential, one
    connection, identified user agent - it just takes six requests rather than one.
    """

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # From config/thresholds.yaml, never hardcoded here (rule 6).
        self.cpv_prefixes = cpv_prefixes

    def query(self, today: date | None = None) -> str:
        """CPV in the pass prefixes, published in the last two days.

        Dates are yyyymmdd with no separators and `field=value*` is the prefix
        form; both verified against the live API.
        """
        since = ((today or date.today()) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
        prefixes = " OR ".join(f"classification-cpv={p}*" for p in self.cpv_prefixes)
        return f"({prefixes}) AND publication-date>={since}"

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        query = self.query()
        ceiling = self.source.expected_max
        raw_notices: list[RawNotice] = []
        total = 0

        for page in range(1, MAX_PAGES + 1):
            document = self.fetch_page(client, query, page)
            notices = parse_notices(document)
            total = document.get("totalNoticeCount", 0)

            raw_notices.extend(self.to_raw_notice(notice) for notice in notices)

            if len(notices) < PAGE_SIZE:
                break
            if len(raw_notices) >= ceiling:
                log.warning(
                    "ted_ceiling_reached",
                    fetched=len(raw_notices),
                    matched=total,
                    ceiling=ceiling,
                    detail="raise expected_items_per_run in sources/ted.yaml or narrow the query",
                )
                break
        else:
            log.warning("ted_max_pages_reached", fetched=len(raw_notices), matched=total, max_pages=MAX_PAGES)

        if total > len(raw_notices):
            log.info("ted_partial_read", fetched=len(raw_notices), matched=total)

        return raw_notices

    def fetch_page(self, client: httpx.Client, query: str, page: int) -> dict:
        """One page. One attempt; any failure raises and the base class names the source."""
        response = client.post(
            self.source.api_url,
            json={"query": query, "fields": FIELDS, "limit": PAGE_SIZE, "page": page},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        document = response.json()

        # The API says so itself when a search did not complete. An incomplete read
        # that looks like a complete one is the failure this whole discipline is
        # against (rule 4).
        if document.get("timedOut"):
            raise ValueError(f"TED reported timedOut on page {page}; the result set is incomplete")

        return document

    def to_raw_notice(self, notice: dict) -> RawNotice:
        return self.raw_notice(
            url=notice_url(notice),
            payload=json.dumps(notice, ensure_ascii=False, sort_keys=True),
            mime="application/json",
        )


def parse_notices(document: dict) -> list[dict]:
    """The notices out of a search response, checked for the fields we read.

    Raises on a renamed or missing field rather than returning fewer notices: a
    parser that quietly yields zero is the failure this whole discipline exists to
    prevent (rule 4).
    """
    if "notices" not in document:
        raise ValueError(f"TED response has no 'notices' key; got {sorted(document)}")

    notices = document["notices"]
    for position, notice in enumerate(notices):
        missing = [field for field in REQUIRED_FIELDS if field not in notice]
        if missing:
            raise ValueError(f"TED notice {position} ({notice.get('publication-number', '?')}) is missing {missing}")
    return notices


def notice_url(notice: dict) -> str:
    """The English HTML permalink. Keys under `links.html` are upper-case codes."""
    html = notice["links"]["html"]
    return html.get("ENG") or html[sorted(html)[0]]
