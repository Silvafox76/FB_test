"""Burkina Faso: DGCMEF's daily public procurement bulletin ("Quotidien").

Recorded from a real call on 2026-09-12: `tests/contract/fixtures/burkina_faso.html`
is `GET https://dgcmef.gov.bf/index.php/fr/revue-de-march-s-pour-tous`, unmodified.
Which of four candidate routes this is (dgcmef.gov.bf, not arcop.bf, not
secop.finances.bf, not the non-existent marches-publics.gov.bf) is measured and
recorded in full in `sources/burkina_faso.yaml`; this file only carries what the
connector itself needs.

**The real markup, and one thing in it that this session's own re-fetch adds to
what the registry entry says.** The page renders the same view twice: the
paginated one this connector reads, inside `#block-gavias-monte-content` (15 rows
per page, 73 pages, newest first), and a second, unpaginated "Informations pour
tous" block further down the page (`id="block-views-block-revue-de-marches-pour-
tous-block-1-2"`) that repeats the newest 10 of those same rows. Both render a
`table.cols-2`, so the container check below is scoped to the first block's id and
not just the table class, or a changed page could still find a `table.cols-2` and
silently read the wrong one.

**One correction to this connector's own brief, found in the row this session
happened to look closely at.** The brief it was given says "one PDF href per
issue". The real row for Quotidien n°4480 (Jeudi 03 septembre 2026) carries two:
the normal file and a second file named "(bis)" — a same-day reissue, still live
on the server next to the original. Both are fetched: the file cell's `<a>` tags
are read as a list, not a single link, and the connector raises if that list is
ever empty rather than assuming there is exactly one.

**The "some issues are scanned images" premise, checked independently rather than
carried over from the brief.** `sources/burkina_faso.yaml` reports ten issues
sampled 2026-08-21-adjacent back to 2021 all had embedded fonts and real text. This
session fetched one of the fifteen listed issues directly — Quotidien n°4478,
2026-09-01, 1,758,977 bytes, 49 pages — and ran `pdffonts`/`pdftotext` against it
before writing a line of parser: embedded TrueType subsets throughout (Calibri,
Arial and Times New Roman families) and 297,512 characters of real French prose,
inside the registry's measured 124,471-631,173 range. Not a scan. That PDF is not
committed at that size; `tests/contract/fixtures/burkina_faso_sample.pdf` is its
first four of 49 pages, and `tests/contract/fixtures/burkina_faso.json` records
both numbers and how the truncation was made.

**Separation of concerns (rule 5), decided rather than defaulted.** This connector
stores each PDF's bytes, base64-encoded because `RawNotice.payload` is `str`, as
the payload, with `mime="application/pdf"`. It does not call
`monitor.normalise.ocr.extract` and it does not decide text-layer against
Textract, and it does not split one issue's thirty-odd pages of French prose into
its individual dossier notices — that decision, and the section-heading parsing
`sources/burkina_faso.yaml` asks for ("Ministères..." vs "Régions", the three
`Marchés de...` subsections), is the normaliser's, not read twice here. The PDF
bytes are what DGCMEF published; storing them unread and letting the normaliser
extract and split is rule 9's "original text is the record" taken at face value,
and it is also the only way to avoid rule 5's finding twice over: this connector
already fetches from a listing and would otherwise be doing acquisition, OCR
routing and per-notice segmentation in one file.

**The consequence this creates, flagged rather than quietly absorbed.** `fetch()`
therefore yields one `RawNotice` per issue PDF (occasionally two, for a same-day
"bis"), not one per dossier notice. On an ordinary business day that is 1 item;
`sources/burkina_faso.yaml`'s own `expected_items_per_run: [10, 160]` is measured
as dossier-reference strings *inside* one issue, which the registry entry itself
says needs "the connector's actual section parsing" to become a real per-notice
count — it is not a count this connector's `fetch()` can ever produce without
doing that parsing itself. Wiring this source into `monitor fetch` needs one of:
a second, connector-level health expectation that describes issues per run
(around 1, occasionally 2) rather than notices per issue; or `monitor/fetch.py`
and `monitor/health/source_health.py` reading the *normaliser's* per-notice yield
for this source instead of `len(connector.fetch())`. Neither is decided here.

**A second, smaller consequence for the same reason.** `monitor/fetch.py`'s
`_store_notice` currently does `document = json.loads(payload)` unconditionally
before handing it to a mapper, and `store_payload` always writes the stored file
with a `.json` suffix. Both assume every connector's payload is JSON text, which
was true of every connector before this one. Wiring this source in also needs a
mime-aware branch there (base64-decode and route through `ocr.extract` rather than
`json.loads`, and a `.pdf`-suffixed path from `mime`) before `monitor fetch
burkina_faso` can do anything but crash on the first row. `monitor/fetch.py` is
outside this file's lane; noted here so the step that wires this source in does
not discover it by running it.

**The window.** The schedule is daily on business days
(`sources/burkina_faso.yaml`: `0 11 * * 1-5`), so a five-day lookback is enough to
absorb one missed run over a normal weekend or a single holiday Monday without
reaching back far enough to need a second listing page: one page holds fifteen
rows, roughly three calendar weeks, and the fixture's own window (2026-09-12,
5 days back) resolves entirely within it. No pagination is built: this session
recorded page 0 and nothing past it, and a `?page=1` branch nobody has fetched
would be built against documentation memory rather than a response, which is
exactly what this repository does not do (CLAUDE.md). If the window is ever wider
than what page 0 holds, `fetch_raw` says so in a log line rather than guessing at
a second page's shape.
"""

