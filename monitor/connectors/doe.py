"""Datenservice Öffentlicher Einkauf, Germany's federal procurement publication.

Recorded from real calls on 2026-09-12. The fixture is
`tests/contract/fixtures/doe.json` and this parser is written against the fields
that are in it.

The platform publishes eForms-DE and offers four bulk representations of a day's
notices, chosen by `format=<name>.zip` on the export endpoint or by the matching
`Accept` header. Probing all four is the whole reason this connector is shaped the
way it is, because **two of the three obvious choices lose data in silence**.
Measured on `pubDay=2026-09-11`, which published 975 notices:

  - `format=eforms.zip` - 975 eForms-DE UBL XML documents. Complete, and the
    published record. Not used: `monitor/fetch.py` stores a payload by
    `json.loads`, so an XML payload cannot pass through the shared acquire stage,
    and that file is not this connector's to change. Reported rather than worked
    around.
  - `format=ocds.zip` - 975 OCDS 1.1 release packages. Complete and JSON, and the
    representation this connector would otherwise have been built on. Not used:
    it carries **no submission deadline at all**. Every JSON path across all 975
    releases was enumerated - 150 of them - and there is no `tenderPeriod`
    anywhere, in the bulk export or on the per-notice endpoint. It also carries no
    buyer legal type, which is the one field that decides `admin_level`, and no
    `language` on 396 of the 975.
  - `format=ocds2.zip` - the OCDS eForms profile. Carries the deadline and the
    buyer legal type, and **silently omits 484 of the 975 notices**, 333 of them
    live `cn-standard` contract notices: half of the day's opportunities, with no
    marker in the response and a 200 on the request. Probing why settled it - the
    per-notice endpoint answers `HTTP 500` for every omitted notice tested, so the
    bulk export is swallowing its own converter's failures. A connector built on
    it would report a healthy run having missed half of Germany, which is the
    exact failure rule 4 exists against.
  - `format=csv.zip` - 19 relational CSVs covering all 975. No submission deadline
    either (`submissionTerms.csv` has the offer's validity period and the public
    opening date, not the closing date).

So the listing and the notice come from different endpoints, which is Prozorro's
shape and for the same reason: one request gets the day's metadata, and the
notices worth reading are then fetched one at a time. `notice.csv` and
`classification.csv` out of the day's `csv.zip` carry the notice id, its version,
its form type and its CPV codes and no prose at all, so restricting on them
decides what to read rather than what to keep (rule 5), the same acquisition-scope
decision as TED's `exclude_notice_types` and Prozorro's `OPEN_STATUSES`. The
notice itself is then `?format=domain`, the platform's own complete JSON rendering
of the eForms document: it carries the deadline, the German buyer legal type, the
CPV codes, the NUTS region and the text, and it answered 200 for every notice
tested including all four that `ocds2` fails on.

Measured cost: 661 of the day's 975 notices survive the form-type exclusion and 90
survive the CPV prefix test, so a run over two days makes 2 listing requests and
127 to 239 detail requests. The five days sampled selected 143, 96, 90, 70 and 57.

Two more things probing settled that documentation would not have:

  1. **Every query parameter except the date is ignored in silence.**
     `cpv=72000000`, `noticeType=cn-standard`, `page=0` and a deliberately invented
     `totallyBogusParam=xyz` each returned a response byte-identical to the
     unfiltered one (3,847,323 bytes). There is no server-side filter to lean on
     beyond the day, so this connector asks for a day and nothing else, and checks
     what came back.
  2. **The date parameter, unlike those, is real and strict.** `pubDay=bogus`,
     a malformed date, an empty value and two `pubDay` values all return 400, and
     `pubDay` with `pubMonth` is refused outright. Three different days returned
     disjoint id sets. It is still checked per row, because 2 of the 1,094 rows on
     `pubDay=2026-09-09` carried a `publicationDate` of the day before, so the
     export groups by publication batch rather than strictly by that column.

`noticeVersion` is part of the key, not decoration: one notice was published twice
on 2026-09-11, as version `1` (a contract notice) and version `2` (a correction 16
minutes later). The detail endpoint honours the string exactly - `01` and `1` are
different notices and a wrong one is a 404 - so the listing's value is passed
through verbatim.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The two representations this connector asks for. `format` is content negotiation
# by query parameter and is equivalent to the `Accept` header the 406 response
# advertises; both were verified to return identical bytes on 2026-09-12.
LISTING_FORMAT = "csv.zip"
DETAIL_FORMAT = "domain"

# The per-notice endpoint. `api_url` in the registry is the export endpoint, which
# is the one a run starts from; this is the second stage and has no registry field.
DETAIL_URL = "https://oeffentlichevergabe.de/api/notices/{notice_id}"

# The human page for one notice, which is what the reviewer opens. `/notices/:noticeId`
# is the route in the platform's own client-side router; the server returns the
# single-page-app shell for it and renders the notice in the browser, so a 200 here
# is the shell and not proof of the notice. There is no server-rendered notice page
# to link to instead.
NOTICE_URL = "https://oeffentlichevergabe.de/ui/de/notices/{notice_id}"

# The export is keyed to one completed publication day. Two days per run, which is
# the same window TED and the World Bank use and the only resilience available
# under rule 2: a run that does not happen is picked up by the next one, and the
# content hash makes the re-read free downstream. Today is deliberately not asked
# for - the day's publication batches ran to 19:00 UTC on the day measured, so
# asking for it would read a partial day, and a partial day is indistinguishable
# from a quiet one (rule 4).
LOOKBACK_DAYS = 2

# The absolute bound on detail requests per run, above the registry's expected_max.
# Germany's whole procurement publication is behind this endpoint and a quiet
# assumption about volume is how a polite pass becomes an impolite one (rule 21).
MAX_DETAIL_REQUESTS = 600

# The two files read out of the day's archive. The archive holds 19; the other 17
# are lot, contract, award, duration and purpose tables, and purpose.csv is the one
# that carries titles and descriptions. They are not opened: this stage decides
# what to read, and reading the prose here and then discarding it would be the
# filter stage doing acquisition's job.
NOTICE_FILE = "notice.csv"
CLASSIFICATION_FILE = "classification.csv"

# Every column the listing parser reads. A renamed column raises rather than
# yielding fewer notices (rule 4). `procedureIdentifier` and `procedureLegalBasis`
# are in the file and are deliberately not required: nothing here reads them.
NOTICE_COLUMNS = ("noticeIdentifier", "noticeVersion", "formType", "noticeType", "publicationDate")
CLASSIFICATION_COLUMNS = (
    "noticeIdentifier",
    "noticeVersion",
    "classificationType",
    "mainClassificationCode",
    "additionalClassificationCodes",
)

# The only classification scheme in the file: 2,172 of the 2,231 rows on
# 2026-09-11 say `cpv` and the other 59 say nothing at all and carry no code, one
# per notice whose classification block is empty. A third value would be a scheme
# this connector has never seen and it raises.
CPV_TYPE = "cpv"

# A CPV code as this source writes it, which is four different ways. Measured over
# the 2,231 classification rows of 2026-09-11: 1,538 are the plain eight digits,
# 328 carry the check digit ("45112000-5"), 298 are a bare two-digit division
# ("45", "50", "32", "42", "73") and 8 are several codes in one field separated by
# spaces ("90713000-8 80590000-6"). The irregular three all come from the legacy
# feed, whose notices carry `dataSource: SERVICE_BUND_DE`.
#
# So this reads code *tokens* rather than reusing `monitor/normalise/cpv.py`'s
# extractor, and that is not a second selector for the same value (rule 1) but a
# different question. That module answers "what is the stored eight-digit form",
# and it is right to refuse a bare "45" because no eight-digit form exists without
# inventing six digits. This answers "does the classification the buyer published
# start with a pass prefix", which a two-digit division answers perfectly well -
# and if it were dropped instead, the 298 rows that carry one would read as
# unclassified and every construction tender among them would be fetched.
CPV_TOKEN = re.compile(r"\d{2,8}")

# How far a row's own `publicationDate` may fall outside the day asked for. One
# day, because 2 of the 1,094 rows on `pubDay=2026-09-09` carried 2026-09-08.
DAY_SLACK = timedelta(days=1)

# Every field the mapper reads on every notice. Deliberately absent, with the
# reason: `noticeOfficialLanguages` (the legacy feed declares no language),
# `classification` (empty on notices classified only at lot level), `tedPublication`
# and `procedureIdentifier` (only on notices that went to TED), `additionalInformation`.
REQUIRED_DETAIL_FIELDS = (
    "noticeIdentifier",
    "noticeVersion",
    "publicationDate",
    "noticeType",
    "organisation",
    "organisationRoles",
    "buyers",
    "purpose",
    "lots",
)


@dataclass(frozen=True)
class Listing:
    """One row of the day's listing: what is needed to decide whether to read it."""

    notice_id: str
    version: str
    form_type: str
    notice_type: str
    published_at: str
    cpv_codes: tuple[str, ...]


