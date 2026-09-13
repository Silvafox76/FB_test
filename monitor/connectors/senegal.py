"""Senegal APPEL tenders, `GET https://api.achatspublics.sn/anon/tdo?size=200`.

Recorded from a real call on 2026-09-13. See `sources/senegal.yaml` for how this
route was found (the achatspublics.sn front end is a JS-only Nuxt SPA; the JSON
this connector reads is the same call the SPA's own compiled bundle makes to
`api.achatspublics.sn`, a second host whose `/anon/**` namespace is a deliberate
public carve-out from an otherwise Bearer-guarded API).

**One request, one endpoint, no detail fetch.** Unlike `monitor/connectors/
liberia.py` and `monitor/connectors/ebrd.py`, the listing row here already carries
everything a `Notice` needs - title, buyer, both dates, the reference, the market
type - so there is no second call to make. The registry records that a per-record
detail route exists (`GET /anon/tdo/{uid}`) and states plainly that it answers
"the same shape as the listing row"; fetching it would be a second endpoint for no
new field, which rule 1 forbids and rule 21's one-polite-pass budget does not need.
`RawNotice.url` is built from that confirmed route without calling it, the same
way `monitor/connectors/worldbank.py` builds `NOTICE_URL` from a listing row's id
without ever fetching that public page itself.

**Pagination hazard, measured in the registry and not re-litigated here.**
`?page=`, `?pageNumber=` and `?page=&sort=` are all silently ignored; the whole
archive - 35 rows on 2026-09-13 - arrives in one response as long as `size` is
generous enough, and `data.totalPages` says whether it still is. This connector
asks for `size=200`, comfortably above the archive's current size, and raises
rather than reading a partial archive if `totalPages` is ever anything but 1, or
if `data.numberOfElements` or `data.totalElements` ever disagree with the length
of `data.content` actually returned - the same discipline
`monitor/connectors/liberia.py`'s module docstring describes for its own
one-page-is-the-whole-archive read.

**The rows are not sorted.** `data.sort` is an empty list and `data.pageable.sort`
agrees; the 35 recorded `publicationDate` values are in no date order at all
(measured: neither ascending nor descending). So, exactly as in
`monitor/connectors/ebrd.py`, every row is read and the window is a filter applied
to the whole response, not a cut at the first old row.

**Window: 30 days**, matching the health block's own assumption in
`sources/senegal.yaml` rather than the daily schedule the source runs on. The
registry's own rolling-window measurement over the archive's lifetime found a
7-day window zero on 174 of 306 days (57%) and even a 30-day window zero on 38 of
306 (about 12%) - a daily window would read as unhealthy on most days by design,
not because anything failed, so this connector reads the wider window
`sources/senegal.yaml`'s `expected_items_per_run` was actually measured against.

**Every field a row carries, with one real example value from uid
`45824991-595e-4570-ae89-3c6e3abf61a3`** (see `tests/contract/fixtures/senegal.json`):

    uid                      "45824991-595e-4570-ae89-3c6e3abf61a3"  (external id)
    reference                "T_MDE_DSI_627"
    libelle                  "[Marché de test ] Rénovation du bâtiment des archives"  (title)
    description              null on all 35 rows in hand
    direction.libelle        "Direction des Systèmes d'information"  (buyer sub-unit)
    organization.libelle     "Ministère de la dématerialisation (TESTS APPEL)"  (BUYER - see below)
    acType.libelle           "État (Administration centrale)"
    passationMode.libelle    "Appel d'offres ouvert"
    marketType.libelle       "Travaux"  (closed set on this archive: Services, Fourniture, Travaux)
    submissionMode           "ELECTRONIC"
    obtainingMode            "ONLINE"
    createdAt                "2025-11-18T16:31:34.210+00:00"
    updatedAt                "2025-12-23T12:27:36.602+00:00"
    publicationDate          "2025-11-18T16:37:40.481+00:00"  (PUBLICATION DATE - see below)
    submissionDate           "2025-11-18T17:00:00.000+00:00"  (DEADLINE - see below)
    openingDate              "2025-11-18T17:00:00.000+00:00"  (bid opening; equal to submissionDate on this row)
    submissionPlace          null on all 35 rows in hand
    openingPlace             null on all 35 rows in hand
    tenderNoticeFile         null on all 35 rows in hand (sources/senegal.yaml's own finding)
    expressedInterest        false
    expressedInterestCount   2
    submissionCount          1
    pendingQuestionsCount    0
    questionsCount           1
    isFree                   true
    remainingTime            -25823782713  (milliseconds; already negative, i.e. past)
    status                   "PUBLISHED"  (the only value on all 35 rows in hand)
    apd                      false
    supplierProperties       null on 22 of 35 rows; where present, a list of
                             eligible-bidder-size categories such as
                             "Petite entreprise (PE)" - institutional eligibility
                             criteria, not a person or a firm's own submission
    cible                    "NATIONAL" (6 of 35; 29 are "COMMUNITY" - see
                             sources/senegal.yaml's note on this field)

**BUYER** is `organization.libelle`, the contracting institution
("Ministère de la dématerialisation (TESTS APPEL)", "SENELEC", "Ville de DAKAR",
etc. - 14 distinct organizations across the 35 rows). `direction.libelle` is a
narrower sub-unit within that organization and is not read as the buyer here.

**DEADLINE** is `submissionDate`, ISO 8601 with milliseconds and a UTC offset
(`YYYY-MM-DDTHH:MM:SS.sss+00:00`, e.g. `"2025-11-18T17:00:00.000+00:00"`), present
and non-null on all 35 rows in hand. `openingDate`, in the same format, is the bid
opening and is not the deadline; on every row sampled it equals `submissionDate`,
but nothing here assumes that always holds. **PUBLICATION DATE** is
`publicationDate`, the same format, also present and non-null on all 35 rows -
this is the field the window cut filters on.

**PERSONAL DATA: none found.** Every row in the recorded response was searched for
an email address and for phone-number-shaped digit runs; nothing matched (a few
UUID and millisecond-timestamp fragments look like phone numbers on a naive scan
and were checked by hand - none is one). `organization`, `direction`, `acType`,
`marketType` and `passationMode` are all institutional reference data with a
`uid`/`libelle`/`code` shape and no named-person field anywhere. `supplierProperties`
names eligible bidder categories ("Petite entreprise (PE)", "Groupement d'Intérêt
Économique (GIE)"), not named suppliers. There is nothing here for the normaliser
to strip before a model call (rule 19) - unlike `monitor/connectors/liberia.py`'s
`parties[].contactPoint`, this source's listing row carries no contact field at
all.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See WINDOW in the module docstring: matches the health block's own assumption in
# sources/senegal.yaml rather than the daily schedule this source runs on.
LOOKBACK_DAYS = 30

# The registry's own pagination note: the whole archive (35 rows on 2026-09-13)
# fits in one response as long as `size` is generous, and generous is checked
# against `totalPages` on every call rather than assumed to still hold.
PAGE_SIZE = 200

# The fields every row must carry for this connector to use it at all. Measured
# present (though not always non-null further down) on all 35 rows in hand; see
# the module docstring's field table for what null means on each one.
REQUIRED_FIELDS = ("uid", "libelle", "reference", "organization", "publicationDate", "submissionDate", "status")

# The only status value measured across the 35-row archive on 2026-09-13.
EXPECTED_STATUS = "PUBLISHED"

# The confirmed-live per-record route named in sources/senegal.yaml
# ("GET /anon/tdo/{uid}, same shape as the listing row"). Not fetched by this
# connector - see the module docstring - only used to build a stable per-notice
# URL, the same relationship monitor/connectors/worldbank.py's NOTICE_URL has to
# its listing rows.
NOTICE_URL = "https://api.achatspublics.sn/anon/tdo/{uid}"


class SenegalConnector(FeedConnector):
    """One generously-sized page, read as the whole archive, filtered client-side."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: no CPV or UNSPSC code appears on any row in this source (see
        # sources/senegal.yaml), so every notice reaches the lexicon stage. Taken
        # so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest publicationDate this run keeps. Daily schedule, 30-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        response = client.get(self.source.api_url, params={"size": PAGE_SIZE})
        response.raise_for_status()
        document = response.json()

        rows = parse_response(document)
        wanted = within_window(rows, cutoff)

        ceiling = self.source.expected_max
        if len(wanted) > ceiling:
            log.warning(
                "senegal_ceiling_reached",
                in_window=len(wanted),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/senegal.yaml",
            )

        raw_notices = [
            self.raw_notice(
                url=NOTICE_URL.format(uid=row["uid"]),
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in wanted[:ceiling]
        ]

        log.info(
            "senegal_fetch",
            archive_rows=len(rows),
            in_window=len(wanted),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_response(document: dict) -> list[dict]:
    """The archive's rows, checked for shape before anything is filtered.

    Four checks, in the order a failure is cheapest to explain:

      1. `success` is true, so a documented-but-unsuccessful response (an error
         wrapped in a 200, the shape this API itself uses) raises rather than
         being read as an empty archive.
      2. `data` and `data.content` are the container this connector expects and
         `content` is a list, so a renamed key raises instead of yielding zero rows.
      3. `data.totalPages` is 1, the one-page-fits-all assumption this connector is
         built on (see the module docstring's PAGINATION HAZARD). More than one
         page means PAGE_SIZE no longer covers the archive and the rest is unread.
      4. `data.totalElements` and `data.numberOfElements` both equal the number of
         rows actually returned, the same cross-check
         `monitor/connectors/liberia.py` and `monitor/connectors/ebrd.py` apply in
         their own shape: a mismatch means this response is a partial one dressed
         as a complete one.

    Every row is then checked for REQUIRED_FIELDS and for the one status value
    measured on this archive, the same way `monitor/connectors/liberia.py` checks
    its own closed-vocabulary status field.
    """
    if not isinstance(document, dict) or document.get("success") is not True:
        raise ValueError(
            f"Senegal /anon/tdo response was not successful: success={document.get('success')!r}, "
            f"message={document.get('message')!r}"
        )

    data = document.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        raise ValueError(f"Senegal /anon/tdo response has no data.content list; got {sorted(document)}")

    rows = data["content"]

    if data.get("totalPages") != 1:
        raise ValueError(
            f"Senegal /anon/tdo has {data.get('totalPages')!r} pages, not 1; the one-page-fits-all "
            f"assumption in sources/senegal.yaml no longer holds and PAGE_SIZE must be raised"
        )
    if data.get("totalElements") != len(rows) or data.get("numberOfElements") != len(rows):
        raise ValueError(
            f"Senegal /anon/tdo reports totalElements={data.get('totalElements')!r}, "
            f"numberOfElements={data.get('numberOfElements')!r}, but content has {len(rows)} rows; "
            "this response is a partial archive"
        )

    for position, row in enumerate(rows):
        missing = [field for field in REQUIRED_FIELDS if not row.get(field)]
        if missing:
            raise ValueError(f"Senegal row {position} ({row.get('uid', '?')}) is missing {missing}")
        if row["status"] != EXPECTED_STATUS:
            raise ValueError(
                f"Senegal row {row['uid']} has status {row['status']!r}, not {EXPECTED_STATUS!r}; "
                f"{EXPECTED_STATUS!r} is the only value measured across the recorded archive, so an "
                "unrecognised one is a changed API, not a row to skip"
            )
    return rows


def within_window(rows: list[dict], cutoff: date) -> list[dict]:
    """The rows whose publicationDate falls on or after the cutoff.

    Every row is checked. `data.sort` is empty and the recorded 35
    `publicationDate` values are in no date order at all (measured, neither
    ascending nor descending), so this is a filter over the whole response, not a
    cut at the first old row - the same reasoning
    `monitor/connectors/ebrd.py` applies to its own unsorted archive.
    """
    return [row for row in rows if publication_date(row) >= cutoff]


def publication_date(row: dict) -> date:
    """A row's publicationDate, ISO 8601 with milliseconds and a UTC offset, as a date.

    Raises on a missing or unparseable value rather than skipping the row:
    publicationDate was present and well-formed on all 35 rows measured, so
    trouble here is a changed API, not an empty field.
    """
    raw = row.get("publicationDate")
    if not raw:
        raise ValueError(f"Senegal row {row.get('uid', '?')} has no publicationDate")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as cause:
        raise ValueError(f"Senegal row {row.get('uid', '?')} has publicationDate {raw!r}: {cause}") from cause
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).date()