from __future__ import annotations

import base64
import re
from datetime import date, timedelta

import httpx
import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# Daily, business days only (sources/burkina_faso.yaml: '0 11 * * 1-5'). Five days
# absorbs one missed run over a weekend or a single holiday Monday while staying
# inside what a single listing page holds (15 rows, roughly three calendar weeks)
# — see the module docstring's WINDOW section for why no second page is fetched.
LOOKBACK_DAYS = 5

# The absolute bound on PDF requests in one run, above what the window is expected
# to need. Five business days is normally 5 issues and rarely 6 (a same-day "bis"
# reissue, like Quotidien n°4480's); this is headroom for a backlog after a missed
# run, not a number this source is expected to reach in the ordinary case.
MAX_ISSUE_REQUESTS = 20

# Scoped to the id of the paginated block and not just the table class: the same
# view is rendered a second time, unpaginated, further down the page (see the
# module docstring), and it renders the identical `table.cols-2`.
LISTING_CONTAINER = "#block-gavias-monte-content table.cols-2"
CELLS_PER_ROW = 2
CELL_TITLE = 0
CELL_FILES = 1

PDF_HREF = re.compile(r"\.pdf$", re.IGNORECASE)

# French month names as this listing spells them, accented and not, because the
# archive spans 2021 to today and this session only read 2026's rendering. Keyed
# so an alternation can be built directly from them.
MONTHS_FR = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}

# The last "<day> <month> <year>" in a title is the date this connector cuts the
# window on. A combined issue names two weekdays but one month
# ("Mardi 25 & Mercredi 26 août 2026"): the bare "25" before the "&" states no
# month of its own and this pattern does not match it, so `finditer` yields one
# match here, "26 août 2026" — the later of the two days, which is what the window
# cut needs and is also the day DGCMEF filed the combined issue under.
TITLE_DATE = re.compile(r"(\d{1,2})\s+(" + "|".join(MONTHS_FR) + r")\s+(\d{4})", re.IGNORECASE)


