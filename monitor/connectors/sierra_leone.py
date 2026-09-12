"""Sierra Leone's National Public Procurement Authority (NPPA), Bid Opportunities.

Recorded from a real call on 2026-09-12. The fixture is
`tests/contract/fixtures/sierra_leone.json`, the page's own HTML stored verbatim
under `listing_html`, and this parser is written against the markup that is
actually there.

**One page is the whole source.** `https://nppa.gov.sl/bid/` is a WordPress page
whose body is a single server-rendered TablePress table, `table#tablepress-1`: 32
rows on 2026-09-12, no pagination, no JavaScript rendering step, everything already
in the response body a plain `curl` gets. See `sources/sierra_leone.yaml` for the
three-route probe (this host, the unreachable `publicprocurement.gov.sl`, and the
e-GP platform's client-side-only listing behind a disallowed `/api/`) and the
robots.txt reading that clears `/bid/`.

**There is no separate notice page.** Unlike `monitor/connectors/ebrd.py` and
`monitor/connectors/doe.py`, the table row already carries everything a notice
needs: organisation, date posted, expiration date, description, and the bid
document's own link. So one connector pass is one GET, and each row becomes one
`RawNotice` with no second request. The document is a static PDF under
`/wp-content/uploads/<year>/<month>/`, not an HTML detail page, so it is recorded
as a URL and never fetched here (rule 5: this connector reads the listing, not the
documents linked from it).

**Two of the 32 rows carry no document link at all**, a gap in the source rather
than a fetch failure: WEST AFRICA HOLDING (SL) LIMITED (16-06-2026) has no `<a
href>` at all, and UNIVERSITY OF SIERRA LEONE (20-04-2026) has `<a href="">` with
an empty attribute. Both are read as `document_url: None` rather than raising or
being dropped, because a data-entry gap in one column is not a reason to lose the
other four the row does carry. `RawNotice.url` needs a URL regardless, so a row
with no document link is stamped with the bid page's own URL, which is where a
person would in fact find whatever is publicly available about it.

**Nothing here reads a date as a date.** `date_posted` and `expiration_date` are
stored exactly as the cell prints them, whitespace glitches included: two rows
carry a stray internal space ("30- 04-2026", "26 -01-2026", one of those two also
in its expiration date), one expiration date has no leading zero on the month
("13-4-2026"), and one has a two-digit year ("23-04-26"). None of that is fixed up
here (rule 5 and rule 10): the normaliser's date rules read the original string,
not a connector's guess at what was meant.

**The page's own declared language is checked against the registry's**, the one
guard this source's shape allows in place of EBRD's country-name check or DÖE's
per-notice language field: `<html lang="en-US">` is on every page this host
serves, and a change there is the cheapest possible signal that NPPA re-platformed
under a different locale before any row-level check would ever notice.

**No CPV code and no shared national scheme exists on this listing**: each row's
own description is free text and the agency reference numbers it sometimes embeds
(e.g. "MOS/REOI/QCBS/2026/001") are per-agency, not a classification. Every notice
here reaches `config/lexicon_en.yaml` for the same reason `sources/ebrd.yaml`'s do.
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

# The table and its five columns, in the order the page renders them. A row with
# any other number of cells is a changed page and raises (rule 4), not a row read
# with fewer fields.
RESULTS_TABLE = "table#tablepress-1"
RESULTS_ROWS = f"{RESULTS_TABLE} tbody tr"
CELLS_PER_ROW = 5
CELL_ORGANISATION = 0
CELL_DATE_POSTED = 1
CELL_EXPIRATION_DATE = 2
CELL_DESCRIPTION = 3
CELL_DOCUMENT = 4

# Runs of spaces and tabs inside one line of a cell. TablePress's own client-side
# DataTables would collapse these before a person ever saw them; collapsing them
# here is reading the cell as published rather than altering it. Line breaks
# (`<br />` in the source) are kept, because a multi-line organisation name or
# description is how the buyer wrote it. Same idiom as monitor/connectors/ebrd.py.
INLINE_SPACE = re.compile(r"[ \t\r\f\v]+")


class SierraLeoneConnector(FeedConnector):
    """One GET of the whole bid table. No paging, no per-row detail request."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: there is no CPV code or shared classification anywhere on this
        # listing, so every notice reaches the lexicon stage. Taken so every
        # connector is built the same way (monitor/connectors/ebrd.py's idiom).
        self.cpv_prefixes = cpv_prefixes

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        response = client.get(self.source.list_url)
        response.raise_for_status()

        rows = parse_bid_table(response.text, language=self.source.language)

        raw_notices = [
            self.raw_notice(
                url=row["document_url"] or self.source.list_url,
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in rows
        ]

        log.info(
            "sierra_leone_fetch",
            rows=len(rows),
            no_document_link=sum(1 for row in rows if row["document_url"] is None),
        )
        return raw_notices


def parse_bid_table(markup: str, *, language: str) -> list[dict]:
    """Every row of the bid table, checked for the page's shape and its language.

    Three checks, in the order a failure is cheapest to explain:

      0. The page still declares the language the registry says it does. Checked
         first because a re-platformed page could still happen to keep this exact
         table id while serving a different locale, and that would be a stranger
         thing to debug from a row-count mismatch than from this.
      1. The results table is on the page at all, the same container check every
         other connector makes.
      2. Every row has exactly five cells, so a changed page raises instead of
         mapping a row with a field silently shifted into the next one.

    A table found with zero rows also raises: 32 measured on 2026-09-12, and zero
    from a source that normally yields is the failure state rule 4 exists for, not
    a quiet day (the arrival rate itself is genuinely sparse - see VOLUME in
    sources/sierra_leone.yaml - but that is a property of new *postings*, not of
    how many rows the table holds, which is what this connector reads).
    """
    tree = HTMLParser(markup)

    html_tag = tree.css_first("html")
    declared_language = ((html_tag.attributes.get("lang") if html_tag is not None else None) or "").lower()
    if not declared_language.startswith(language.lower()):
        raise ValueError(
            f"NPPA bid page declares <html lang={declared_language!r}>, not "
            f"{language!r} as sources/sierra_leone.yaml states"
        )

    table = tree.css_first(RESULTS_TABLE)
    if table is None:
        raise ValueError(f"NPPA bid page has no {RESULTS_TABLE!r}; the page changed")

    rows: list[dict] = []
    for position, element in enumerate(table.css("tbody tr")):
        cells = element.css("td")
        if len(cells) != CELLS_PER_ROW:
            raise ValueError(
                f"NPPA bid table row {position} has {len(cells)} cells, not {CELLS_PER_ROW}; the table changed"
            )

        organisation = cell_text(cells[CELL_ORGANISATION])
        date_posted = cell_text(cells[CELL_DATE_POSTED])
        expiration_date = cell_text(cells[CELL_EXPIRATION_DATE])
        description = cell_text(cells[CELL_DESCRIPTION])
        document_url = document_link(cells[CELL_DOCUMENT])

        missing = [
            label
            for label, value in (
                ("organisation", organisation),
                ("date posted", date_posted),
                ("expiration date", expiration_date),
                ("description", description),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"NPPA bid table row {position} ({organisation!r}) is missing {missing}")

        rows.append(
            {
                "organisation": organisation,
                "date_posted": date_posted,
                "expiration_date": expiration_date,
                "description": description,
                "document_url": document_url,
            }
        )

    if not rows:
        raise ValueError(f"NPPA bid page has {RESULTS_TABLE!r} but zero rows; the table is empty or changed")

    return rows


def document_link(cell) -> str | None:
    """The row's bid document link, or None where the source states no link.

    Measured as two distinct shapes of "no link" (see the module docstring): an
    `<a>` with no `href` attribute at all, and `<a href="">` with an empty one.
    Both are read the same way; neither raises, because this is a gap the source
    itself publishes and not a fetch failure (rule 4 is about a selector matching
    nothing, not about one column of a matched row being blank).
    """
    link = cell.css_first("a")
    if link is None:
        return None
    return link.attributes.get("href") or None


def cell_text(cell) -> str:
    """One table cell as published: line breaks kept, template whitespace removed.

    Non-breaking spaces become ordinary ones so a phrase survives matching the
    lexicon's ordinary-space phrases later. Same idiom as
    monitor/connectors/ebrd.py's `cell_text`.
    """
    lines = cell.text(separator="\n").replace("\xa0", " ").split("\n")
    kept = [collapsed for line in lines if (collapsed := INLINE_SPACE.sub(" ", line).strip())]
    return "\n".join(kept)
