"""Find a Tender, the UK's post-Brexit replacement for TED coverage. OCDS 1.1.

Recorded from a real call on 2026-09-12. The fixture is
`tests/contract/fixtures/fts.json`.

The easiest of the three connectors, and worth saying why: it publishes OCDS
release packages, so the shape is a published standard rather than a house format,
it filters server side on `updatedFrom` and `stages`, and it is small. Twenty-five
releases in the two days before the fixture was recorded, against TED's 1,449 and
Prozorro's roughly two thousand. One page covers it.

CPV lives in `tender.items[].additionalClassifications` where `scheme` is "CPV",
never in `items[].classification`, which on this publisher carries the buyer's own
scheme. Four of the 25 recorded releases carry no CPV at all; those go to the
lexicon stage, which is what "no CPV code is not a failed match" is for.
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

# Verified 2026-09-12: `stages=tender` returns only tender-stage releases, which is
# what an opportunity is. Award and contract stages are the same procurement after
# the decision.
STAGES = "tender"

REQUIRED_RELEASE_FIELDS = ("ocid", "id", "date", "tender")
REQUIRED_TENDER_FIELDS = ("title", "status")


class FtsConnector(FeedConnector):
    """One page per run, paged only if the window ever needs it."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Find a Tender has no CPV query parameter; the filter decides at step 5.
        self.cpv_prefixes = cpv_prefixes

    def updated_from(self, now: datetime | None = None) -> str:
        moment = (now or datetime.now(UTC)) - timedelta(days=LOOKBACK_DAYS)
        return moment.strftime("%Y-%m-%dT%H:%M:%S")

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        raw_notices: list[RawNotice] = []
        cursor = None

        for _ in range(MAX_PAGES):
            params = {"updatedFrom": self.updated_from(), "limit": PAGE_SIZE, "stages": STAGES}
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

        log.info("fts_fetch", releases=len(raw_notices))
        return raw_notices


def parse_releases(document: dict) -> list[dict]:
    """The releases of one package, checked for the fields the mapper reads."""
    if "releases" not in document:
        raise ValueError(f"Find a Tender response has no 'releases' key; got {sorted(document)}")

    releases = document["releases"]
    for position, release in enumerate(releases):
        missing = [field for field in REQUIRED_RELEASE_FIELDS if field not in release]
        if missing:
            raise ValueError(f"Find a Tender release {position} ({release.get('ocid', '?')}) is missing {missing}")

        tender = release["tender"]
        missing_tender = [field for field in REQUIRED_TENDER_FIELDS if field not in tender]
        if missing_tender:
            raise ValueError(f"Find a Tender release {release.get('ocid', '?')} tender is missing {missing_tender}")
    return releases


def release_url(release: dict) -> str:
    """The public notice page. The ocid is what the service's own URLs key on."""
    return f"https://www.find-tender.service.gov.uk/Notice/{release['id']}"
