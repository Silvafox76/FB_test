"""The OPEC Fund for International Development, project procurement notices.

Recorded from a real call on 2026-09-12. The fixture is
`tests/contract/fixtures/opec_fund.json`, the page's own HTML stored verbatim
under `listing_html`, and this parser is written against the markup that is
actually there.

**One page is the whole source**, the same shape as `monitor/connectors/
sierra_leone.py`: a single server-rendered `<table>` at
`https://opecfund.org/work-with-us/project-procurement/current-opportunities`,
48 data rows on 2026-09-12, no pagination, no archive link, no JavaScript
needed — a plain `curl` with the identified user agent returns every row
already populated. See `sources/opec_fund.yaml` for the robots.txt reading, the
terms-of-use quote and the volume/health reasoning; this docstring covers the
parsing and the three routes that look plausible from the OPEC Fund's own site
and are not this source.

**TRAP ONE: the page linked from the main navigation is not this page, and it
holds zero notices.** `https://opecfund.org/work-with-us/project-procurement`
(one path segment shorter than `list_url`) is the Project Procurement
*framework* page: the Procurement Principles, Procedures, standard bidding
documents and templates the OPEC Fund publishes for the whole programme.
Fetched cold on 2026-09-12: 200 OK, zero `<table>` elements, and its own body
text is prose about the procurement framework ("the OPEC Fund has launched new
Procurement Framework Documents which are consistent with other international
financial institutions' procurement guidelines"), not a single notice. A
connector pointed one level too shallow would fetch 200 OK and yield nothing —
under rule 4 that is a failure state a person must see, not a quiet zero — and
the fix is not a retry or a fallback path (rule 1 forbids both) but reading one
level deeper, to `.../current-opportunities`, which is what `list_url` below
actually is.

**TRAP TWO: never read the sibling corporate or consultant procurement paths,
and this is the trap most likely to survive a careless widening of scope.**
`https://opecfund.org/work-with-us/corporate-procurement/bidding` is the OPEC
Fund buying for **itself** — office IT, consultancy, facilities — not a
government borrower. Fetched cold on 2026-09-12: 200 OK, five tables, 101
rows, and one of them is literally "RFP - Program Management Office Services
for SAP S/4HANA Migration Implementation - 2026/047" (confirmed present in the
live response body). That string is precisely what this pilot's PFM lexicon
and scorer would rank highest, and it is precisely the wrong customer:
FreeBalance sells to governments running public financial management systems,
not to the multilateral lender that finances them. `https://opecfund.org/
work-with-us/consultants/current-opportunities` is the same problem one layer
over — the Fund hiring its own consultants, 36 rows, also confirmed live and
also never read here. A future maintainer "widening the path to get more
notices" would flood the review queue with high-scoring false positives from
the Fund's own back office; this connector reads only `/project-procurement/
current-opportunities`, which is the Fund's lending activity, and nothing else
on this domain.

**TRAP THREE: the table retains rows with nothing to bid on, and the
vocabulary that says so is in the registry, not here (rule 6).** Two whole
notice types are not something a supplier bids against: `General Procurement
Notice (GPN)` announces a project's future procurement programme before any
specific tender is issued (the World Bank and EBRD publish the same category
for the same reason — see `monitor/connectors/ebrd.py`'s own docstring), and
`Contract Award Notice (CAN)` announces who already won. Measured on
2026-09-12: 9 GPNs and 1 CAN out of 48 rows, and both strings are exact and
unambiguous across the whole table (no capitalisation or punctuation variant
of either, unlike the REOI/SPN type strings below). `sources/opec_fund.yaml`'s
`exclude_notice_types` names both; this module applies that list the way
`monitor/connectors/ebrd.py` applies its own — client-side, because nothing
here is a query parameter (see ACQUISITION SCOPE below) — and raises if a
configured name ever stops matching anything, the same guard EBRD's
`parse_listing` makes for the same reason: a typo in the registry must not
silently keep everything.

**The plan going into this build assumed the type and an empty Closing Date
would be the same fact twice, and measured, they are not — a correction worth
stating plainly rather than smoothing over.** 8 of the 10 excluded rows do
read `N/A`, `Not Applicable`, an empty cell or `N/A (Project not yet
approved)`, but 2 GPNs carry a real date in that column regardless
("Highway to Masaya-Sabana Grande Interurban Section Project" states
"September 29, 2026"; a regional infrastructure GPN states "August 15,
2026"). Both are excluded anyway, correctly: the decision below is made on
`notice_type`, not on whether `closing_date` happens to read "N/A", because a
GPN is a programme announcement regardless of what stray value its Closing
Date cell carries. `in_scope` never inspects `closing_date` for this reason.

That is not the only kind of non-biddable row this table carries, and the
other kind is *not* filtered here, on purpose. Of the 48 rows, only 9 have a
`Closing Date` on or after 2026-09-12 (the day this fixture was recorded); the
rest are past their own stated deadline but still listed — the page is an
append-only board, not a live queue. Rule 5 gives that decision to the filter
and normaliser stages, not to a connector: `deadline_at` is parsed from the
original string by `monitor/normalise/dates.py`'s rules, and whether an expired
deadline still stages a candidate is a filter-stage or stager-stage question,
never one this module answers by dropping the row before it is stored. This is
recorded here because it looks, at a glance, like the same problem
`exclude_notice_types` solves, and it is not: a GPN or CAN is excluded on its
type alone, while an expired REOI or SPN has a real deadline that simply
passed and is left for a later stage to weigh.

**ACQUISITION SCOPE: there is nothing to ask for.** Unlike `monitor/
connectors/ted.py`'s expert query or `monitor/connectors/doe.py`'s API
parameters, this page has no query string, no date range and no type filter —
one GET returns the whole board every time, the same shape as
`monitor/connectors/sierra_leone.py` and `monitor/connectors/ebrd.py`.
`exclude_notice_types` is therefore applied to the parsed rows in this module,
not sent anywhere, which is exactly what `sources/ebrd.yaml`'s own comment
calls acquisition scope rather than a filter stage: it decides what this
connector keeps, not what the lexicon later drops, and nothing is read and
then thrown away by a *different* stage that could have been asked not to read
it.

**THE BUYER IS NOT IN THE TABLE AND IS NOT REACHABLE.** The six columns are
Date, Country, Project / Notice, Sector, Closing Date and Type — no
implementing ministry or agency column exists. Each notice's own document is a
FlippingBook publication at `https://publications.opecfund.org/view/<id>/`;
fetched cold on 2026-09-12 and stripped of `<script>`/`<style>`, the page's
`<body>` contains **zero characters of text** — the viewer renders entirely
from JavaScript, and the raw HTML this connector is allowed to read (no
browser, rule 1: one path, no second client) carries nothing about who the
implementing agency is. This connector does not fetch the FlippingBook viewer
for that reason: there is nothing there for an `httpx.Client` to read, only a
boot script for a proprietary viewer. `Notice.buyer` is therefore always empty
for this source; the project name (the notice's own title) stands in for a
buyer in the review queue, and the reviewer resolves the real implementing
agency by opening the notice document by hand. This is a real limit on this
source's usefulness, not an oversight to quietly work around, and it is
recorded again in `sources/opec_fund.yaml` for a reader who never opens this
file.

**THE NOTICE CELL'S SHAPE VARIES, AND ALL OF IT IS READ, NOT GUESSED AT.**
Measured across all 48 rows on 2026-09-12: 46 wrap the title in the cell's
first `<p>` (occasionally itself a link straight to a national e-procurement
portal, e.g. Nicaragua's own `siscae` system, alongside the OPEC Fund's own
FlippingBook documents in a `<ul>` below it); 2 rows (Liberia Integrated
Fisheries Sector Strengthening Project, and the Mano River Union road
programme) have no `<p>` at all — the cell is nothing but a single linked
title, `<a><strong><u>...</u></strong></a>`. `notice_title` reads the first
`<p>` where one exists and falls back to the whole cell's text where it does
not, which covers both shapes without a second parser. Every one of the 48
rows carries at least one `<a href>` somewhere in the cell (unlike
`sierra_leone.yaml`'s bid table, which has two rows with none at all), so
`documents` never needs a None case here — it raises instead if a future row
ever has zero links, because that would be a new gap this source has not yet
shown and not one to paper over with sierra_leone.py's fallback. Document
labels are read as published and are not uniform ("REOI", "Terms of
Reference", "Download", "EOI-Template", "Clarification 1", ...); the first
link in the cell's own document order becomes `RawNotice.url` — usually the
notice's own first FlippingBook document, occasionally (row 0 in the recorded
fixture) the external national portal the title itself links to, and once
(the Liberia fisheries row) a plain `opecfund.org` project page rather than a
FlippingBook link at all. No ranking or preference is applied among the links
a row carries; the row's own order is kept exactly (rule 5).

**CLOSING DATE IS STORED EXACTLY AS PUBLISHED, INCONSISTENCIES INCLUDED.**
Measured shapes for "no deadline stated": `N/A` (7 rows), `Not Applicable` (1),
an empty cell rendering as a lone `&nbsp;` (1, which collapses to an empty
string after whitespace cleanup — the same "blank" rule 5 leaves the
normaliser to notice), and `N/A (Project not yet approved)` (1) — four
distinct spellings of the same fact, none of them rewritten here. One
`Closing Date` also carries a timezone note in the cell itself ("October 2,
2026, 15:00 (Nicaragua time)"); `monitor/normalise/dates.py` reads that
string, not a version of it interpreted here. The notice *type* vocabulary is
similarly inconsistent in ways `exclude_notice_types` never has to care about:
"Request for Expression Interest (REOI)", "Request For Expression Interest
(REOI)" and "Request for Expression of Interest (REOI)" are three
capitalisation/wording variants of the same category across the 48 rows, and
none of them is a type this connector excludes, so the inconsistency costs
nothing here — it would matter to whoever tunes `config/lexicon_en.yaml`
against this source's own words.

**WHAT THE SECTOR COLUMN ACTUALLY SHOWS.** Measured across the 48 rows:
Transport 13, Agriculture 11, Water & Sanitation 6 (plus 3 near-spellings:
"Water and Sanitation", "Sanitation", "Water Supply and Sanitation"), Energy 6,
Education 3 (plus 2 as "Transportation", a distinct sector-column value from
"Transport"), Multisector 1, Health 1. Zero rows are public financial
management or government information-system procurement, and a keyword pass
over every title and sector on the recorded fixture (financial management,
treasury, budget, tax, revenue, IFMIS/GIFMIS/IPPIS/HRMIS/ITAS and the rest of
`config/system_names.yaml`'s vocabulary, e-procurement, audit, payroll,
accounting, digital, ERP, information system) found nothing beyond one
substring false positive ("Improvement of Water Supply in Yangikurgan
**Dist**rict" matching "ict"). The OPEC Fund's own mandate page commits to
"Strengthen public finance", but that lending is policy-based budget support —
it disburses against reform actions taken, not against a system procured — and
none of it appears to run through this notice board. Built anyway, because the
route is clean, the connector is cheap to run, and the geography share below
is the best of any donor source assessed for this pilot; if a future run of
this connector ever does surface PFM or IFMIS content, that is the most
important line to put in that day's report, not a footnote.
"""

