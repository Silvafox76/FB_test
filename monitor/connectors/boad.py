"""BOAD (Banque Ouest-Africaine de Développement) tender notices.

Recorded from real calls on 2026-09-12. The fixtures are
`tests/contract/fixtures/boad.html` (the listing's HTML shell) and
`tests/contract/fixtures/boad.json` (the two paged JSON responses a real run
would read that day). This parser is written against the fields that are
actually in them; see `sources/boad.yaml` for the wider route evidence
(robots.txt, terms of use, the vocabulary count, the Guinea-Bissau gap).

**www.boad.org is Laravel + Vite + Inertia/Vue over a headless WordPress at
admin.boad.org.** The tender listing (`sources/boad.yaml`'s `list_url`) answers
ordinary HTML to a plain GET, and answers `200 application/json` to the same URL
when the request carries `X-Inertia: true` plus a matching `X-Inertia-Version`
header - Inertia's protocol for asking a server-rendered page for its own props
without the page shell around them. `props.tenders` is a standard Laravel
paginator (`current_page`, `per_page: 6`, `total`, `last_page`, `data: [...]`),
paged with `?page=N`.

**This is one deliberate two-request sequence, not a fallback.** The Inertia
version is a hash of the deployed front-end build and it rotates on deploy; a
request carrying a stale one gets a real `409` (confirmed by sending a
deliberately wrong 32-hex-character value against the live endpoint on
2026-09-12, which answered `HTTP/2 409` with an `x-inertia-location` header - the
URL Inertia's own JavaScript client would then do a full page visit to). There is
therefore no way to know the current version without asking, and every run of
this connector does, in this order: GET the listing's own HTML shell, scrape the
version out of its `<div id="app" data-page="...">` attribute, then make the
paged JSON calls with that version. **This is not try-JSON-then-fall-back-to-HTML.**
There is no second parse path anywhere below for a JSON call that fails: a stale
version, a network error, anything - raises exactly the way any other request
failure here would, and the run stops. The HTML-shell request happens first and
unconditionally, every run, because the version genuinely cannot be known any
other way, not because the JSON route is untrusted.

**A measured hazard on a parameter that looked like it should narrow the query at
the source, and does not.** The front end's own filter widgets suggest a
`project_status` query parameter. Sending it - with a real taxonomy slug
(`tender_notice`, `starting`) or a made-up one, both tried against the live
endpoint on 2026-09-12 - does not filter the paginator. It replaces
`props.tenders` with a bare empty list (`[]`) instead of the paginator dict every
other request on this endpoint returns, which is a different and more dangerous
shape than an ignored parameter: `search=<term>` was tried in the same session
and correctly returns a well-shaped, merely empty paginator (`total: 0,
last_page: 1`), so this is specific to `project_status`. Nothing here sends that
parameter for this reason. `parse_tenders_page` raises if `props.tenders` is ever
anything but the paginator shape, which is what would catch this hazard if it
were ever triggered by accident (a stray query string surviving a copy-paste,
say) rather than reading the response as a healthy empty page.

**The scope decision - which `project_status` values are a live opportunity - is
applied here, to the page already fetched, not at the query.** See
`sources/boad.yaml` for the full vocabulary count (six values measured across the
entire 379-record French corpus on 2026-09-12) and why the registry's own
`exclude_notice_types` holds the four decided/non-opportunity values rather than
the two wanted ones: `Source` (`monitor/models.py`) forbids unknown fields, so
this is the field the schema already has, used the way `sources/ted.yaml`,
`sources/doe.yaml` and `sources/ebrd.yaml` all use it. Two things an exclude list
alone cannot catch are handled here instead, the same way
`EBRD_COUNTRY_NAMES` in `monitor/connectors/ebrd.py` is code rather than a YAML
field for a parallel reason (a source-specific vocabulary `Source` has no room
for):

  1. `BOAD_PROJECT_STATUS` is the closed set of every value measured in the whole
     corpus. A value outside it raises rather than being silently kept or
     dropped - BOAD's own taxonomy has grown a term this registry has never
     classified, and that is a person's decision, not a default either way.
  2. 65 of the 379 records carry no `project_status` at all. A live deadline with
     no classification is not enough signal to call a record an opportunity, so
     `is_biddable` requires at least one status and requires it to not be an
     excluded one, not merely "no excluded status present".

**`acf.end_at` is required in addition to the status check, and it is not
redundant with it - measured, not assumed.** 19 of the 379 records carry a
biddable status with `end_at: null` (a data-entry gap in the source, the same
shape `sources/sierra_leone.yaml` documents for two of its own rows), and 24
carry a non-null `end_at` under a status that is not biddable (18 with no status
at all, 6 under `Avis de passation de marché` alone - see `sources/boad.yaml` for
why that one is flagged rather than silently accepted). Both checks are load-
bearing; either alone lets through rows a reviewer cannot act on or should not
see.

**Sorted newest-first by `date_gmt`, verified rather than assumed sound.**
`validate_rows` checks this on every page fetched, the same defence
`monitor/connectors/worldbank.py` and `monitor/connectors/liberia.py` both apply
to their own sorted, unfiltered-by-the-server pages: the window cut below only
reads correctly while the sort holds, and nothing server-side here promises it
does.

**`acf.end_at` and `acf.start_at` are stored exactly as the source writes them,
`DD/MM/YYYY`, and nothing here parses them.** Rule 9's original-language, rule
10's deadline-from-the-original and rule 5's derive-nothing all point the same
way: this connector's job ends at handing the string on. A future
`monitor/normalise/boad.py` reads it.

**No per-tender country field exists in this API at all** - `props.countries`
and `props.domains` are the search page's own filter-option lists, not fields on
a tender record, checked against the full 379-record corpus. `source.covers` is
therefore read for logging only, the same "taken so every connector is built the
same shape" reason `monitor/connectors/liberia.py` accepts and ignores
`cpv_prefixes`; there is nothing in a BOAD response to scope a fetch to a member
state with.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See WINDOW in sources/boad.yaml's health comment: 4 biddable-with-a-deadline
# notices arrived in the 30 days before this connector was built, and BOAD's
# publication rate over nearly three years of history supports nothing tighter
# without reading a zero window most days.
LOOKBACK_DAYS = 30

# The paginator's own page size, measured and not requested: this connector never
# asks for a different one (see the module docstring's project_status hazard for
# why an untested query parameter is not sent here either).
PAGE_SIZE = 6

# The absolute bound on pages walked in one run. At PAGE_SIZE 6 this is 60 rows,
# comfortably above the registry's own expected_max and the measured 60-day
# ceiling of 12 biddable notices; a burst that filled every page with in-window
# rows still stops here rather than walking the whole 64-page, 379-row archive on
# a schedule this source does not need it on.
MAX_PAGES = 10

# The whole vocabulary this connector has ever seen, measured across the full
# 379-record French corpus on 2026-09-12. Not configuration: rule 6 puts
# thresholds and keywords a person tunes in YAML, and nobody tunes what BOAD's own
# CMS calls a taxonomy term. This sits beside sources/boad.yaml's
# exclude_notice_types the way monitor/connectors/ebrd.py's EBRD_COUNTRY_NAMES
# sits beside sources/ebrd.yaml's exclude_notice_types: Source forbids unknown
# fields, so a source's own closed vocabulary that the registry's shared schema
# has no field for lives here instead.
BOAD_PROJECT_STATUS = frozenset(
    {
        "Avis d'appel d'offre",
        "Avis de manifestation d'intérêt",
        "Résultats d'appel d'offre",
        "Résultats de manifestation d'intérêt",
        "Avis de passation de marché",
        "Plan de Passation des Marchés",
    }
)

# Every one of the 379 records measured on 2026-09-12 carried these three values.
# A row with any other value is a changed listing (a draft, or a new content
# type sharing the tender endpoint), not an ordinary variation.
EXPECTED_STATUS = "publish"
EXPECTED_TYPE = "tender"

# The fields the parser reads or the next request needs. Deliberately excludes
# `content` and `excerpt` (null on every record measured; the real body text is
# `acf.presentation.text`) and the `yoast_head*` SEO blocks, which are carried in
# the stored payload as published but are not required for this connector to
# consider a row well-formed.
REQUIRED_ROW_FIELDS = (
    "id",
    "external_id",
    "slug",
    "link",
    "title",
    "date_gmt",
    "modified_gmt",
    "acf",
    "status",
    "type",
)

PUBLIC_BASE_URL = "https://www.boad.org"

# The Inertia client payload's outer shell, e.g.
# `<div id="app" data-page="{&quot;component&quot;:...}">`. HTML-entity-encoded,
# not JSON-escaped, because it is an HTML attribute value; `html.unescape` is what
# turns `&quot;` back into `"` before `json.loads`.
INERTIA_DATA_PAGE = re.compile(r'<div id="app" data-page="(.*?)"\s*>', re.S)


class BoadConnector(FeedConnector):
    """One HTML shell read for its version, then a walk of sorted, paged JSON."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: there is no CPV code anywhere in this source and no per-tender
        # country field either (see the module docstring). Taken so every
        # connector is built the same shape.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest date_gmt this run keeps. Daily schedule, 30-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def inertia_version(self, client: httpx.Client) -> str:
        """The one HTML-shell request every run makes before any JSON call.

        See the module docstring: a stale version gets a real 409, so this is not
        optional and not cached across runs - the shell is read fresh every time.
        """
        response = client.get(self.source.list_url)
        response.raise_for_status()
        return parse_inertia_version(response.text)

    def fetch_page(self, client: httpx.Client, *, version: str, page: int) -> dict:
        response = client.get(
            self.source.list_url,
            params={"page": page},
            headers={"X-Inertia": "true", "X-Inertia-Version": version, "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        version = self.inertia_version(client)

        rows_seen = 0
        in_window: list[dict] = []
        reached_window_end = False
        last_page_read = 0

        for page in range(1, MAX_PAGES + 1):
            document = self.fetch_page(client, version=version, page=page)
            paginator = parse_tenders_page(document)
            rows = validate_rows(paginator["data"])
            rows_seen += len(rows)
            last_page_read = page

            kept = within_window(rows, cutoff)
            in_window.extend(kept)
            reached_window_end = len(kept) < len(rows)
            if reached_window_end or page >= paginator["last_page"]:
                break
        else:
            log.warning("boad_max_pages_reached", fetched_pages=MAX_PAGES, rows_seen=rows_seen)

        if not reached_window_end:
            # The walk ran out of pages before it ran out of window, so the oldest
            # notice in the window may not have been read. Said out loud rather
            # than reported as a complete pass, the same discipline
            # monitor/connectors/worldbank.py and monitor/connectors/liberia.py
            # both log under their own *_window_not_closed events.
            log.info("boad_window_not_closed", rows_seen=rows_seen, cutoff=cutoff.isoformat())

        biddable = [row for row in in_window if is_biddable(row, exclude_notice_types=self.source.exclude_notice_types)]

        ceiling = self.source.expected_max
        if len(biddable) > ceiling:
            log.warning(
                "boad_ceiling_reached",
                biddable=len(biddable),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/boad.yaml",
            )

        raw_notices = [
            self.raw_notice(
                url=PUBLIC_BASE_URL + row["link"],
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in biddable[:ceiling]
        ]

        log.info(
            "boad_fetch",
            pages_read=last_page_read,
            rows_seen=rows_seen,
            in_window=len(in_window),
            biddable=len(biddable),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_inertia_version(markup: str) -> str:
    """The Inertia asset version, scraped from the page shell's own `data-page`.

    See the module docstring: this is the one thing that has to be read fresh
    before any JSON call, because a stale value gets a real 409 rather than
    being ignored or negotiated.
    """
    match = INERTIA_DATA_PAGE.search(markup)
    if match is None:
        raise ValueError("BOAD listing page has no Inertia data-page attribute; the page shell changed")

    document = json.loads(html.unescape(match.group(1)))
    version = document.get("version")
    if not version:
        raise ValueError(f"BOAD data-page carries no version; got top-level keys {sorted(document)}")
    return version


def parse_tenders_page(document: dict) -> dict:
    """`props.tenders`, checked for the paginator shape the window cut depends on.

    Two checks, in the order a failure is cheapest to explain:

      1. That the container is there at all, so a renamed prop raises instead of
         yielding zero rows.
      2. That it is the paginator dict (`current_page`, `data`, `last_page`, ...)
         and not the bare list the measured `project_status` hazard in the module
         docstring returns instead. Nothing here sends that parameter, but this
         is the check that would catch it if a stray one ever survived into a
         request.
    """
    props = document.get("props")
    if not isinstance(props, dict) or "tenders" not in props:
        raise ValueError(f"BOAD response has no props.tenders; got top-level keys {sorted(document)}")

    tenders = props["tenders"]
    if not isinstance(tenders, dict) or "data" not in tenders or "last_page" not in tenders:
        raise ValueError(
            f"BOAD props.tenders is not the paginator shape (a dict with 'data' and 'last_page'); got "
            f"{type(tenders).__name__}. A filtered request to this endpoint can answer with a bare list "
            "instead - see the module docstring's project_status hazard."
        )
    return tenders


def validate_rows(rows: list[dict]) -> list[dict]:
    """Every row of one page, checked for shape, vocabulary and sort order.

    Three checks, in the order a failure is cheapest to explain:

      1. The fields the mapper and the next request need, so a renamed field
         raises instead of yielding fewer notices.
      2. `status` and `type`, both a closed set of one value measured across the
         whole 379-record corpus; anything else is a changed listing.
      3. `project_status`, against BOAD_PROJECT_STATUS - a value this connector
         has never seen is a person's decision (see the module docstring), not a
         silent keep or drop.

    Then the sort every window cut in this connector depends on: `date_gmt`
    descending, the same defence monitor/connectors/worldbank.py and
    monitor/connectors/liberia.py both apply to their own unfiltered, sorted
    pages.
    """
    for position, row in enumerate(rows):
        missing = [field for field in REQUIRED_ROW_FIELDS if field not in row]
        if missing:
            raise ValueError(f"BOAD tender {row.get('id', position)} is missing {missing}")

        if row["status"] != EXPECTED_STATUS:
            raise ValueError(
                f"BOAD tender {row['id']} has status {row['status']!r}, not {EXPECTED_STATUS!r}; every one of "
                "the 379 tenders measured on 2026-09-12 was published, so this is a changed listing rather "
                "than an ordinary variation"
            )
        if row["type"] != EXPECTED_TYPE:
            raise ValueError(f"BOAD tender {row['id']} has type {row['type']!r}, not {EXPECTED_TYPE!r}")

        names = [entry.get("name") for entry in row.get("project_status") or []]
        unknown = [name for name in names if name not in BOAD_PROJECT_STATUS]
        if unknown:
            raise ValueError(
                f"BOAD tender {row['id']} carries project_status {unknown!r}, which is none of the "
                f"{sorted(BOAD_PROJECT_STATUS)} values measured across the whole 379-tender corpus on "
                "2026-09-12; BOAD's own taxonomy has grown a term this connector has never classified, and a "
                "person must decide whether it is biddable before sources/boad.yaml's exclude_notice_types "
                "can be trusted again"
            )

    dates = [record_date(row) for row in rows]
    for position, (newer, older) in enumerate(zip(dates, dates[1:], strict=False)):
        if older > newer:
            raise ValueError(
                f"BOAD page is not sorted by date_gmt descending: row {position + 1} ({older.isoformat()}) is "
                f"newer than row {position} ({newer.isoformat()}); the window cut would under-read"
            )
    return rows


def is_biddable(row: dict, *, exclude_notice_types: list[str]) -> bool:
    """Whether this row is an open opportunity: see sources/boad.yaml for the count.

    By the time this runs, `validate_rows` has already confirmed every status
    name is one of BOAD_PROJECT_STATUS, so no membership check is repeated here -
    only the registry's own exclude list and the deadline.
    """
    names = [entry.get("name") for entry in row.get("project_status") or []]
    if not names:
        return False
    if any(name in exclude_notice_types for name in names):
        return False
    return bool((row.get("acf") or {}).get("end_at"))


def within_window(rows: list[dict], cutoff: date) -> list[dict]:
    """The rows from the top of a page sorted newest-first down to the first old one.

    Everything below that first old row is older still, which is the property
    validate_rows's sort check defends.
    """
    kept: list[dict] = []
    for row in rows:
        if record_date(row) < cutoff:
            break
        kept.append(row)
    return kept


def record_date(row: dict) -> date:
    """`date_gmt`, e.g. "2026-09-11T18:15:56.000000Z". Raises on anything else.

    Every one of the 379 records measured on 2026-09-12 carried this field, so its
    absence or an unparseable value is a changed API, not a notice to skip.
    """
    raw = row.get("date_gmt")
    if not raw:
        raise ValueError(f"BOAD tender {row.get('id', '?')} has no date_gmt")
    try:
        return datetime.fromisoformat(raw).date()
    except ValueError as cause:
        raise ValueError(f"BOAD tender {row.get('id', '?')} has date_gmt {raw!r}, not ISO 8601: {cause}") from cause
