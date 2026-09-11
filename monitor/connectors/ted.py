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
     three-letter language code, with a translation into all 24 EU languages.
     TED does its own translating, so `eng` is present on every notice. The
     original-language value is still what is stored (rule 9); the English one is
     carried to the score as a derived field and is why TED will not need the
     step 14 translation stage.
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

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

API_URL = "https://api.ted.europa.eu/v3/notices/search"
PAGE_SIZE = 50
LOOKBACK_DAYS = 2

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
    """One page of TED notices per run. No paging: 50 a day is the whole point."""

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
        response = client.post(
            self.source.api_url,
            json={"query": self.query(), "fields": FIELDS, "limit": PAGE_SIZE, "page": 1},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        document = response.json()
        return [self.to_raw_notice(notice) for notice in parse_notices(document)]

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