class DoeConnector(FeedConnector):
    """One listing request per completed day, then one request per notice kept."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # From config/thresholds.yaml, never hardcoded here (rule 6). Used at the
        # listing stage, which is where TED uses the same value in its query.
        self.cpv_prefixes = cpv_prefixes

    def days(self, today: date | None = None) -> list[date]:
        """The completed publication days this run reads, newest first."""
        reference = today or date.today()
        return [reference - timedelta(days=offset) for offset in range(1, LOOKBACK_DAYS + 1)]

    def listing_params(self, day: date) -> dict:
        return {"pubDay": day.isoformat(), "format": LISTING_FORMAT}

    def detail_params(self, row: Listing) -> dict:
        return {"format": DETAIL_FORMAT, "noticeVersion": row.version}

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        ceiling = min(self.source.expected_max, MAX_DETAIL_REQUESTS)
        raw_notices: list[RawNotice] = []

        for day in self.days():
            rows = self.fetch_listing(client, day)
            selected = select(
                rows,
                exclude_form_types=self.source.exclude_notice_types,
                cpv_prefixes=self.cpv_prefixes,
            )
            log.info("doe_listing", day=day.isoformat(), published=len(rows), selected=len(selected))

            for row in selected:
                if len(raw_notices) >= ceiling:
                    log.warning(
                        "doe_ceiling_reached",
                        fetched=len(raw_notices),
                        ceiling=ceiling,
                        detail="raise expected_items_per_run in sources/doe.yaml",
                    )
                    return raw_notices
                detail = self.fetch_detail(client, row)
                raw_notices.append(
                    self.raw_notice(
                        url=notice_url(row.notice_id),
                        payload=json.dumps(detail, ensure_ascii=False, sort_keys=True),
                        mime="application/json",
                    )
                )

        log.info("doe_fetch", notices=len(raw_notices), days=[day.isoformat() for day in self.days()])
        return raw_notices

    def fetch_listing(self, client: httpx.Client, day: date) -> list[Listing]:
        """One day's metadata. One attempt; any failure raises (rules 2 and 3)."""
        response = client.get(self.source.api_url, params=self.listing_params(day))
        response.raise_for_status()
        return parse_listing(read_archive(response.content), day)

    def fetch_detail(self, client: httpx.Client, row: Listing) -> dict:
        """One notice, as the platform's own JSON rendering of its eForms document."""
        response = client.get(DETAIL_URL.format(notice_id=row.notice_id), params=self.detail_params(row))
        response.raise_for_status()
        return check_detail(response.json(), row)