from __future__ import annotations

import json
import re

import httpx
import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The only table on the page (checked: `table.table` matches exactly one
# element in the recorded fixture). `class="table"` is a generic Bootstrap
# class rather than an id chosen for this content, so the header-text check
# below is the stronger guard against a re-theme that leaves some unrelated
# table carrying the same class.
RESULTS_TABLE = "table.table"
RESULTS_ROWS = f"{RESULTS_TABLE} tbody tr"
CELLS_PER_ROW = 6
CELL_DATE = 0
CELL_COUNTRY = 1
CELL_PROJECT = 2
CELL_SECTOR = 3
CELL_CLOSING_DATE = 4
CELL_TYPE = 5

# The page's own header row, checked verbatim. A changed column order would
# still have six `<td>` per row and would not trip CELLS_PER_ROW, so this is
# the guard that catches a reorder rather than a resize.
HEADER_CELLS = ("Date", "Country", "Project / Notice", "Sector", "Closing Date", "Type")

# Runs of spaces and tabs inside one line of a cell, and non-breaking spaces
# from the page's own markup. Same idiom as monitor/connectors/ebrd.py and
# monitor/connectors/sierra_leone.py: a browser already collapses these before
# a person sees them, so collapsing them here is reading the cell as published
# rather than altering it. Line breaks are kept, dropped only when they are
# entirely whitespace (the trailing "&nbsp;"-only paragraph one row's Type
# cell carries).
INLINE_SPACE = re.compile(r"[ \t\r\f\v]+")


class OpecFundConnector(FeedConnector):
    """One GET of the whole current-opportunities board. No paging, no detail request."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: this board carries no CPV code or shared classification of any
        # kind, so every kept notice reaches the lexicon stage. Taken so this
        # connector's constructor matches every other FeedConnector's — see
        # monitor/fetch.py's build_connector, which instantiates every connector
        # the same way regardless of whether a given source has a use for it.
        self.cpv_prefixes = cpv_prefixes

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        response = client.get(self.source.list_url)
        response.raise_for_status()

        rows = parse_opportunities(response.text, language=self.source.language)
        wanted = in_scope(rows, excluded_types=self.source.exclude_notice_types)

        raw_notices = [
            self.raw_notice(
                url=row["documents"][0]["url"],
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in wanted
        ]

        log.info(
            "opec_fund_fetch",
            rows=len(rows),
            excluded=len(rows) - len(wanted),
            fetched=len(raw_notices),
        )
        return raw_notices


def parse_opportunities(markup: str, *, language: str) -> list[dict]:
    """Every row of the current-opportunities board, checked for shape and language.

    Checks, in the order a failure is cheapest to explain, the same ordering
    principle monitor/connectors/sierra_leone.py and monitor/connectors/ebrd.py
    use:

      0. The page still declares the language the registry says it does.
      1. The results table is on the page at all.
      2. The header row still reads Date / Country / Project / Notice / Sector /
         Closing Date / Type, in that order — the guard against a column reorder
         that a per-row cell count alone would not catch.
      3. Every row has exactly six cells.
      4. Every row states its required fields (date, country, title, sector,
         type) and at least one document link; Closing Date is not required
         here (see the module docstring: four different spellings of "no
         deadline" are real, published values, not a parse failure).

    A table found with zero rows also raises: 48 measured on 2026-09-12, and a
    quiet re-read finding nothing is the redesign/selector failure rule 4
    exists to catch, not a healthy empty run (the same reasoning
    sierra_leone.yaml's VOLUME note gives for its own always-re-read table).
    """
    tree = HTMLParser(markup)

    html_tag = tree.css_first("html")
    declared_language = ((html_tag.attributes.get("lang") if html_tag is not None else None) or "").lower()
    if not declared_language.startswith(language.lower()):
        raise ValueError(
            f"OPEC Fund current-opportunities page declares <html lang={declared_language!r}>, not "
            f"{language!r} as sources/opec_fund.yaml states"
        )

    table = tree.css_first(RESULTS_TABLE)
    if table is None:
        raise ValueError(f"OPEC Fund current-opportunities page has no {RESULTS_TABLE!r}; the page changed")

    thead = table.css_first("thead")
    header = tuple(cell_text(th) for th in thead.css("th")) if thead is not None else ()
    if header != HEADER_CELLS:
        raise ValueError(f"OPEC Fund board header is {header}, not {HEADER_CELLS}; the table changed")

    rows: list[dict] = []
    for position, element in enumerate(table.css(RESULTS_ROWS)):
        cells = element.css("td")
        if len(cells) != CELLS_PER_ROW:
            raise ValueError(
                f"OPEC Fund board row {position} has {len(cells)} cells, not {CELLS_PER_ROW}; the table changed"
            )

        date_posted = cell_text(cells[CELL_DATE])
        country = cell_text(cells[CELL_COUNTRY])
        title = notice_title(cells[CELL_PROJECT])
        sector = cell_text(cells[CELL_SECTOR])
        closing_date = cell_text(cells[CELL_CLOSING_DATE])
        notice_type = cell_text(cells[CELL_TYPE])
        docs = documents(cells[CELL_PROJECT], position=position)

        missing = [
            label
            for label, value in (
                ("date", date_posted),
                ("country", country),
                ("title", title),
                ("sector", sector),
                ("type", notice_type),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"OPEC Fund board row {position} ({title!r}) is missing {missing}")

        rows.append(
            {
                "date_posted": date_posted,
                "country": country,
                "title": title,
                "sector": sector,
                "closing_date": closing_date,
                "notice_type": notice_type,
                "documents": docs,
            }
        )

    if not rows:
        raise ValueError(f"OPEC Fund board has {RESULTS_TABLE!r} but zero rows; the table is empty or changed")

    return rows


def in_scope(rows: list[dict], *, excluded_types: list[str]) -> list[dict]:
    """Rows minus the registry's non-biddable types (see TRAP THREE above).

    Applied here rather than at a query because this board has no query to
    apply it to (see ACQUISITION SCOPE in the module docstring). The guard
    below is monitor/connectors/ebrd.py's: a name in `exclude_notice_types`
    that matches nothing here is a typo that would otherwise exclude nothing
    and let every General Procurement Notice and Contract Award Notice reach
    the pipeline silently.
    """
    published_types = {row["notice_type"] for row in rows}
    for excluded in excluded_types:
        if excluded not in published_types:
            raise ValueError(
                f"sources/opec_fund.yaml excludes notice type {excluded!r}, which no row in the board carries; "
                f"the exclusion matches nothing. The board publishes {sorted(published_types)}"
            )
    return [row for row in rows if row["notice_type"] not in excluded_types]


def notice_title(cell) -> str:
    """The Project / Notice cell's title, read from its first `<p>` where one exists.

    Two of the 48 recorded rows carry no `<p>` at all — the whole cell is a
    single linked title (see the module docstring) — so the fallback is the
    whole cell's own text, which for those two rows is exactly the title and
    nothing else.
    """
    first_paragraph = cell.css_first("p")
    return cell_text(first_paragraph if first_paragraph is not None else cell)


def documents(cell, *, position: int) -> list[dict]:
    """Every document link the Project / Notice cell carries, in the row's own order.

    Every one of the 48 rows recorded on 2026-09-12 carries at least one link,
    unlike sierra_leone.yaml's bid table (two gaps out of 32); a row with none
    here is a shape this source has not yet shown, so it raises rather than
    silently pointing the notice at the board's own URL the way
    monitor/connectors/sierra_leone.py's `document_link` does for a known,
    already-measured gap.
    """
    found = [{"label": cell_text(link), "url": (link.attributes.get("href") or "").strip()} for link in cell.css("a")]
    if not found:
        raise ValueError(f"OPEC Fund board row {position} has no document link at all")
    for document in found:
        if not document["url"]:
            raise ValueError(f"OPEC Fund board row {position} has a document link with no href")
    return found


def cell_text(cell) -> str:
    """One table cell as published: line breaks kept, template whitespace removed.

    Non-breaking spaces become ordinary ones so a phrase survives matching the
    lexicon's ordinary-space phrases later, and a line that is nothing but
    whitespace (the Type cell that carries a trailing empty `<p>&nbsp;</p>`) is
    dropped rather than kept as a blank line. Same idiom as
    monitor/connectors/ebrd.py's and monitor/connectors/sierra_leone.py's
    `cell_text`.
    """
    lines = cell.text(separator="\n").replace("\xa0", " ").split("\n")
    kept = [collapsed for line in lines if (collapsed := INLINE_SPACE.sub(" ", line).strip())]
    return "\n".join(kept)
