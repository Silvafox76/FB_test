"""EBRD client procurement notices, the ECEPP notice search.

Recorded from real calls on 2026-09-12. The fixture is both halves of what this
connector does, stored as the bytes that arrived: the listing response in
`tests/contract/fixtures/ebrd.html` and the notice pages it then fetched in
`tests/contract/fixtures/ebrd.json`.

**Where EBRD's notices actually are, because three routes look plausible and only
one of them is a source.** Probed 2026-09-12:

  1. `www.ebrd.com/.../project-procurement/procurement-notices.html` is an Adobe
     Experience Manager page whose list is drawn by JavaScript from
     `POST /bin/ebrd_dxp/filterlistservlet`. That servlet answers without a
     credential and reports `resultCount` **10**. The page says why in its own
     first paragraph: procurement notices "are now only published on ECEPP", and
     these ten are the residue of contracts not run through it. Ten notices in two
     years, one of them in the pilot's geography, is not this source.
  2. `ecepp.ebrd.com/delta/noticeSearchResults.html` is the portal itself, needs no
     credential, and returns **4,050 notices from 2015-07-27 to 2026-09-11** - the
     whole public archive - in one 3.8 MB server-rendered table. This is the source.
  3. EBRD's *own* corporate and consultancy-services procurement, which is where a
     Bank-funded PFM advisory assignment would sit, moved to SMART by GEP and that
     page states "Registration is required to view current Procurement Notices".
     It is behind a credential and is not read here. See sources/ebrd.yaml.

There is no RSS feed. `ecepp.ebrd.com/feed/` is the WordPress wrapper's own feed
and holds one item, a post titled "test" from November 2020. `/delta/rss.html`,
`/delta/syndication.html` and `noticeSearchResults.rss` all 302 to the CAS login.

**Hazard one: not one query parameter exists.** The search form on that page has no
`name` on any input - keyword, notice type, current state and the date range are
`id`-only and are wired to DataTables in the browser. Every filter is client-side.
Measured, because a parameter ignored in silence is the most dangerous thing a
connector can be built on:

    noticeSearchResults.html                                            4,050 rows
    ?noticeType=General Procurement Notice&country=Ukraine
        &keyword=financial&currentState=Open&pageSize=10                4,050 rows
    POST noticeSearch.html (the form's own action) with keyword=...     4,050 rows

So there is nothing to ask for. The whole archive arrives on every request and the
window, the country scope and the notice-type exclusion are all applied here.

**Hazard two, and it is the one that would have cost notices: the rows are not
sorted.** The top of the page looks newest-first, which invites the walk
`monitor/connectors/worldbank.py` uses - read down a descending page and stop at
the first row outside the window. On this page that walk under-reads. Measured over
the 4,050 rows: 1,107 pairs are out of order on the minute-level sort key and 10
are out of order by whole days, the worst jumping 70 days (row 1509 goes
2023-09-28 -> 2023-12-07). One of the ten is inside a 30-day window: row 31 is
2026-08-07, older than the cutoff, and row 32 is 2026-08-17, inside it. A walk that
stopped at row 31 would have reported a healthy run and silently dropped the rest
of the month. DataTables sorts in the browser (`"order": [[3, "desc"]]`); the
server's order is its own. So every row is read and the window is a filter over all
of them, not a cut. No ordering is asserted because none is relied on.

**The country comes from the row's hidden metadata cell, not from the title.** Each
row carries four columns DataTables hides, the last of which is a flat bracketed
list `[project name, project id, country, ..., client, sector, notice type]` that
exists so the browser's keyword box can match on fields the table does not show.
It cannot be split on commas - client names contain them ("Federal Ministry of
Innovation, Communication and Digital Economy of Nigeria") - so the scope is a
membership test for `", <country>,"` and nothing is read positionally. Checked
against a positional reading of all 4,050 rows: they agree on every row, and no row
contains two covered country names.

The title's `Country: ` prefix was the obvious alternative and it is prose the
client types, not a field. Two of the 4,050 prove it: one row is titled `BA: ...`
where the metadata says Bosnia And Herzegovina, and one is
`United Kingdom: MDB PIA test`, a test notice left in the live archive with project
id `N/A` and client `PPAD`. The metadata cell also keeps a country current where the
title does not: 52 titles still say "Macedonia FYR" or "FYR Macedonia", and 9 of
those rows carry "North Macedonia" in the metadata (42 keep the historical name
there too, and one has no metadata at all).

**The listing's clock is wrong for an hour of the day, which is the third reason
only the notice page is mapped.** The hour 12 never appears in the 4,050 published
timestamps the listing renders - not once - while the hour 00 appears 444 times
against 8 at 01:00 and 9 at 23:00, and the neighbouring hours 11 and 13 are busy
with 438 and 377. The noon hour is being rendered as midnight, 11% of the archive's
worth, and one of the eleven notices one run fetched proves it directly: the listing
says it was published at 00:18 and its own page says 12:18. The *date* survives
either way, which is why the window cut may read the listing and does, but
`published_at` and `deadline_at` are taken from the notice page and nothing here
reads a time from the listing.

Nineteen rows carry an empty metadata cell - `[]` - and so state no country at all.
All nineteen are older than 2023 (17 addenda from 2016 to 2018 and two 2022 General
Procurement Notices, one of them Ukrainian), so a 30-day window never reaches them.
An empty cell *inside* the window raises rather than being dropped, because one of
the nineteen was a covered country and losing it would have been silent.

WINDOW. 30 days, and it is chosen from the arrival rate rather than from habit. The
seven covered countries hold 911 of the 4,050 notices, arriving at 0.45 a day; the
rolling 30-day count over the last two years runs 3 to 14, median 9. A two-day
window like the notices API's would return nothing on four days in five, and
`monitor/health/source_health.py` counts zero `items_seen` on a source whose
`expected_min` is above zero as a failure state rather than an empty success - this
source would sit at unhealthy while working perfectly. Re-reading 30 days costs no
extra listing request, because there is only ever one and it is the whole archive;
it costs about nine detail pages, and the content hash makes `items_new` zero for
the ones already seen.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import httpx
import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The oldest publication date a run keeps. See WINDOW in the module docstring.
LOOKBACK_DAYS = 30

# The absolute bound on detail requests in one run, above the registry's
# expected_max. The measured 30-day maximum over two years is 14; a burst that
# filled the window with in-scope notices still stops here rather than making a
# hundred requests in a pass the source's operators would read as a crawl.
MAX_DETAIL_REQUESTS = 60

# EBRD's own spelling of each covered country, keyed by the ISO code the registry
# uses, with the historical spellings that are still in the archive. This is the
# source's vocabulary, not configuration: rule 6 puts keywords, thresholds,
# weights and placeholders in YAML because a person tunes them, and nobody tunes
# "Macedonia FYR" - it is what the portal's own metadata says, and the day it
# changes this connector must fail rather than be re-tuned. It sits beside
# `monitor/normalise/codes.py`'s alpha-3 table for the same reason, and not *in* it
# because the mapping is this source's and no other's. It is also not in
# sources/ebrd.yaml: `Source` forbids unknown fields, so a `country_names` block
# there would have to be added to the model every source is validated against,
# which puts one source's vocabulary in all of their contracts.
#
# Counts in the archive on 2026-09-12, from the metadata cell, all verified:
# Ukraine 337, Bosnia And Herzegovina 383, North Macedonia 154 + Macedonia FYR 41 +
# FYR Macedonia 1, Albania 139, Montenegro 104, Kosovo 83, Nigeria 3.
#
# The other twelve countries in the pilot's geography are absent from this map
# because EBRD publishes nothing for them: 0 rows in 4,050 across eleven years for
# Benin, Burkina Faso, Cote d'Ivoire, Gambia, Ghana, Liberia, Mali, Mauritania,
# Niger, Senegal, Sierra Leone and Togo. Adding one means finding out what EBRD
# calls it first, which cannot be done from a country that has never appeared.
EBRD_COUNTRY_NAMES = {
    "UA": ("Ukraine",),
    "BA": ("Bosnia And Herzegovina",),
    "MK": ("North Macedonia", "Macedonia FYR", "FYR Macedonia"),
    "AL": ("Albania",),
    "ME": ("Montenegro",),
    "XK": ("Kosovo",),
    "NG": ("Nigeria",),
}

ISO2_BY_EBRD_NAME = {name: code for code, names in EBRD_COUNTRY_NAMES.items() for name in names}

# The listing table and its columns. Six are shown and four are hidden by
# DataTables (`"visible": false` on targets 6 to 9); the server sends all ten, so a
# row with any other number of cells is a changed page and raises.
RESULTS_TABLE = "table#noticeResultsTable"
RESULTS_ROWS = f"{RESULTS_TABLE} tbody tr"
CELLS_PER_ROW = 10
CELL_TITLE = 0
CELL_NOTICE_TYPE = 1
CELL_EXERCISE_TITLE = 2
CELL_PUBLISHED_AT = 3
CELL_CLOSING_AT = 4
CELL_STATE = 5
CELL_PUBLISHED_DATE = 6
CELL_METADATA = 9

# The detail page's field table, and the five labels every notice must carry.
#
# **The field set varies by notice type and the variation is large.** Counts from
# the eleven pages one real run fetched, plus two General Procurement Notices
# probed separately because no GPN fell inside the recorded window and 715 of the
# 4,050 archived notices are GPNs:
#
#   on all 13:  Project Name, Country, Client Name, Type of Procurement,
#               Business Sector, Notice Type, Publication Date, EBRD Project ID
#   on the 11:  ECEPP ID, Procurement Exercise Name, Procurement Method, Issue Date,
#               Closing Date
#   on 9 of 11: Procurement Exercise Description
#   on neither GPN: all five of the second group and the description
#
# A GPN announces a project's whole procurement programme rather than one bid, so
# it has no exercise, no method and no closing date - the same shape the World Bank
# publishes and for the same reason. Requiring any of those would have failed this
# source on the first GPN to enter the window, which is 18% of what it publishes.
OVERVIEW_TABLE = "table#oppoverviewtable"
DETAIL_PROJECT_NAME = "Project Name"
DETAIL_COUNTRY = "Country"
DETAIL_CLIENT = "Client Name"
DETAIL_EXERCISE_NAME = "Procurement Exercise Name"
DETAIL_DESCRIPTION = "Procurement Exercise Description"
DETAIL_NOTICE_TYPE = "Notice Type"
DETAIL_PUBLISHED = "Publication Date"
DETAIL_CLOSING = "Closing Date"
REQUIRED_DETAIL_LABELS = (
    DETAIL_PROJECT_NAME,
    DETAIL_COUNTRY,
    DETAIL_CLIENT,
    DETAIL_NOTICE_TYPE,
    DETAIL_PUBLISHED,
)

NOTICE_URL = "https://ecepp.ebrd.com/delta/viewNotice.html?displayNoticeId={notice_id}"
NOTICE_ID = re.compile(r"displayNoticeId=(\d+)")

# dd/mm/yyyy, which is what this portal publishes and what
# `monitor/normalise/dates.py` deliberately refuses: it parses only forms where
# which number is the day is unambiguous, and says a source publishing an ambiguous
# one "needs its own mapper deciding which it means, not a guess made here". This
# is that decision, and it is measured rather than assumed - see `iso_timestamp`.
UK_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$")

# The clock this portal writes when a form was filled in with a date and no time.
# See `iso_timestamp`.
NO_TIME_STATED = (0, 0)

# Runs of spaces and tabs inside one line of a cell. The page's templates indent
# inside text nodes, so `Notice Type` arrives as "Invitation<20 spaces>For<20
# spaces>Tenders", which no reader ever sees: a browser collapses it. Collapsing it
# here is reading the cell as published rather than altering it, and it is done
# line by line so the line breaks a client typed into a description survive.
INLINE_SPACE = re.compile(r"[ \t\r\f\v]+")


class EbrdConnector(FeedConnector):
    """One listing of the whole archive, then a detail page per in-scope notice."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: there is no CPV code anywhere in this source, so every notice
        # reaches the lexicon stage. Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def cutoff(self, today: date | None = None) -> date:
        """The oldest publication date this run keeps. Daily schedule, 30-day window."""
        return (today or date.today()) - timedelta(days=LOOKBACK_DAYS)

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        cutoff = self.cutoff()
        listing = client.get(self.source.list_url)
        listing.raise_for_status()

        rows = parse_listing(
            listing.text,
            covers=self.source.covers,
            excluded_types=self.source.exclude_notice_types,
        )
        wanted = in_scope(rows, cutoff=cutoff, excluded_types=self.source.exclude_notice_types)

        ceiling = min(self.source.expected_max, MAX_DETAIL_REQUESTS)
        if len(wanted) > ceiling:
            log.warning(
                "ebrd_ceiling_reached",
                in_scope=len(wanted),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/ebrd.yaml",
            )

        raw_notices: list[RawNotice] = []
        for row in wanted[:ceiling]:
            payload = {"listing": row, "detail": self.fetch_detail(client, row)}
            raw_notices.append(
                self.raw_notice(
                    url=NOTICE_URL.format(notice_id=row["notice_id"]),
                    payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
            )

        log.info(
            "ebrd_fetch",
            archive_rows=len(rows),
            in_scope=len(wanted),
            fetched=len(raw_notices),
            cutoff=cutoff.isoformat(),
        )
        return raw_notices

    def fetch_detail(self, client: httpx.Client, row: dict) -> dict:
        """The notice's own field table, checked against the two things the scope used.

        Both checks are of a decision this connector already took against the value
        the notice itself states, because there is no server-side filter to hold to
        account here - the scope is the connector's own and nothing else verifies it.

          - Country. A disagreement means the membership test matched something that
            is not the country field - a project or client name containing
            `", Ukraine,"`, say - and the notice would arrive filed under the wrong
            geography weight.
          - Notice type. This is what `exclude_notice_types` was applied to, so a
            disagreement means 1,109 decided tenders were sorted on one string and
            kept or dropped on the strength of another. Matched on all eleven pages
            one run fetched.
        """
        response = client.get(NOTICE_URL.format(notice_id=row["notice_id"]))
        response.raise_for_status()
        detail = parse_detail(response.text, notice_id=row["notice_id"])

        if detail[DETAIL_COUNTRY] != row["country_name"]:
            raise ValueError(
                f"EBRD notice {row['notice_id']} is filed under {detail[DETAIL_COUNTRY]!r} on its own page but "
                f"{row['country_name']!r} in the listing metadata this run scoped on; the membership test matched "
                "something that is not the country field"
            )
        if detail[DETAIL_NOTICE_TYPE] != row["notice_type"]:
            raise ValueError(
                f"EBRD notice {row['notice_id']} is a {detail[DETAIL_NOTICE_TYPE]!r} on its own page but a "
                f"{row['notice_type']!r} in the listing this run applied exclude_notice_types to"
            )
        return detail


def parse_listing(markup: str, *, covers: list[str], excluded_types: list[str]) -> list[dict]:
    """Every row of the archive, checked for shape and for both vocabularies.

    Four checks, in the order a failure is cheapest to explain:

      0. That the results table is on the page at all, as every other connector
         checks its container. Without this, a renamed table would be an archive of
         zero rows and the next check would report it as seven vanished countries.
      1. The table's ten cells per row, so a changed page raises instead of
         yielding fewer notices.
      2. That every covered country still appears somewhere in the archive. This
         guard is only possible because the response is the whole archive, and it
         closes the hole `monitor/connectors/worldbank.py` could only name: a
         country whose spelling changed returns nothing, and nothing is
         indistinguishable from a quiet day. Over eleven years it is not. Kosovo
         becoming "Kosovo (the Republic of)" would drop 83 notices' worth of
         geography and read as a healthy run; here it raises.
      3. That every notice type the registry excludes is a type this source
         actually publishes. A typo in `exclude_notice_types` would exclude nothing
         and the run would quietly carry 1,109 decided tenders into the pipeline.
    """
    tree = HTMLParser(markup)
    if tree.css_first(RESULTS_TABLE) is None:
        raise ValueError(f"EBRD listing has no {RESULTS_TABLE!r}; the notice search page changed")

    rows: list[dict] = []
    for position, element in enumerate(tree.css(RESULTS_ROWS)):
        cells = element.css("td")
        if len(cells) != CELLS_PER_ROW:
            raise ValueError(
                f"EBRD listing row {position} has {len(cells)} cells, not {CELLS_PER_ROW}; the results table changed"
            )
        values = [cell_text(cell) for cell in cells]
        link = cells[CELL_TITLE].css_first("a")
        if link is None:
            raise ValueError(f"EBRD listing row {position} has no notice link in its title cell")
        match = NOTICE_ID.search(link.attributes.get("href") or "")
        if match is None:
            raise ValueError(
                f"EBRD listing row {position} links to {link.attributes.get('href')!r}, "
                "which carries no displayNoticeId"
            )
        rows.append(
            {
                "notice_id": match.group(1),
                "title": values[CELL_TITLE],
                "notice_type": values[CELL_NOTICE_TYPE],
                "exercise_title": values[CELL_EXERCISE_TITLE],
                "published_at": values[CELL_PUBLISHED_AT],
                "closing_at": values[CELL_CLOSING_AT],
                "state": values[CELL_STATE],
                "published_date": values[CELL_PUBLISHED_DATE],
                "metadata": values[CELL_METADATA],
                "country_name": country_name(values[CELL_METADATA], covers=covers),
            }
        )

    archive = "\n".join(row["metadata"] for row in rows)
    for code in covers:
        if not any(field(name) in archive for name in ebrd_names(code)):
            raise ValueError(
                f"no notice in EBRD's {len(rows)}-row archive names {ebrd_names(code)} as its country, so the "
                f"scope for {code} now matches nothing; check what the portal calls it and update "
                "EBRD_COUNTRY_NAMES in monitor/connectors/ebrd.py"
            )

    published_types = {row["notice_type"] for row in rows}
    for excluded in excluded_types:
        if excluded not in published_types:
            raise ValueError(
                f"sources/ebrd.yaml excludes notice type {excluded!r}, which no notice in the archive carries; "
                f"the exclusion matches nothing. EBRD publishes {sorted(published_types)}"
            )
    return rows


def in_scope(rows: list[dict], *, cutoff: date, excluded_types: list[str]) -> list[dict]:
    """The rows this run reads: inside the window, in a covered country, wanted type.

    Every row is looked at. The server's order is not newest-first - see hazard two
    in the module docstring - so this is a filter over the whole archive rather than
    a cut at the first old row, and nothing here depends on how the page is sorted.

    The notice-type exclusion is the registry's (`exclude_notice_types`), which the
    `Source` model documents as acquisition scope rather than a filter stage: it
    decides what to read, not what to keep. Applied here and not at the query
    because this source has no query at all.
    """
    wanted: list[dict] = []
    for row in rows:
        if published_date(row) < cutoff:
            continue
        if row["metadata"] == "[]":
            raise ValueError(
                f"EBRD notice {row['notice_id']} ({row['published_date']}) carries an empty metadata cell, so it "
                "states no country and cannot be scoped; dropping it would be silent"
            )
        if row["country_name"] is None:
            continue
        if row["notice_type"] in excluded_types:
            continue
        wanted.append(row)
    return wanted


def country_name(metadata: str, *, covers: list[str]) -> str | None:
    """The covered country this row is for, or None where it is not a covered one.

    A membership test for `", <name>,"` in the metadata cell, which is a flat
    bracketed field list that cannot be split on commas. Exactly one covered name
    may match: two would mean the test is matching a project or client name and the
    country can no longer be read from this cell.
    """
    matched = sorted(
        {ISO2_BY_EBRD_NAME[name] for code in covers for name in ebrd_names(code) if field(name) in metadata}
    )
    if len(matched) > 1:
        raise ValueError(
            f"EBRD listing row names more than one covered country ({matched}) in its metadata cell, so the "
            f"country cannot be read from it: {metadata[:200]!r}"
        )
    if not matched:
        return None
    names = ebrd_names(matched[0])
    return next(name for name in names if field(name) in metadata)


def ebrd_names(code: str) -> tuple[str, ...]:
    """EBRD's spellings of one ISO code. An unmapped code raises.

    Scoping on six of seven countries would read as a quiet week in the seventh.
    """
    if code not in EBRD_COUNTRY_NAMES:
        raise ValueError(
            f"no EBRD country name for {code!r}; add it to EBRD_COUNTRY_NAMES in monitor/connectors/ebrd.py "
            "after checking what the portal's own notice metadata calls it"
        )
    return EBRD_COUNTRY_NAMES[code]


def field(name: str) -> str:
    """A country name as it appears in the metadata cell: one comma-delimited field."""
    return f", {name},"


def published_date(row: dict) -> date:
    """The row's publication date, which is dd/mm/yyyy. Raises on anything else.

    The window cut reads this, so an unparseable value is a changed page rather
    than a row to skip.
    """
    raw = row["published_date"]
    match = UK_DATE.match(raw)
    if match is None:
        raise ValueError(f"EBRD notice {row['notice_id']} has published date {raw!r}, not dd/mm/yyyy")
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return date(year, month, day)
    except ValueError as cause:
        raise ValueError(f"EBRD notice {row['notice_id']} has published date {raw!r}: {cause}") from cause


def parse_detail(markup: str, *, notice_id: str) -> dict:
    """One notice's field table as a label to value mapping.

    The overview table is the notice's structured record and is all that is stored.
    The prose block below it (`div.completeentry`) is deliberately not read: on an
    Invitation for Tenders it is the same four paragraphs of funding and
    registration boilerplate on every notice, and the only notice-specific parts of
    it are the client's postal address with a named officer's phone and personal
    email, and - on a Shortlist Notice - the names and countries of the shortlisted
    firms. A named contact or a third party's business record reaching a model call
    is blocking (rule 19), and `Notice.body` is read by the scorer. What is
    substantive is `Procurement Exercise Description`, which is in the table.
    """
    table = HTMLParser(markup).css_first(OVERVIEW_TABLE)
    if table is None:
        raise ValueError(f"EBRD notice {notice_id} has no {OVERVIEW_TABLE!r}; the detail page changed")

    detail: dict[str, str] = {}
    for position, element in enumerate(table.css("tr")):
        cells = element.css("td")
        if len(cells) != 2:
            raise ValueError(
                f"EBRD notice {notice_id} field row {position} has {len(cells)} cells, not 2; the detail page changed"
            )
        label = cell_text(cells[0]).removesuffix(":").strip()
        if not label:
            raise ValueError(f"EBRD notice {notice_id} field row {position} has no label")
        if label in detail:
            raise ValueError(
                f"EBRD notice {notice_id} states {label!r} twice, so which value is the notice's is undecidable"
            )
        detail[label] = cell_text(cells[1])

    missing = [label for label in REQUIRED_DETAIL_LABELS if not detail.get(label)]
    if missing:
        raise ValueError(f"EBRD notice {notice_id} is missing {missing}")
    return detail


def cell_text(cell) -> str:
    """One table cell as published: line breaks kept, template indentation removed.

    Non-breaking spaces become ordinary ones. They are HTML layout rather than the
    publisher's prose, and a phrase written with one in the middle would not match
    the lexicon, which matches whole phrases with ordinary spaces in them: the
    notice would be dropped for containing the phrase in the wrong kind of space.
    """
    lines = cell.text(separator="\n").replace("\xa0", " ").split("\n")
    kept = [collapsed for line in lines if (collapsed := INLINE_SPACE.sub(" ", line).strip())]
    return "\n".join(kept)


def iso_timestamp(raw: str, *, notice_id: str, label: str) -> str | None:
    """A dd/mm/yyyy [HH:MM] stamp as an ISO 8601 string, or None where absent.

    Converted rather than parsed here so the shared rules in
    `monitor/normalise/dates.py` still decide what a date with no time of day means.
    That module refuses dd/mm/yyyy on purpose, because which number is the day is
    ambiguous and a guess made centrally would be wrong for somebody. For this
    portal it is not a guess: over the 4,050 rows in the archive the first number
    runs 1 to 31 and the second never exceeds 12, and the hidden sort key beside
    each row spells the same instant out as yyyymmddHHMM. Day first.

    **A stated 00:00 is read as no time of day, not as the instant a day begins.**
    It is this portal's data-entry default - two of the eleven closing dates one run
    fetched are 00:00, and one of those notices states 00:00 for its issue date as
    well. Read literally, a tender "closing 19/10/2026 00:00" would be recorded as
    shut before the 19th started, and `monitor/normalise/dates.py` already pushes a
    date with no time to the end of its day for exactly that reason: a tender
    closing on the 19th is open on the 19th. Dropping the clock here hands both
    callers the same shared rule rather than writing a second one. On a publication
    date the two readings are the same instant anyway.

    Empty is None and unparseable raises. The two are different: this source leaves
    `Closing Date` out of a notice that has no closing date - every General
    Procurement Notice, 715 of the archive - and that is a notice to map rather than
    a page that changed.
    """
    if not raw:
        return None
    match = UK_DATE.match(raw)
    if match is None:
        raise ValueError(f"EBRD notice {notice_id} has {label} {raw!r}, not dd/mm/yyyy [HH:MM]")

    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        stamped = date(year, month, day)
    except ValueError as cause:
        raise ValueError(f"EBRD notice {notice_id} has {label} {raw!r}: {cause}") from cause

    if match.group(4) is None:
        return stamped.isoformat()
    hour, minute = int(match.group(4)), int(match.group(5))
    if hour > 23 or minute > 59:
        raise ValueError(f"EBRD notice {notice_id} has {label} {raw!r}, which is not a time of day")
    if (hour, minute) == NO_TIME_STATED:
        return stamped.isoformat()
    return datetime(year, month, day, hour, minute).isoformat()