def read_archive(archive: bytes) -> dict[str, str]:
    """The two listing files out of the day's export archive, as text.

    Raises when either is absent, because an archive that no longer carries the
    notice table is a changed export and not a quiet day.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        names = set(bundle.namelist())
        missing = [name for name in (NOTICE_FILE, CLASSIFICATION_FILE) if name not in names]
        if missing:
            raise ValueError(f"DÖE export archive is missing {missing}; got {sorted(names)}")
        # utf-8-sig: the service writes a byte-order mark, and without stripping it
        # the first column name would be "﻿noticeIdentifier" and every row
        # would look like it was missing its id.
        return {name: bundle.read(name).decode("utf-8-sig") for name in (NOTICE_FILE, CLASSIFICATION_FILE)}


def parse_listing(files: dict[str, str], day: date) -> list[Listing]:
    """One day's listing rows, checked for columns, for the day and for uniqueness."""
    codes = parse_classifications(files[CLASSIFICATION_FILE])
    reader = _reader(files[NOTICE_FILE], NOTICE_FILE, NOTICE_COLUMNS)

    rows: list[Listing] = []
    seen: set[tuple[str, str]] = set()
    for position, row in enumerate(reader):
        key = (row["noticeIdentifier"], row["noticeVersion"])
        if not key[0] or not key[1]:
            raise ValueError(f"{NOTICE_FILE} row {position} has no notice id or version: {row}")
        if key in seen:
            # The same id appears twice in a day as two versions, which is normal.
            # The same id *and* version twice would mean the listing is not keyed
            # the way the detail endpoint is, and the walk would read one notice
            # twice while believing it read two.
            raise ValueError(f"{NOTICE_FILE} has {key[0]} version {key[1]} twice")
        seen.add(key)

        published = row["publicationDate"]
        _check_day(published, day, key[0])
        rows.append(
            Listing(
                notice_id=key[0],
                version=key[1],
                form_type=row["formType"],
                notice_type=row["noticeType"],
                published_at=published,
                cpv_codes=tuple(codes.get(key, ())),
            )
        )
    return rows


