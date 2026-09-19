"""Public Contracts Scotland (PCS), below-threshold site contract notices only.

Recorded from a real call on 2026-09-19. The fixture is
`tests/contract/fixtures/pcs_gb_sct.json`: one whole month (September 2026) of
`noticeType=102` releases, 62 of them.

`sources/pcs_gb_sct.yaml` explains why 102 is the only population this
connector ever asks for: 2 duplicates Find a Tender's own above-threshold
notices word for word, 101 is planning stage, 103/104 are award stage
(already decided). That is acquisition scope -- which population to request --
not a filter on a mixed feed, so `noticeType` is a fixed request parameter
here rather than something read from `sources/pcs_gb_sct.yaml`, the same way
`monitor/connectors/fts.py` fixes `stages=tender`.

The API's `dateFrom` parameter takes a *month* (mm-yyyy), not a day, and every
call returns that whole month's matching releases -- there is no
finer-grained date filter and no pagination metadata (confirmed live: the
response is one flat object with a `releases` list, nothing naming a next
page). A scheduled pass therefore requests the month(s) the window touches and
keeps only the releases whose own publication date (`release["date"]`, always
midnight UTC on this publisher) falls inside the window; the pipeline's
content_hash/external_id dedupe (steps 4/8) is what tells a new release from
one already seen on the next run, not this connector.

THE ONE THING THIS CONNECTOR MUST NEVER DO: fetch anything from
www.publiccontractsscotland.gov.uk. That host's robots.txt disallows every
user agent this connector could present (`User-agent: *` / `Disallow: /`), and
it is also the only host any `tender.documents[].url` in the payload ever
names -- on the recorded fixture that is 43 of 62 releases, from one entry
(the human notice page) up to eight (the notice page plus PDF/Word/image
attachments on the busiest release). This connector never reads a document
URL at all: it reads title, description, buyer, value, CPV,
`tenderPeriod.endDate` and the free-text description from the JSON already
returned, and the one URL it records per notice is the OCDS package's own
`links[].rel == "canonical"` entry, which stays on the api host
(`https://api.publiccontractsscotland.gov.uk/v1/Notice?id=...`).

PERSONAL DATA, redacted in the committed fixture, not by this connector. Every
release's `parties[]` can carry a `contactPoint`, and on the recorded response
37 of them name an individual (`"name": "Jasper Rea"`, "Mikko Saari", ...)
alongside a work email and often a direct telephone number -- the same hazard
`tests/contract/fixtures/liberia.json` and
`tests/contract/fixtures/contractsfinder_gb.json` already carry and redact.
Rule 19 forbids a staff contact detail reaching a model call and rule 20
forbids one in a committed fixture, so wherever `contactPoint.name` held a
person's name (never a role or a team, on this sample) that field was replaced
with `"REDACTED (named individual, rule 19)"` and any non-empty `email` or
`telephone` beside it with `"redacted@redacted.invalid"` / `"REDACTED"` --
the same placeholders and the same scoping `contractsfinder_gb.json` uses.
`contactPoint`s with no `name` field (a generic mailbox such as
`procurement@cairngorms.co.uk` with nobody named) were left exactly as
published, which is a real, documented limitation of this redaction and not
an oversight: a personal-looking local part with no accompanying `name` field
was not parsed for a name. Nothing else in the fixture was touched.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# sources/pcs_gb_sct.yaml: 102 is the below-threshold, tender-stage, open
# population ("Site CN") and the only one this connector reads. See the
# module docstring for what 2, 101, 103 and 104 are and why each is excluded
# at the request rather than after the fetch.
NOTICE_TYPE = "102"
OUTPUT_TYPE = "0"  # OCDS, confirmed live against the same parameter name TED uses for JSON.

# The default window when a scheduled pass calls with no since/until: the two
# days before today, same LOOKBACK_DAYS shape as monitor/connectors/ted.py and
# monitor/connectors/fts.py. since/until exist for a future backfill script,
# the same way TED's do (decision 59): a scheduled pass never passes them.
LOOKBACK_DAYS = 2

REQUIRED_RELEASE_FIELDS = ("ocid", "id", "date", "tag", "buyer", "parties", "tender", "language", "links")
REQUIRED_TENDER_FIELDS = ("title", "status")

# noticeType=102 is a single, unambiguous population (unlike Contracts Finder's
# stages=tender, which also lets amendment and cancellation tags through): every
# one of the 62 recorded releases carries exactly `"tag": ["tender"]` and
# `tender.status == "active"`. Anything else means the query stopped doing what
# sources/pcs_gb_sct.yaml says it does, which is a layout change to raise on.
EXPECTED_TAG = ["tender"]
EXPECTED_STATUS = "active"

WWW_HOST = "www.publiccontractsscotland.gov.uk"


class PcsGbSctConnector(FeedConnector):
    """One call per calendar month the window touches; each call returns that
    whole month's noticeType=102 population, so the connector filters by the
    release's own publication date afterwards rather than trusting the API to
    have windowed it.
    """

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # PCS has no CPV query parameter; the filter decides at step 5, the
        # same as Find a Tender.
        self.cpv_prefixes = cpv_prefixes

    def window(
        self, today: date | None = None, *, since: date | None = None, until: date | None = None
    ) -> tuple[date, date]:
        now = today or date.today()
        start = since or (now - timedelta(days=LOOKBACK_DAYS))
        end = until or now
        if start > end:
            raise ValueError(f"pcs_gb_sct: since {start} is after until {end}")
        return start, end

    def fetch_raw(
        self, client: httpx.Client, *, since: date | None = None, until: date | None = None
    ) -> list[RawNotice]:
        start, end = self.window(since=since, until=until)
        raw_notices: list[RawNotice] = []
        seen_ids: set[str] = set()

        for month in months_covering(start, end):
            document = self.fetch_month(client, month)
            for release in parse_releases(document):
                if not (start <= release_date(release) <= end):
                    continue
                if release["id"] in seen_ids:
                    continue  # only possible if the window spans two calendar months
                seen_ids.add(release["id"])
                raw_notices.append(
                    self.raw_notice(
                        url=release_url(release),
                        payload=json.dumps(release, ensure_ascii=False, sort_keys=True),
                        mime="application/json",
                    )
                )

        log.info("pcs_gb_sct_fetch", releases=len(raw_notices), window_start=str(start), window_end=str(end))
        return raw_notices

    def fetch_month(self, client: httpx.Client, month: date) -> dict:
        """One month's whole noticeType=102 population. One attempt; any failure raises."""
        response = client.get(
            self.source.api_url,
            params={"dateFrom": month.strftime("%m-%Y"), "noticeType": NOTICE_TYPE, "outputType": OUTPUT_TYPE},
        )
        response.raise_for_status()
        return response.json()


