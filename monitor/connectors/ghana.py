"""GHANEPS Current Tenders, `GET /epps/quickSearchAction.do?searchSelect=6`.

Recorded from real, anonymous, cookie-free GETs on 2026-09-13. See
`sources/ghana.yaml` for the full route assessment: the OCDS records API this
host also ships (same European Dynamics product family as
`monitor/connectors/liberia.py`'s Liberia instance) 500s on every body shape
tried, so it is not "the first route that works" and this HTML listing is used
instead. `access: public_listing` / `connector: PageConnector` describes a
plain, unauthenticated HTML page read with `httpx` and `selectolax` and no
browser, the same shape `monitor/connectors/ebrd.py` and
`monitor/connectors/sierra_leone.py` are built to, just paginated where those
two are not.

**No cookie, no login, no JavaScript, reproduced.** Three separate GETs, each
a fresh TCP connection with no cookie jar carried between them, all returned
the same "Current Tenders" table (69 results, page 1 of 7). The only byte-level
difference across the three was the page's own live clock
(`<p class="Time">HH:MM:SS GMT</p>`), which nothing here reads. Page 2 was
fetched the same way - no cookie, no prior request - and answered with rows
11-20, so whatever holds this listing's state lives entirely server-side and
keyed by the URL, not by a session.

**Pagination uses a dynamic-looking parameter name that measured stable.**
GHANEPS's "Next" link reads `?d-3680175-p=2&searchSelect=6` - `d-3680175` looks
like a per-request Struts DataTable id, the kind of thing that could easily be
session-scoped. It was not: three independent, cookie-free fetches of page 1,
spread over several minutes, all named the same `d-3680175-p=2` in their own
"Next" link, and a fresh, cookie-free GET of that exact URL correctly returned
"Page 2 of 7". This connector relies on that id being a fixed property of this
deployment rather than of a session - and does not trust it blindly: every
fetched page's own "Page N of M" text is checked against the page number this
connector asked for (`_require_page_matches`), so a redeploy that changes the
id would make page 2 come back as page 1 again, and that mismatch raises
rather than silently reading the same ten rows twice.

**Window: 7 days, and this is not a retry, the same distinction
`monitor/connectors/ted.py` and `monitor/connectors/simap.py` draw for their
own paging.** Measured on the recorded pages: all 10 rows on page 1 fall
inside the 7-day window ending 2026-09-13 (cutoff 2026-09-06T00:00:00Z), so a
second page is fetched; on page 2, the first 6 rows (11-16) are still inside
the window and the 7th (row 17, published 2026-09-05) is not, so the fetch
stops there. That is 16 in-window rows from 2 pages - the exact count
`sources/ghana.yaml`'s VOLUME section measured, and inside its
`expected_items_per_run` band of [5, 35]. `MAX_PAGES` bounds this at 5 pages
(50 rows, comfortably above the registry's 35-item ceiling) so a sudden burst
still stops well short of walking the whole 7-page, 69-row archive - one
polite pass reads only as much of the listing as this run's window needs.

**A changed page raises rather than reading fewer rows or reading page 1
twice.** `parse_listing_page` requires `table#T01` and its own pagination
block to be present, requires every row to have exactly nine cells, requires
the title link's and the notice PDF link's `resourceId` to agree (the same
cross-check `monitor/connectors/liberia.py` makes between a listing row's
`ocid` and its download), and requires `status` to be the one value measured
across all 20 recorded rows ("Bid Submission"). A GHANEPS session or proxy
hiccup that redirects this URL to the portal's home page - the transient
`sources/ghana.yaml` describes seeing on 2026-09-12 - lands on a page with no
`table#T01` at all, which raises here rather than being read as a healthy
zero-row page. No retry is built for that case: `monitor/models.py`'s `Source`
has no field for a source-declared retry count, so there is nothing for this
connector to consult (rule 2), and rule 1 forbids a second request shape
invented to work around it. If GHANEPS's flakiness turns out to need a retry,
that is a registry decision (a field on `Source` and a documented count), not
a connector improvising one.

**Every field a row carries, with one real example from resourceId 3403640**
(page 1, row 1; see `tests/contract/fixtures/ghana.html`):

    title              "Construction of Akim Oda Branch Manager's bungalow
                        at Akim Swedru"   (`<a>` text, cell 2)
    procuring_entity   "Social Security And National Insurance Trust"  (buyer,
                        cell 3, plain text)
    description        "Construction of Akim Oda Branch Manager's bungalow at
                        Akim Swedru."   (the "Info" column's `<img title=...>`,
                        cell 4 - sometimes a repeat of the title, sometimes
                        several sentences longer; see the module's own sample
                        of 20 rows, none of which named a person)
    deadline           "Fri Oct 02 10:00:00 GMT 2026"   (cell 5, "Bids
                        Submission Deadline", verbatim; DEADLINE PARSING IS
                        THE NORMALISER'S JOB, NOT READ HERE - rule 10)
    procedure          "National Competitive Tendering"   (cell 6; also
                        measured: "Least Cost Selection with EOI",
                        "Quality-Cost Based Selection with EOI" - free text,
                        no CPV or other code anywhere on this listing, matching
                        `sources/ghana.yaml`'s own grep)
    status             "Bid Submission"   (cell 7; the only value measured
                        across all 20 rows sampled across both fetched pages)
    notice_pdf_url     "https://www.ghaneps.gov.gh/epps/cft/
                        downloadNoticeForAdvSearch.do?resourceId=3403640"
                        (cell 8's `<a href>`, made absolute; this is the URL
                        `sources/ghana.yaml` confirmed answers 200,
                        application/pdf, with no login and no cookie)
    publication_date   "Fri Sep 11 15:58:07 GMT 2026"   (cell 9, verbatim;
                        read here ONLY to cut the window - see
                        `publication_datetime` - and carried into the payload
                        unmodified for the normaliser to parse)

Both dates are Java's default `Date.toString()` format
(`EEE MMM dd HH:mm:ss 'GMT' yyyy`) and always say `GMT`, which is Ghana's own
zone year-round (`sources/ghana.yaml`: UTC+0, no DST) - so nothing here is a
UTC-offset guess, unlike a source that has to infer a zone from a bare local
time.

**No CPV code and no other classification.** Confirmed on both recorded pages,
matching `sources/ghana.yaml`'s grep of a full notice PDF: notices are typed
only by the free-text `procedure` column.

**PERSONAL DATA: none found.** Every row's title, procuring entity and
description tooltip across both recorded pages (20 rows total) was searched
for an email address and for phone-number-shaped digit runs: zero matches.
Every description names an institution (a ministry, an assembly, an agency) or
nothing more specific than "eligible Tenderers" - never a named individual.
There is nothing in a listing row for a normaliser to strip before a model
call (rule 19). This connector does not fetch the notice PDF linked from each
row - only the listing is read, matching rule 5 and the "one route" rule 1
requires - so whatever the PDF itself might contain is a question for whoever
later reads it, not for the fixture recorded here.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin

import httpx
import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See WINDOW in the module docstring: the daily schedule reads the last 7 days,
# and 2 of GHANEPS's 7 pages measured enough to cover it on 2026-09-13.
LOOKBACK_DAYS = 7

# GHANEPS returns 10 rows per page; not configurable by any query parameter
# this listing exposes (see sources/ghana.yaml's own probe).
ROWS_PER_PAGE = 10

# The registry's own expected_max (35) is comfortably inside 5 pages (50 rows).
# A burst big enough to need more than this stops here rather than walking the
# whole 7-page, 69-row archive every run - see the module docstring.
MAX_PAGES = 5

# The Struts DataTable id measured stable across three independent, cookie-free
# fetches on 2026-09-13 (see the module docstring's PAGINATION note). Page 1 is
# the bare list_url; page N>1 adds this parameter.
PAGE_PARAM = "d-3680175-p"

RESULTS_TABLE = "table#T01"

# The table's nine columns, in the order GHANEPS renders them (see
# tests/contract/fixtures/ghana.html). A row with any other count is a changed
# page and raises (rule 4), not a row read with fields shifted.
CELLS_PER_ROW = 9
CELL_TITLE = 1
CELL_PROCURING_ENTITY = 2
CELL_INFO = 3
CELL_DEADLINE = 4
CELL_PROCEDURE = 5
CELL_STATUS = 6
CELL_NOTICE_PDF = 7
CELL_PUBLICATION_DATE = 8

# The only status value measured across all 20 rows sampled on 2026-09-13
# (10 from each of the two fetched pages). See the module docstring.
EXPECTED_STATUS = "Bid Submission"

BASE_URL = "https://www.ghaneps.gov.gh"

RESOURCE_ID = re.compile(r"resourceId=(\d+)")

# Runs of spaces and tabs inside one line of a cell. Same idiom as
# monitor/connectors/sierra_leone.py's cell_text: collapsing template
# whitespace is reading the cell as published, not altering it.
INLINE_SPACE = re.compile(r"[ \t\r\f\v]+")


class GhanaConnector(FeedConnector):
    """GHANEPS Current Tenders, one page at a time until the 7-day window closes."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: no CPV or other classification code appears anywhere on this
        # listing (see the module docstring), so every notice reaches the
        # lexicon stage. Taken so every connector is built the same shape.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> datetime:
        """The oldest publication_date this run keeps. Daily schedule, 7-day window."""
        as_of = today or date.today()
        return datetime.combine(as_of - timedelta(days=LOOKBACK_DAYS), datetime.min.time(), tzinfo=UTC)

    def page_url(self, page: int) -> str:
        if page == 1:
            return self.source.list_url
        return f"{self.source.list_url}&{PAGE_PARAM}={page}"

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()

        wanted: list[dict] = []
        page = 1
        while True:
            response = client.get(self.page_url(page))
            response.raise_for_status()
            listing = parse_listing_page(response.text, expected_page=page, language=self.source.language)

            page_wanted = within_window(listing["rows"], cutoff)
            wanted.extend(page_wanted)

            full_page_in_window = len(page_wanted) == len(listing["rows"])
            if not full_page_in_window:
                break
            if page >= min(listing["total_pages"], MAX_PAGES):
                if page < listing["total_pages"]:
                    # The whole page was inside the window and there were more
                    # pages left to read: the true edge of the window was not
                    # reached. Said out loud rather than reported as a
                    # complete pass, same reasoning as
                    # monitor/connectors/liberia.py's own window-not-closed log.
                    log.info(
                        "ghana_window_not_closed",
                        pages_fetched=page,
                        total_pages=listing["total_pages"],
                        cutoff=cutoff.isoformat(),
                    )
                break
            page += 1

        ceiling = self.source.expected_max
        if len(wanted) > ceiling:
            log.warning(
                "ghana_ceiling_reached",
                in_window=len(wanted),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/ghana.yaml",
            )

        raw_notices = [
            self.raw_notice(
                url=row["notice_pdf_url"],
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in wanted[:ceiling]
        ]

        log.info(
            "ghana_fetch",
            pages_fetched=page,
            in_window=len(wanted),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_listing_page(markup: str, *, expected_page: int, language: str) -> dict:
    """One page's rows plus its own pagination state, checked before anything is used.

    Checks, in the order a failure is cheapest to explain:

      0. The page still declares the language the registry says it does - the
         same guard monitor/connectors/sierra_leone.py makes first, for the
         same reason: a re-platformed page could keep the exact same table id
         while serving a different locale.
      1. The results table and the pagination block are both on the page, so a
         redirect-to-home (the transient hazard sources/ghana.yaml describes)
         or a renamed container raises instead of reading a healthy zero.
      2. The page GHANEPS actually returned is the page this connector asked
         for - see the module docstring's PAGINATION note on why this cannot
         be assumed from the request alone.
      3. Every row has exactly CELLS_PER_ROW cells, and the title link and the
         notice PDF link name the same resourceId, the same identity
         cross-check monitor/connectors/liberia.py makes between a listing row
         and its own download.
    """
    tree = HTMLParser(markup)

    html_tag = tree.css_first("html")
    declared_language = ((html_tag.attributes.get("lang") if html_tag is not None else None) or "").lower()
    if not declared_language.startswith(language.lower()):
        raise ValueError(
            f"GHANEPS Current Tenders page declares <html lang={declared_language!r}>, not "
            f"{language!r} as sources/ghana.yaml states"
        )

    table = tree.css_first(RESULTS_TABLE)
    if table is None:
        raise ValueError(f"GHANEPS Current Tenders page has no {RESULTS_TABLE!r}; the page changed or redirected")

    pagination = tree.css_first("div.Pagination")
    if pagination is None:
        raise ValueError("GHANEPS Current Tenders page has no pagination block; the page changed")

    pagination_text = " ".join(pagination.text(separator=" ").split())
    results_match = re.search(r"([\d,]+) results in total", pagination_text)
    page_match = re.search(r"Page (\d+) of (\d+)", pagination_text)
    if not results_match or not page_match:
        raise ValueError(f"GHANEPS pagination block names no result count or page number: {pagination_text!r}")

    total_results = int(results_match.group(1).replace(",", ""))
    current_page = int(page_match.group(1))
    total_pages = int(page_match.group(2))
    if current_page != expected_page:
        raise ValueError(
            f"requested page {expected_page} but GHANEPS returned page {current_page}; the "
            f"{PAGE_PARAM!r} pagination parameter this connector relies on may have stopped working "
            "(see the module docstring's PAGINATION note)"
        )

    rows: list[dict] = []
    for position, element in enumerate(table.css("tbody tr")):
        cells = element.css("td")
        if len(cells) != CELLS_PER_ROW:
            raise ValueError(
                f"GHANEPS row {position} on page {expected_page} has {len(cells)} cells, not {CELLS_PER_ROW}; "
                "the table changed"
            )

        title_link = cells[CELL_TITLE].css_first("a")
        pdf_link = cells[CELL_NOTICE_PDF].css_first("a")
        info_img = cells[CELL_INFO].css_first("img")
        if title_link is None or pdf_link is None or info_img is None:
            raise ValueError(
                f"GHANEPS row {position} on page {expected_page} is missing its title link, "
                "notice PDF link or description icon"
            )

        title_id = _resource_id(title_link.attributes.get("href"), position, expected_page, "title link")
        pdf_id = _resource_id(pdf_link.attributes.get("href"), position, expected_page, "notice PDF link")
        if title_id != pdf_id:
            raise ValueError(
                f"GHANEPS row {position} on page {expected_page} names resourceId {title_id!r} in its title "
                f"link but {pdf_id!r} in its notice PDF link; the download would answer for the wrong notice"
            )

        title = cell_text(cells[CELL_TITLE])
        procuring_entity = cell_text(cells[CELL_PROCURING_ENTITY])
        description = (info_img.attributes.get("title") or "").strip()
        deadline = cell_text(cells[CELL_DEADLINE])
        procedure = cell_text(cells[CELL_PROCEDURE])
        status = cell_text(cells[CELL_STATUS])
        publication_date = cell_text(cells[CELL_PUBLICATION_DATE])

        missing = [
            label
            for label, value in (
                ("title", title),
                ("procuring entity", procuring_entity),
                ("description", description),
                ("deadline", deadline),
                ("procedure", procedure),
                ("publication date", publication_date),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"GHANEPS row {position} ({title_id}) on page {expected_page} is missing {missing}")
        if status != EXPECTED_STATUS:
            raise ValueError(
                f"GHANEPS row {position} ({title_id}) has status {status!r}, not {EXPECTED_STATUS!r}; "
                f"{EXPECTED_STATUS!r} is the only value measured across every row sampled on 2026-09-13"
            )

        rows.append(
            {
                "resource_id": title_id,
                "title": title,
                "procuring_entity": procuring_entity,
                "description": description,
                "deadline": deadline,
                "procedure": procedure,
                "status": status,
                "notice_pdf_url": urljoin(BASE_URL, pdf_link.attributes["href"]),
                "publication_date": publication_date,
            }
        )

    if not rows:
        raise ValueError(f"GHANEPS Current Tenders page {expected_page} has {RESULTS_TABLE!r} but zero rows")

    return {"rows": rows, "current_page": current_page, "total_pages": total_pages, "total_results": total_results}


def _resource_id(href: str | None, position: int, page: int, field: str) -> str:
    match = RESOURCE_ID.search(href or "")
    if not match:
        raise ValueError(f"GHANEPS row {position} on page {page} has no resourceId in its {field} ({href!r})")
    return match.group(1)


def within_window(rows: list[dict], cutoff: datetime) -> list[dict]:
    """The rows from the top of this page down to the first one older than cutoff.

    GHANEPS's default sort is publication_date descending on both recorded
    pages (checked here, not trusted): everything below the first old row is
    older still, the same reasoning monitor/connectors/liberia.py applies to
    its own sorted search page.
    """
    dates = [publication_datetime(row) for row in rows]
    for position, (newer, older) in enumerate(zip(dates, dates[1:], strict=False)):
        if older > newer:
            raise ValueError(
                f"GHANEPS Current Tenders rows are not sorted by publication date descending: row "
                f"{position + 1} ({older.isoformat()}) is newer than row {position} ({newer.isoformat()}); "
                "the window cut would under-read"
            )

    kept: list[dict] = []
    for row, published in zip(rows, dates, strict=True):
        if published < cutoff:
            break
        kept.append(row)
    return kept


def publication_datetime(row: dict) -> datetime:
    """A row's publication_date, Java's `Date.toString()` format, as a UTC datetime.

    Used only to cut the window; the raw string travels into the payload
    unmodified for the normaliser (rule 9, rule 10). Raises on a missing or
    unparseable value rather than skipping the row: this field was present and
    well-formed on every one of the 20 rows sampled on 2026-09-13, so trouble
    here is a changed page, not an empty field.
    """
    raw = row.get("publication_date")
    if not raw:
        raise ValueError(f"GHANEPS row {row.get('resource_id', '?')} has no publication_date")
    try:
        parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S GMT %Y")
    except ValueError as cause:
        raise ValueError(f"GHANEPS row {row.get('resource_id', '?')} has publication_date {raw!r}: {cause}") from cause
    return parsed.replace(tzinfo=UTC)


def cell_text(cell) -> str:
    """One table cell as published: line breaks kept, template whitespace removed.

    Non-breaking spaces become ordinary ones so a phrase survives matching the
    lexicon's ordinary-space phrases later. Same idiom as
    monitor/connectors/sierra_leone.py's `cell_text`.
    """
    lines = cell.text(separator="\n").replace("\xa0", " ").split("\n")
    kept = [collapsed for line in lines if (collapsed := INLINE_SPACE.sub(" ", line).strip())]
    return "\n".join(kept)