def parse_classifications(text: str) -> dict[tuple[str, str], list[str]]:
    """CPV code tokens per (notice id, version), from every lot row of the day."""
    reader = _reader(text, CLASSIFICATION_FILE, CLASSIFICATION_COLUMNS)

    codes: dict[tuple[str, str], list[str]] = {}
    for position, row in enumerate(reader):
        kind = row["classificationType"].strip().lower()
        written = (row["mainClassificationCode"], row["additionalClassificationCodes"])

        if kind and kind != CPV_TYPE:
            raise ValueError(f"{CLASSIFICATION_FILE} row {position} uses scheme {kind!r}, which is not {CPV_TYPE!r}")
        if not kind:
            if any(value.strip() for value in written):
                raise ValueError(f"{CLASSIFICATION_FILE} row {position} states a code under no scheme: {row}")
            # An empty classification block, 59 of 2,231 rows on 2026-09-11.
            continue

        key = (row["noticeIdentifier"], row["noticeVersion"])
        for code in classification_codes(*written):
            bucket = codes.setdefault(key, [])
            if code not in bucket:
                bucket.append(code)
    return codes


def classification_codes(*values: str) -> list[str]:
    """Every CPV code or division written in the given fields, in order seen.

    As written, not normalised: see `CPV_TOKEN`. `monitor/normalise/cpv.py` is what
    produces the eight-digit form stored on the notice.
    """
    found: list[str] = []
    for value in values:
        for match in CPV_TOKEN.finditer(value or ""):
            if match.group(0) not in found:
                found.append(match.group(0))
    return found


def select(rows: list[Listing], *, exclude_form_types: list[str], cpv_prefixes: list[str]) -> list[Listing]:
    """The notices this run reads: still open, and classified in the pass prefixes.

    Both cuts are made before a notice is fetched, so nothing is read and then
    thrown away (rule 5). The form types come from the registry and the prefixes
    from `config/thresholds.yaml`; neither is decided here (rule 6).
    """
    excluded = frozenset(exclude_form_types)
    return [row for row in rows if row.form_type not in excluded and passes_cpv(row.cpv_codes, cpv_prefixes)]


def passes_cpv(codes: tuple[str, ...], prefixes: list[str]) -> bool:
    """Does this notice's classification let it through to be read?

    Same asymmetry as `monitor/filter/cpv.py`, deliberately: codes present and
    none passing means the buyer classified the notice and said it is not
    software, IT services or consultancy; no codes at all is not a failed match.
    21 to 43 of the notices selected each day are unclassified ones arriving this
    way, and the lexicon stage gets its turn on them.
    """
    if not codes:
        return True
    return any(code.startswith(prefix) for code in codes for prefix in prefixes)


def check_detail(document: dict, row: Listing) -> dict:
    """One detail response, checked against what was asked for and what is read.

    The identity check is not ceremony. Every parameter on the export endpoint
    except the date is ignored in silence, so `noticeVersion` being honoured is
    asserted rather than assumed: a detail endpoint that started ignoring it would
    hand back the latest version of a corrected notice under the old version's key,
    and the content hash would make that look like a new notice every day.
    """
    missing = [field for field in REQUIRED_DETAIL_FIELDS if field not in document]
    if missing:
        raise ValueError(f"DÖE notice {row.notice_id} version {row.version} is missing {missing}")

    if document["noticeIdentifier"] != row.notice_id or document["noticeVersion"] != row.version:
        raise ValueError(
            f"DÖE returned notice {document['noticeIdentifier']!r} version "
            f"{document['noticeVersion']!r} for a request for {row.notice_id!r} version {row.version!r}"
        )
    return document


def notice_url(notice_id: str) -> str:
    return NOTICE_URL.format(notice_id=notice_id)


def _reader(text: str, filename: str, required: tuple[str, ...]) -> csv.DictReader:
    reader = csv.DictReader(io.StringIO(text))
    missing = [column for column in required if column not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"{filename} is missing columns {missing}; got {reader.fieldnames}")
    return reader


def _check_day(published: str, day: date, notice_id: str) -> None:
    """The row's own publication date, against the day that was asked for."""
    parsed = _publication_day(published, notice_id)
    if abs(parsed - day) > DAY_SLACK:
        raise ValueError(
            f"DÖE notice {notice_id} was published {parsed.isoformat()} but the listing asked for "
            f"{day.isoformat()}; the pubDay parameter was ignored and the response is another day"
        )


def _publication_day(published: str, notice_id: str) -> date:
    """The day out of `2026-09-11T02:04:04Z` or `2026-09-11T00:00:00+02:00`.

    Read as the calendar day the service wrote, without shifting it into another
    zone: the export groups notices by publication day in Berlin time and the
    check above is against that grouping, not against an instant.
    """
    head = published.strip()[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError as cause:
        raise ValueError(f"DÖE notice {notice_id} has publicationDate {published!r}: {cause}") from cause
