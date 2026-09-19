"""Contracts Finder, the UK's national below-OJEU-threshold procurement feed, run
by the Crown Commercial Service (Cabinet Office). OCDS 1.1, the same publisher and
the same OCDS shape as Find a Tender (`fts.py`) but zero overlap in the notices
each one carries - see `sources/contractsfinder_gb.yaml` for the live check that
established that.

Recorded from a real call on 2026-09-19. The fixture is
`tests/contract/fixtures/contractsfinder_gb.json`;
`tests/contract/fixtures/contractsfinder_gb.notes.txt` records the one change made
to it before commit: twelve of the fourteen recorded releases named an individual
buyer contact (name, work email, sometimes a direct phone), and rule 19 strips
that and nothing else.

Query parameters, all server-side, from `sources/contractsfinder_gb.yaml`:
`publishedFrom` / `publishedTo` (seconds precision, no timezone suffix - the same
shape Find a Tender's `updatedFrom` takes) and `stages=tender`, the opportunity
stage; award and contract stages are the same procurement after the decision,
the same choice `fts.py` makes and for the same reason. There is no CPV query
parameter on this endpoint.

CPV lives at `tender.classification` on every one of the 14 recorded releases -
not in `items[].additionalClassifications`, which is where Find a Tender carries
it. The two publishers share an OCDS shape but not this detail, confirmed
directly against the live sample rather than assumed from Find a Tender's shape.

One live fact the registry note did not carry: three of the fourteen recorded
releases carry `"tag": ["tenderAmendment"]` rather than `["tender"]`, even though
the query asked for `stages=tender`. `stages` filters on procurement lifecycle
stage (planning / tender / award / contract / implementation); `tag` names the
release type within that stage, and an amendment to a tender notice is still a
tender-stage release - it has not reached award. `parse_releases` accepts every
tag that keeps a release in the tender stage and raises on anything that would
mean the query had started returning award or contract releases instead.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

PAGE_SIZE = 100
LOOKBACK_DAYS = 2
MAX_PAGES = 20

# Verified live 2026-09-19: `stages=tender` returns only pre-award releases, same
# choice fts.py makes and for the same reason - an opportunity is what has not
# been awarded yet.
STAGES = "tender"

REQUIRED_RELEASE_FIELDS = ("ocid", "id", "date", "tender")
REQUIRED_TENDER_FIELDS = ("title", "status")

# Release types that stay within the tender stage. "tender" is a new notice; the
# other three are updates to one that keep it pre-award. Any other tag (an award
# or contract tag, say) would mean the stages=tender filter had stopped doing
# what it says, and that is a layout change to raise on, not absorb quietly.
TENDER_STAGE_TAGS = frozenset({"tender", "tenderUpdate", "tenderAmendment", "tenderCancellation"})


class ContractsfinderGbConnector(FeedConnector):
    """One page per run; the seven-day volume (66-70) never approaches PAGE_SIZE."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Contracts Finder has no CPV query parameter either; kept only for the
        # uniform connector constructor every entry in monitor/fetch.py's
        # CONNECTORS table takes, and unused here for the same reason fts.py
        # never uses it: the free filter decides at step 5.
        self.cpv_prefixes = cpv_prefixes

    def published_from(self, since: datetime | None = None) -> str:
        moment = since or (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS))
        return moment.strftime("%Y-%m-%dT%H:%M:%S")

    def published_to(self, until: datetime | None = None) -> str:
        moment = until or datetime.now(UTC)
        return moment.strftime("%Y-%m-%dT%H:%M:%S")

    def fetch_raw(
        self,
        client: httpx.Client,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[RawNotice]:
        raw_notices: list[RawNotice] = []
        cursor = None
        params = {
            "publishedFrom": self.published_from(since),
            "publishedTo": self.published_to(until),
            "stages": STAGES,
            "limit": PAGE_SIZE,
        }

        for _ in range(MAX_PAGES):
            response = client.get(cursor or self.source.api_url, params=None if cursor else params)
            response.raise_for_status()
            document = response.json()

            releases = parse_releases(document)
            raw_notices.extend(
                self.raw_notice(
                    url=release_url(release),
                    payload=json.dumps(release, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
                for release in releases
            )

            if len(releases) < PAGE_SIZE or len(raw_notices) >= self.source.expected_max:
                break
            cursor = (document.get("links") or {}).get("next")
            if not cursor:
                break

        log.info("contractsfinder_gb_fetch", releases=len(raw_notices))
        return raw_notices


def parse_releases(document: dict) -> list[dict]:
    """The releases of one package, checked for the fields the mapper reads."""
    if "releases" not in document:
        raise ValueError(f"Contracts Finder response has no 'releases' key; got {sorted(document)}")

    releases = document["releases"]
    for position, release in enumerate(releases):
        missing = [field for field in REQUIRED_RELEASE_FIELDS if field not in release]
        if missing:
            raise ValueError(f"Contracts Finder release {position} ({release.get('ocid', '?')}) is missing {missing}")

        tender = release["tender"]
        missing_tender = [field for field in REQUIRED_TENDER_FIELDS if field not in tender]
        if missing_tender:
            raise ValueError(f"Contracts Finder release {release.get('ocid', '?')} tender is missing {missing_tender}")

        tags = set(release.get("tag") or [])
        if not tags & TENDER_STAGE_TAGS:
            raise ValueError(
                f"Contracts Finder release {release.get('ocid', '?')} carries tag {sorted(tags)}, none of "
                "which is a tender-stage tag; the stages=tender query may have stopped working"
            )
    return releases


def release_url(release: dict) -> str:
    """The public notice page, read from the release's own stated document URL.

    Not derived from `release['id']` by string surgery: the id embeds an internal
    numeric suffix ("...-914731") the public URL does not carry, and the release
    already states the real address in `tender.documents`, so that is what is
    read. `sources/contractsfinder_gb.yaml` is explicit that this connector must
    not fetch that page (it returns a bare WAF 403 for this client) - reading its
    stated address out of the OCDS payload is not fetching it.
    """
    for document in release["tender"].get("documents") or []:
        if document.get("documentType") == "tenderNotice" and document.get("format") == "text/html":
            return document["url"]
    raise ValueError(f"Contracts Finder release {release.get('ocid', '?')} has no tenderNotice html document url")
