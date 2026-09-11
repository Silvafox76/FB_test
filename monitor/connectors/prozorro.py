"""Prozorro, Ukraine's national e-procurement system. API 2.5.

Recorded from real calls on 2026-09-12. The fixture is
`tests/contract/fixtures/prozorro.json` and holds both halves of what this
connector does: one listing page and the detail records it then fetched.

Prozorro is shaped nothing like TED and the difference decides the design.

  - The list endpoint is a *change feed*, not a data feed. It returns ids and a
    `dateModified` and, by default, oldest first, starting in 2015. `descending=1`
    turns it around. `opt_fields` adds a few fields to each row but silently drops
    ones it does not support, `title` among them, so there is no way to see what a
    tender is about without fetching it.
  - A detail record is about 110 KB. Fetching one per modified tender is the only
    route to the title, and 100 tenders were modified in the 74 minutes before the
    fixture was recorded, which is roughly 2,000 a day.
  - Of those 100, nine were `active.tendering`. The rest were in auction,
    qualification, award or already complete: tenders that are running or finished,
    not opportunities anyone can still bid for.

So the listing is filtered on `status` before any detail is fetched, which is the
difference between about 180 detail requests a day and about 2,000. That is an
acquisition-scope decision rather than a filter stage (rule 5): it decides what to
read, not what to keep, and nothing is dropped after being read.

DK021 is Ukraine's national classifier. Its codes are CPV-shaped and its top two
digits carry the same divisions, so they are mapped to CPV **by the top-level
prefix only**. Below that the two diverge and a full-code mapping would be wrong.

Titles stay in Ukrainian with `language = uk` (rule 9). They stop at "needs
translation" unless they carry a passing CPV prefix, in which case they reach the
scorer in Ukrainian and the prompt asks for `title_en` back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

LIST_PAGE_SIZE = 100
LOOKBACK = timedelta(days=1)

# Verified on 2026-09-12: these are returned; `title` is not.
LIST_FIELDS = "status,procurementMethodType,dateModified"

# The statuses that are still open to a bidder. Everything else on the feed is a
# tender in auction, in qualification, awarded, complete, cancelled or
# unsuccessful. Generous on purpose: recall matters more than the cost of a detail
# fetch, because the CPV and lexicon stages drop what is irrelevant afterwards for
# nothing.
OPEN_STATUSES = frozenset(
    {
        "active",  # the tendering stage for some method types
        "active.tendering",
        "active.enquiries",
        "active.pre-qualification",
    }
)

# An absolute bound on detail requests per run, on top of the registry's
# expected_max. Prozorro is the whole of Ukrainian public procurement and a quiet
# assumption about volume is how a polite pass becomes an impolite one (rule 21).
MAX_DETAIL_REQUESTS = 400
MAX_LIST_PAGES = 20


class ProzorroConnector(FeedConnector):
    """List the change feed, keep what is still open, fetch those in full."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused by the fetch: Prozorro cannot filter on classification and the CPV
        # decision is the filter's at step 5. Taken so every connector is built the
        # same way.
        self.cpv_prefixes = cpv_prefixes

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        since = datetime.now(UTC) - LOOKBACK
        open_ids = self.open_tender_ids(client, since)

        raw_notices = []
        for tender_id in open_ids[: min(self.source.expected_max, MAX_DETAIL_REQUESTS)]:
            detail = self.fetch_detail(client, tender_id)
            raw_notices.append(
                self.raw_notice(
                    url=f"https://prozorro.gov.ua/tender/{detail.get('tenderID', tender_id)}",
                    payload=json.dumps(detail, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
            )

        log.info("prozorro_fetch", open_found=len(open_ids), detail_fetched=len(raw_notices))
        return raw_notices

    def open_tender_ids(self, client: httpx.Client, since: datetime) -> list[str]:
        """Walk the feed newest first until it is older than the window."""
        found: list[str] = []
        offset = None

        for _ in range(MAX_LIST_PAGES):
            params = {"limit": LIST_PAGE_SIZE, "descending": 1, "opt_fields": LIST_FIELDS}
            if offset:
                params["offset"] = offset
            page = client.get(self.source.api_url, params=params)
            page.raise_for_status()
            document = page.json()

            rows = parse_listing(document)
            if not rows:
                break

            found.extend(row["id"] for row in rows if row.get("status") in OPEN_STATUSES)
            if oldest_modified(rows) < since:
                break

            offset = (document.get("next_page") or {}).get("offset")
            if not offset:
                break

        return found

    def fetch_detail(self, client: httpx.Client, tender_id: str) -> dict:
        response = client.get(f"{self.source.api_url}/{tender_id}")
        response.raise_for_status()
        document = response.json()
        if "data" not in document:
            raise ValueError(f"Prozorro tender {tender_id} has no 'data' key; got {sorted(document)}")
        return document["data"]


def parse_listing(document: dict) -> list[dict]:
    """The rows of one listing page, checked for the fields the walk reads."""
    if "data" not in document:
        raise ValueError(f"Prozorro listing has no 'data' key; got {sorted(document)}")

    rows = document["data"]
    for position, row in enumerate(rows):
        missing = [field for field in ("id", "dateModified") if field not in row]
        if missing:
            raise ValueError(f"Prozorro listing row {position} is missing {missing}")
    return rows


def oldest_modified(rows: list[dict]) -> datetime:
    return min(datetime.fromisoformat(row["dateModified"]) for row in rows)
