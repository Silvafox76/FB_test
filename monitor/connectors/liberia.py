"""Liberia PPCC e-GP OCDS records, POST /ocds/record/searchRecords.action.

**Built as a `FeedConnector`, not the "simpler Page connector" BUILD_ORDER step 18
names.** That line predates the route this connector actually reads. Two HTML
listings were probed first, by CLAUDE.md's own access-route order, and both were
ruled out (see `sources/liberia.yaml`'s ROUTES CONSIDERED). What answers instead is
PPCC's e-GP platform's own OCDS "Visualisation" web app: an unauthenticated JSON
search endpoint plus a per-record JSON download, structured data by the letter of
the standard it names itself after. Recorded from real calls on 2026-09-12.

**No login, no cookie, checked with a bare `curl` and no prior cookie jar on both
endpoints.** The registry's evidence holds: a fresh request with an empty cookie
jar gets the same `{"total": 1385, "items": [...]}` a session-carrying one does,
and the same is true of `downloadRecord`.

**Two measured hazards, and they cut in opposite directions.**

  1. `sortField` and `sortDir` are not optional in the way an unrecognised value
     is: leaving either one out entirely 500s (`General Error / Server Encountered
     An Unexpected Condition`). This connector always sends both.
  2. A *present but wrong* `sortField` does not 500 and does not fall back to any
     sort a person would call sensible - it 200s with a page that is genuinely
     unsorted (measured: `sortField=bogusField` returns rows spanning
     2025-01-05 to 2025-12-08 in no date order at all, not even reverse). So the
     one sort this connector relies on - `creationDate desc` - is verified against
     the page it actually got back, not trusted because it was asked for. This is
     the same shape of hazard `monitor/connectors/worldbank.py` defends against,
     on the parameter that matters here instead of on `noticedate`.

`creationDate` was chosen as both the sort key and the window field because it is
the only date the search page's own column exposes a tooltip meaning for: "the date
and time the first release was published for this OCID." `processDate` ("the date
and time the latest releases were processed") is null on 251 of the 1,385 records
and answers a different question - when the system last touched the record, not
when the notice was first public - so it is read from neither.

**There is no modified-since or date-range parameter.** The search form the
Angular client renders (`recordGrid.action`) has exactly one filter field, `ocid` -
useful for looking up one known record, not for windowing a feed - and no other
input has a `name` at all. Sorting is the only narrowing this API supports, so it
is the narrowing used: `creationDate desc`, cut client-side at the window, the same
shape `monitor/connectors/worldbank.py` uses for the same reason.

**WINDOW.** Measured over the 32 days before this connector was built (2026-08-11
to 2026-09-11): 0 to 14 notices a day, 10 of the 32 days zero, rolling 7-day sums 8
to 44 (never zero). A daily cron (`0 11 * * *` in `sources/liberia.yaml`) reading a
single day would read a legitimate zero on nearly a third of days, which
`monitor/health/source_health.py` cannot tell apart from an outage. Seven days is
the shortest window the measured data supports never reading empty, so it is the
one used; re-reading a week costs nothing extra server-side (one request, sorted,
cut client-side) and downstream deduplication is by content hash, not by this
connector remembering what it saw yesterday.

`PAGE_SIZE` is 60, the registry's own `expected_max` and comfortably above the
measured 7-day ceiling of 44. One page has been enough in every measurement taken;
if a page ever comes back exactly full of in-window rows, that is a burst large
enough that the true edge of the window was not reached, and this connector says so
rather than reporting a quietly short week.

**Status is a closed vocabulary of one, measured, not assumed:** every one of the
1,385 records in the corpus on 2026-09-12 is `"GENERATED"`. The listing tooltip
lists `NEW` and `UPDATED` as other valid values, for a record whose compiled release
may not exist yet - `compiledRelease` and `versionedRelease` are `null` in every
listing row regardless of status, which is why the compiled release is always
fetched separately rather than trusted from the listing. An unrecognised status
raises rather than being downloaded blind, since nothing here has ever seen what a
non-`GENERATED` download answers.

**Every field this connector requires was checked against 29 releases spread across
the full corpus** - the 14 inside this run's own window, 6 more from the rest of
one recent page, and 10 more sampled from evenly spaced positions across all 1,385,
including the very oldest and very newest. All 29 carry a `tender` block with
`title`, `description`, `procuringEntity`, `tenderPeriod`, `value`,
`procurementMethod` and at least one item; the oldest sampled record additionally
carries `awards` (its tender is `"complete"`, not `"active"`), and one carries
`tender.status: "complete"` with no other shape change. Nothing sampled lacked a
`tender` block outright, so none is treated as optional the way
`monitor/connectors/worldbank.py` and `monitor/connectors/ebrd.py` treat a General
Procurement Notice's missing fields - there is no evidence yet of a Liberia record
shaped that way, and requiring these fields is what makes it loud the day one
appears rather than mapped as if it were an ordinary tender.

**Every item is classified ISIC, never CPV** (`tender.items[].classification.scheme
== "ISIC"` on every sampled release), which is `sources/liberia.yaml`'s own note and
the reason `cpv_prefixes` is accepted and unused here: every notice reaches the
lexicon stage rather than the free CPV filter, the same as
`monitor/connectors/ebrd.py` and `monitor/connectors/worldbank.py`.

**`release["language"]` is `"en"`, lowercase, on all 29 sampled releases** - not to
be confused with `tender.documents[].language`, which is `"EN"`, uppercase, where a
document exists at all (absent on roughly a third of the sample). Both are read by
this connector's checks; neither is rewritten, because rule 9 makes original text
the record and choosing between them is the normaliser's job, not this one's.

**`parties[].contactPoint` carries real personal contact details, not just
institutional ones**, the same hazard the Nigeria onboarding found. Most buyer
contacts here are an office line and a `.gov.lr` address, but at least one sampled
record gives a named individual's personal Gmail address as the sole contact for a
national agency, and an older record outside this run's window
(`ocds-dwjm7l-253017`, not in the fixture) lists two competing suppliers by name
with their own personal Gmail addresses and phone numbers as `tenderer` parties.
This connector does not strip that block - rule 5 leaves what becomes `Notice.body`
to the normaliser, and rule 9 makes the record what was published - but whoever
writes `monitor/normalise/liberia.py` next must not let `parties[].contactPoint` or
`tender.tenderers` reach a model call (rule 19). `tests/contract/test_liberia.py`
redacts these values in the committed fixture; see its docstring for which ones and
why.

There is no per-notice public page to link to: the OCDS Visualisation app's only
per-record view is a Bootstrap modal that renders JSON client-side
(`viewDataInModal` in `RecordGridCtrl.js`), not a route. `RawNotice.url` is the
`downloadRecord` endpoint itself, the one URL that actually answers with this
notice and nothing else.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See WINDOW in the module docstring: the shortest window the measured 32-day
# corpus never reads as an empty success.
LOOKBACK_DAYS = 7

# The registry's own expected_max (sources/liberia.yaml), comfortably above the
# measured 7-day ceiling of 44. See the module docstring for what a full page means.
PAGE_SIZE = 60

# The absolute bound on detail requests in one run, above the registry's
# expected_max. A burst that filled the window with in-scope notices still stops
# here rather than downloading past what one polite pass should (rule 21).
MAX_DETAIL_REQUESTS = 60

# The sort this connector relies on and verifies. See hazard 2 in the module
# docstring: a wrong value here would not fail loudly on its own.
SORT_FIELD = "creationDate"
SORT_DIR = "desc"

# The fields a search result row must carry for this connector to use it at all.
REQUIRED_LISTING_FIELDS = ("id", "ocid", "status", "creationDate")

# The one status value measured across the whole 1,385-row corpus on 2026-09-12.
# See the module docstring: NEW and UPDATED are the platform's other documented
# values, for a record whose compiled release may not exist to download yet.
EXPECTED_STATUS = "GENERATED"

# https://eprocurement.ppcc.gov.lr/ocds/record/downloadRecord/{id}/COMPILED.action,
# verified 2026-09-12. Not in sources/liberia.yaml: the Source model has one
# api_url per source and this is a second, per-record URL built from the first
# response, the same relationship NOTICE_URL has to list_url in
# monitor/connectors/ebrd.py and monitor/connectors/worldbank.py.
DOWNLOAD_URL = "https://eprocurement.ppcc.gov.lr/ocds/record/downloadRecord/{id}/COMPILED.action"

# The tender fields every one of the 29 sampled releases carries. See the module
# docstring for the spread sampled and why nothing here is treated as optional yet.
REQUIRED_TENDER_FIELDS = (
    "title",
    "description",
    "procuringEntity",
    "tenderPeriod",
    "value",
    "procurementMethod",
    "items",
)

# The only classification scheme measured on any item, on any of the 29 sampled
# releases. sources/liberia.yaml's basis for reaching the lexicon stage rather than
# the free CPV filter (see module docstring).
ITEM_CLASSIFICATION_SCHEME = "ISIC"


class LiberiaConnector(FeedConnector):
    """One sorted search page, then a compiled-release download per in-window row."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: every item here is classified ISIC, never CPV, so every notice
        # reaches the lexicon stage. Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest creationDate this run keeps. Daily schedule, 7-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def search(self, client: httpx.Client) -> dict:
        """The one page this run reads, sorted newest-first by creationDate.

        sortField and sortDir are both always sent: leaving either out 500s (see
        the module docstring), and a bogus-but-present sortField 200s with an
        unsorted page, which is why the response is checked rather than trusted.
        """
        response = client.post(
            self.source.api_url,
            json={"page": 1, "pagesize": PAGE_SIZE, "sortField": SORT_FIELD, "sortDir": SORT_DIR},
        )
        response.raise_for_status()
        return response.json()

    def fetch_release(self, client: httpx.Client, row: dict) -> dict:
        """One record's full downloaded package, checked against the listing row.

        The ocid cross-check is the only thing standing between this connector and
        a download endpoint that silently answered for the wrong record: there is
        no server-side filter here to hold to account, the way
        monitor/connectors/worldbank.py holds its query parameters to account, so
        the notice's own stated ocid is checked against what the listing named.
        """
        response = client.get(DOWNLOAD_URL.format(id=row["id"]))
        response.raise_for_status()
        document = response.json()
        parse_release(document, notice_id=row["id"], expected_ocid=row["ocid"])
        return document

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        document = self.search(client)
        rows = parse_search_response(document)
        wanted = within_window(rows, cutoff)

        if wanted and len(wanted) == len(rows):
            # The whole page was inside the window: the true edge was not reached,
            # so this is said out loud rather than reported as a complete pass -
            # the same reasoning monitor/connectors/worldbank.py logs as
            # worldbank_window_not_closed.
            log.info(
                "liberia_window_not_closed",
                page_size=PAGE_SIZE,
                fetched=len(wanted),
                cutoff=cutoff.isoformat(),
            )

        ceiling = min(self.source.expected_max, MAX_DETAIL_REQUESTS)
        if len(wanted) > ceiling:
            log.warning(
                "liberia_ceiling_reached",
                in_window=len(wanted),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/liberia.yaml",
            )

        raw_notices: list[RawNotice] = []
        for row in wanted[:ceiling]:
            release_document = self.fetch_release(client, row)
            payload = {"listing": row, "detail": release_document}
            raw_notices.append(
                self.raw_notice(
                    url=DOWNLOAD_URL.format(id=row["id"]),
                    payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
            )

        log.info(
            "liberia_fetch",
            page_rows=len(rows),
            in_window=len(wanted),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_search_response(document: dict) -> list[dict]:
    """The page's rows, checked for shape, required fields and the sort relied on.

    Three checks, in the order a failure is cheapest to explain:

      1. That the response is the container this connector expects, so a renamed
         key raises instead of yielding zero rows.
      2. That every row carries what the window cut and the next request need.
      3. That the page is actually sorted newest-first by creationDate. See hazard
         2 in the module docstring: a wrong sortField 200s with an unsorted page,
         so this is checked on the response received, not assumed from the request
         sent.
    """
    if "items" not in document:
        raise ValueError(f"Liberia search response has no 'items' key; got {sorted(document)}")

    rows = document["items"]
    for position, row in enumerate(rows):
        missing = [field for field in REQUIRED_LISTING_FIELDS if field not in row]
        if missing:
            raise ValueError(f"Liberia search result {position} ({row.get('id', '?')}) is missing {missing}")
        if row["status"] != EXPECTED_STATUS:
            raise ValueError(
                f"Liberia record {row['id']} has status {row['status']!r}, not {EXPECTED_STATUS!r}; "
                "the platform's other documented values (NEW, UPDATED) may mean the compiled release "
                "is not generated yet, so downloading it blind is not safe until that is checked"
            )

    dates = [creation_date(row) for row in rows]
    for position, (newer, older) in enumerate(zip(dates, dates[1:], strict=False)):
        if older > newer:
            raise ValueError(
                f"Liberia search results are not sorted by creationDate descending: row {position + 1} "
                f"({older.isoformat()}) is newer than row {position} ({newer.isoformat()}); sortField may have "
                "been ignored and the window cut would under-read"
            )
    return rows


def within_window(rows: list[dict], cutoff: date) -> list[dict]:
    """The rows from the top of the sorted page down to the first older one.

    Everything below that first old row is older still, which is the property the
    sort check in parse_search_response defends.
    """
    kept: list[dict] = []
    for row in rows:
        if creation_date(row) < cutoff:
            break
        kept.append(row)
    return kept


def creation_date(row: dict) -> date:
    """A listing row's creationDate, epoch milliseconds UTC, as a date.

    Unlike the string dates monitor/connectors/ebrd.py and
    monitor/connectors/worldbank.py parse, this is an unambiguous integer
    timestamp, so there is no day/month ordering to guess at. Raises on a missing
    value rather than skipping the row: creationDate was present on all 1,385
    records measured, so its absence is a changed API, not an empty field.
    """
    raw = row.get("creationDate")
    if raw is None:
        raise ValueError(f"Liberia record {row.get('id', '?')} has no creationDate")
    return datetime.fromtimestamp(raw / 1000, tz=UTC).date()


def parse_release(document: dict, *, notice_id: str, expected_ocid: str) -> dict:
    """The downloaded record's one compiled release, checked for shape and identity.

    See REQUIRED_TENDER_FIELDS in the module docstring for what this requires and
    what it was measured against.
    """
    releases = document.get("releases")
    if not releases:
        raise ValueError(f"Liberia record {notice_id} download has no releases; the record page changed")

    release = releases[0]
    if release.get("ocid") != expected_ocid:
        raise ValueError(
            f"Liberia record {notice_id} downloads ocid {release.get('ocid')!r}, but the listing named "
            f"{expected_ocid!r}; the download endpoint answered for the wrong record"
        )
    if not release.get("language"):
        raise ValueError(f"Liberia record {notice_id} has no language field")

    tender = release.get("tender")
    if not tender:
        raise ValueError(f"Liberia record {notice_id} has no tender block; the record page changed")

    missing = [field for field in REQUIRED_TENDER_FIELDS if not tender.get(field)]
    if missing:
        raise ValueError(f"Liberia record {notice_id} tender is missing {missing}")

    for item in tender["items"]:
        scheme = (item.get("classification") or {}).get("scheme")
        if scheme != ITEM_CLASSIFICATION_SCHEME:
            raise ValueError(
                f"Liberia record {notice_id} item {item.get('id', '?')} is classified {scheme!r}, not "
                f"{ITEM_CLASSIFICATION_SCHEME!r}; sources/liberia.yaml's basis for reaching the lexicon "
                "stage rather than the free CPV filter no longer holds"
            )
    return release