class BurkinaFasoConnector(FeedConnector):
    """One listing page, newest first, then every in-window issue's PDF bytes."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: sources/burkina_faso.yaml found no CPV code or other numbered
        # classification anywhere in ten sampled issues. Taken so every connector
        # is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest issue date this run keeps. Daily schedule, five-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        listing = client.get(self.source.list_url)
        listing.raise_for_status()

        issues = parse_listing(listing.text)
        wanted, window_closed = in_scope(issues, cutoff=cutoff)
        if not window_closed:
            log.info(
                "burkina_faso_window_not_closed",
                fetched=len(wanted),
                cutoff=cutoff.isoformat(),
                detail="the oldest row on this listing page is still inside the window; a second page exists "
                "but is not fetched (see the module docstring's WINDOW section)",
            )

        files = [(issue, href) for issue in wanted for href in issue["hrefs"]]
        if len(files) > MAX_ISSUE_REQUESTS:
            log.warning(
                "burkina_faso_ceiling_reached",
                in_scope=len(files),
                ceiling=MAX_ISSUE_REQUESTS,
                detail="raise MAX_ISSUE_REQUESTS in monitor/connectors/burkina_faso.py or shorten LOOKBACK_DAYS",
            )
        files = files[:MAX_ISSUE_REQUESTS]

        raw_notices: list[RawNotice] = []
        for _issue, href in files:
            response = client.get(href)
            response.raise_for_status()
            raw_notices.append(
                self.raw_notice(
                    url=href,
                    payload=base64.b64encode(response.content).decode("ascii"),
                    mime="application/pdf",
                )
            )

        log.info(
            "burkina_faso_fetch",
            issues_on_page=len(issues),
            issues_in_scope=len(wanted),
            files_fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices


def parse_listing(markup: str) -> list[dict]:
    """Every issue row on the page, checked for shape before anything is trusted.

    Three checks, cheapest to explain first: that the scoped container is on the
    page at all (without it a renamed block would be an archive of zero issues,
    not an error); that every row still has exactly two cells; that the title cell
    names a real business-day date and the file cell names at least one PDF.
    """
    tree = HTMLParser(markup)
    container = tree.css_first(LISTING_CONTAINER)
    if container is None:
        raise ValueError(f"Burkina Faso listing has no {LISTING_CONTAINER!r}; the Drupal view changed")

    issues: list[dict] = []
    for position, row in enumerate(container.css("tbody tr")):
        cells = row.css("td")
        if len(cells) != CELLS_PER_ROW:
            raise ValueError(
                f"Burkina Faso listing row {position} has {len(cells)} cells, not {CELLS_PER_ROW}; the view changed"
            )

        title_link = cells[CELL_TITLE].css_first("a")
        if title_link is None:
            raise ValueError(f"Burkina Faso listing row {position} has no title link")
        title = " ".join(title_link.text().split())
        if not title.lower().startswith("quotidien"):
            raise ValueError(
                f"Burkina Faso listing row {position} is titled {title!r}, not a Quotidien issue; "
                "the view may now be listing a different document type"
            )

        hrefs = [href for a in cells[CELL_FILES].css("a") if (href := a.attributes.get("href"))]
        if not hrefs:
            raise ValueError(f"Burkina Faso listing row {position} ({title!r}) has no file link")
        not_pdf = [href for href in hrefs if not PDF_HREF.search(href)]
        if not_pdf:
            raise ValueError(f"Burkina Faso listing row {position} ({title!r}) links to a non-PDF file: {not_pdf}")

        issues.append({"title": title, "hrefs": hrefs, "issue_date": issue_date(title, position=position)})

    dates = [issue["issue_date"] for issue in issues]
    for position, (later, earlier) in enumerate(zip(dates, dates[1:], strict=False)):
        if earlier > later:
            raise ValueError(
                f"Burkina Faso listing is not sorted newest first: row {position + 1} ({earlier.isoformat()}) "
                f"is newer than row {position} ({later.isoformat()}); the window cut would under-read"
            )
    return issues


def in_scope(issues: list[dict], *, cutoff: date) -> tuple[list[dict], bool]:
    """The issues from the top of the (descending) page down to the first old one.

    Returns the in-window issues and whether an older row was actually found on
    this page — `False` means the page ran out before the window did, which is
    worth a log line rather than a silent under-read (see the module docstring's
    WINDOW section on why a second page is not fetched instead).
    """
    wanted: list[dict] = []
    for issue in issues:
        if issue["issue_date"] < cutoff:
            return wanted, True
        wanted.append(issue)
    return wanted, False


def issue_date(title: str, *, position: int = 0) -> date:
    """The most recent calendar date a title states. Raises if it states none.

    See `TITLE_DATE` on why the *last* match is the one this reads, including for
    a combined two-day issue.
    """
    matches = list(TITLE_DATE.finditer(title))
    if not matches:
        raise ValueError(f"Burkina Faso listing row {position} title {title!r} carries no dd month yyyy date")
    day, month_name, year = matches[-1].groups()
    try:
        return date(int(year), MONTHS_FR[month_name.lower()], int(day))
    except ValueError as cause:
        raise ValueError(f"Burkina Faso listing row {position} title {title!r} has an invalid date: {cause}") from cause