def months_covering(start: date, end: date) -> list[date]:
    """The first of each calendar month from `start` to `end`, inclusive, in order."""
    months = []
    cursor = start.replace(day=1)
    last = end.replace(day=1)
    while cursor <= last:
        months.append(cursor)
        cursor = date(cursor.year + 1, 1, 1) if cursor.month == 12 else date(cursor.year, cursor.month + 1, 1)
    return months


def parse_releases(document: dict) -> list[dict]:
    """The releases of one month's package, checked for the fields the connector reads.

    Raises rather than returning fewer releases: a parser that quietly yields zero
    on a renamed field is indistinguishable from a quiet day (rule 4).
    """
    if "releases" not in document:
        raise ValueError(f"Public Contracts Scotland response has no 'releases' key; got {sorted(document)}")

    releases = document["releases"]
    for position, release in enumerate(releases):
        missing = [field for field in REQUIRED_RELEASE_FIELDS if field not in release]
        if missing:
            raise ValueError(
                f"Public Contracts Scotland release {position} ({release.get('id', '?')}) is missing {missing}"
            )

        tender = release["tender"]
        missing_tender = [field for field in REQUIRED_TENDER_FIELDS if field not in tender]
        if missing_tender:
            raise ValueError(
                f"Public Contracts Scotland release {release.get('id', '?')} tender is missing {missing_tender}"
            )

        if release.get("tag") != EXPECTED_TAG:
            raise ValueError(
                f"Public Contracts Scotland release {release.get('id', '?')} carries tag {release.get('tag')}, "
                f"expected {EXPECTED_TAG}; noticeType={NOTICE_TYPE} may have stopped being tender-stage-only"
            )
        if tender.get("status") != EXPECTED_STATUS:
            raise ValueError(
                f"Public Contracts Scotland release {release.get('id', '?')} has tender.status "
                f"{tender.get('status')!r}, expected {EXPECTED_STATUS!r}"
            )
    return releases


def release_date(release: dict) -> date:
    """The release's own publication date. Stated at midnight UTC on every recorded release."""
    return datetime.fromisoformat(release["date"].replace("Z", "+00:00")).date()


def release_url(release: dict) -> str:
    """The OCDS package's own canonical link. On the api host on every recorded release;
    raises rather than ever returning a link on the robots-disallowed www host."""
    for link in release.get("links") or []:
        if link.get("rel") == "canonical" and link.get("href"):
            href = link["href"]
            if WWW_HOST in href:
                raise ValueError(f"{release.get('id', '?')}: canonical link names the disallowed www host: {href}")
            return href
    raise ValueError(f"{release.get('id', '?')}: release has no canonical link")
