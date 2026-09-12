"""simap.ch, Switzerland's federal and cantonal publication platform.

Recorded from real calls on 2026-09-12. The fixture is
`tests/contract/fixtures/simap.json` and this parser is written against the fields
that are in it.

There is no documented API. simap.ch is a server-rendered React application, and
`/assets/js/main.js` carries its own generated API client: the search call is
`getPublicProjectSearch`, a GET of
`/api/publications/v2/project/project-search` whose nineteen query parameters are
named there. No key, no registration, no session, no CSRF token: the same request
with only an identified user agent answers 200 from a cold client. The three
endpoints this connector uses were each confirmed against the live host before a
line of the parser was written.

robots.txt matters here and shapes the design. It disallows `/{lang}/project-detail`
in all four site languages, which is the human notice page. Nothing under `/api` is
disallowed. So this connector reads the JSON API and never fetches a notice page -
`NOTICE_URL` is built for the reviewer to click and is not requested by anything
here (rule 21).

**The hazard this connector is built against: the endpoint is project-centric, not
publication-centric.** `project-search` returns one row per project carrying that
project's *newest* publication, and `newestPublicationFrom/Until` and
`newestPubTypes` both bound that newest publication. Measured on 2026-09-12: a
two-day window returned 195 rows with 195 distinct project ids and 195 distinct
publication ids - never two rows for one project. The consequence is the opposite
of the usual one: **a longer lookback loses notices.** A project whose tender is
published on Monday and whose award lands on Wednesday is, in any window that
reaches Wednesday, a single row of type `award`, and the type filter then drops the
project entirely - taking Monday's tender with it. A short window run often is what
keeps that from happening, so `LOOKBACK_DAYS` is a correctness bound and not a
politeness one.

Three more measured facts that shaped the request:

  1. **Unknown query parameters are accepted with a 200 and ignored in silence.**
     `nonsenseParameter=1` alongside a date bound returned the same 20 rows as the
     bound alone. Known parameters with bad values are refused loudly
     (`newestPubTypes=nope`, `lang=DE` and `cpvCodes=72` are each a 400 with
     `E0025 Invalid parameter`), so the danger is only in the names. That is why
     `parse_page` checks that every row that came back is inside the window and
     inside the asked-for types: a renamed date or type parameter would otherwise
     read the whole platform and report a healthy run.
  2. **`cpvCodes` is hierarchical and takes eight-digit codes only.** Asking for
     `48000000,72000000,79000000` returned notices whose own codes are 79420000,
     72300000, 48100000, 72230000, 48921000 and 79330000, so a division root
     matches the division. `cpvCodes=72` is a 400. Values are comma separated.
  3. **The filter's type vocabulary is not the result's.** `newestPubTypes=award`
     is a 400; the three award values the filter accepts are `award_tender`,
     `award_competition` and `award_study_contract`, and each returns rows whose
     `pubType` reads `award` or `direct_award`. The five live types map to
     themselves. `RESULT_PUB_TYPES` records that measured mapping, because the
     guard in `parse_page` has to know what the server may legitimately answer
     with.

Volume, measured over 2026-08-01 to 2026-09-12 with the CPV and type filters both
applied: 151 projects, about 3.6 a day. Without the CPV filter the same two days
hold 118 live-type projects, so the CPV query is what makes a detail fetch per
notice a polite pass rather than an inconsiderate one.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The search returns a project's newest publication only, so a window that spans
# more than a run's own gap starts hiding tenders behind later awards. Two days on
# a daily schedule gives one run of overlap and no more. See the module docstring.
LOOKBACK_DAYS = 2

# `itemsPerPage` is 20 and no parameter changes it; the response states it and the
# walk reads it from there. 20 pages is 400 projects, against a measured 3.6 a day.
MAX_PAGES = 20

# The absolute bound on detail requests in one run, above the registry's
# expected_max. One detail is 40 to 130 KB, so an unexamined assumption about
# volume is how a polite pass becomes an impolite one (rule 21).
MAX_DETAIL_REQUESTS = 100

# Every value `newestPubTypes` accepts, verified one at a time against the live API
# on 2026-09-12: each of these returns 200, and `award`, `correction` and `qna` -
# all three of which appear in the front end's own enums - return 400. The registry
# says which of these to exclude; this is the vocabulary that list is checked
# against, so a typo in the YAML fails before the request rather than silently
# widening it.
FILTER_PUB_TYPES = frozenset(
    {
        "advance_notice",
        "request_for_information",
        "tender",
        "competition",
        "study_contract",
        "participant_selection",
        "selective_offering_phase",
        "direct_award",
        "award_tender",
        "award_competition",
        "award_study_contract",
        "abandonment",
        "revocation",
    }
)

# What each filter value may come back as in a row's `pubType`. Identity for the
# live types; the three award filters answer with the coarser `award` and, measured
# on 2026-09-12, also with `direct_award` - `award_competition` returned 6 `award`
# rows and 3 `direct_award` ones. The guard in `parse_page` needs this mapping
# rather than the filter list, or asking for an award type would raise on the
# server's own honest answer.
RESULT_PUB_TYPES = {
    "advance_notice": frozenset({"advance_notice"}),
    "request_for_information": frozenset({"request_for_information"}),
    "tender": frozenset({"tender"}),
    "competition": frozenset({"competition"}),
    "study_contract": frozenset({"study_contract"}),
    "participant_selection": frozenset({"participant_selection"}),
    "selective_offering_phase": frozenset({"selective_offering_phase"}),
    "direct_award": frozenset({"direct_award"}),
    "award_tender": frozenset({"award", "direct_award"}),
    "award_competition": frozenset({"award", "direct_award"}),
    "award_study_contract": frozenset({"award", "direct_award"}),
    "abandonment": frozenset({"abandonment"}),
    "revocation": frozenset({"revocation"}),
}

# The two endpoints the search does not give. Both are on the same host and neither
# is disallowed by robots.txt. They are module constants rather than registry keys
# because `Source` forbids unknown fields and a `detail_url` key would have to be
# added to the contract every source is validated against, for one source's routes.
DETAIL_URL = "https://www.simap.ch/api/publications/v1/project/{project_id}/publication-details/{publication_id}"
PROC_OFFICES_URL = "https://www.simap.ch/api/procoffices/v1/po/public"

# The human page, for the reviewer. This is the link the site's own share button
# copies (`ProjectDetailPage`, params `{id, lang}` in main.js). `{lang}` is the
# notice's own publication language, so the reviewer lands on the text that is
# actually the record. Never fetched: robots.txt disallows this path (rule 21).
NOTICE_URL = "https://www.simap.ch/{lang}/project-detail/{project_id}"

CONTAINER = "projects"
PAGINATION = "pagination"

# Every field the window guard, the type guard, the detail fetch and the payload
# read on every row. Deliberately absent: `orderAddress`, which is the place of
# performance and is null on 29 of the 151 rows measured over six weeks, and
# `lots`, which is empty on most.
REQUIRED_ROW_FIELDS = ("id", "publicationId", "publicationDate", "pubType", "publicationNumber")

# The directory of publishing bodies, and the field on it that states a buyer's
# level of government. 4,966 offices on 2026-09-12.
OFFICES_CONTAINER = "procOffices"
REQUIRED_OFFICE_FIELDS = ("id", "name", "type")


class SimapConnector(FeedConnector):
    """One CPV- and type-filtered window, then a detail per project, then the levels.

    Three endpoints, because the source splits the notice across three and none of
    them is an alternative to another (rule 1): the search says which projects
    published in the window, the detail carries the text, the language, the
    deadline and the codes, and the office directory is the only place simap states
    whether a buyer is federal, cantonal or communal.
    """

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # From config/thresholds.yaml, never hardcoded here (rule 6). Unlike TED's,
        # these reach the query: simap filters on CPV server side and the division
        # roots are what make one detail fetch per notice affordable.
        self.cpv_prefixes = cpv_prefixes

    def window(self, today: date | None = None) -> tuple[date, date]:
        """The publication dates this run asks for, inclusive at both ends."""
        end = today or date.today()
        return end - timedelta(days=LOOKBACK_DAYS), end

    def include_pub_types(self) -> list[str]:
        """The publication types to ask for: everything the filter takes, less the excluded.

        simap offers an include list and no exclude list, so the registry's
        `exclude_notice_types` is turned into its complement here. An excluded value
        that is not a real filter value raises, because a typo in the YAML would
        otherwise silently widen the query to include what it was meant to drop.
        """
        excluded = set(self.source.exclude_notice_types)
        unknown = sorted(excluded - FILTER_PUB_TYPES)
        if unknown:
            raise ValueError(
                f"sources/{self.source.id}.yaml excludes {unknown}, which simap's newestPubTypes does not accept; "
                f"valid values are {sorted(FILTER_PUB_TYPES)}"
            )
        included = sorted(FILTER_PUB_TYPES - excluded)
        if not included:
            raise ValueError(f"sources/{self.source.id}.yaml excludes every publication type simap has")
        return included

    def expected_result_types(self) -> frozenset[str]:
        """What `pubType` the server may legitimately answer with, for this query."""
        return frozenset().union(*(RESULT_PUB_TYPES[value] for value in self.include_pub_types()))

    def params(self, window: tuple[date, date], cursor: str = "") -> dict:
        start, end = window
        params = {
            "newestPublicationFrom": start.isoformat(),
            "newestPublicationUntil": end.isoformat(),
            "cpvCodes": cpv_query(self.cpv_prefixes),
            "newestPubTypes": ",".join(self.include_pub_types()),
        }
        if cursor:
            params["lastItem"] = cursor
        return params

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        window = self.window()
        expected_types = self.expected_result_types()
        rows = self.search(client, window, expected_types)
        if not rows:
            # Zero is not an error here and is a failure state one layer up
            # (base.py, rule 4). The directory is 1.1 MB and there is nothing to
            # look up in it, so it is not fetched.
            log.info("simap_fetch", projects=0, window=[day.isoformat() for day in window])
            return []

        offices = self.fetch_offices(client)
        ceiling = min(self.source.expected_max, MAX_DETAIL_REQUESTS)
        if len(rows) > ceiling:
            log.warning(
                "simap_ceiling_reached",
                found=len(rows),
                ceiling=ceiling,
                detail="raise expected_items_per_run in sources/simap.yaml",
            )

        raw_notices = [self.to_raw_notice(client, row, offices) for row in rows[:ceiling]]
        log.info(
            "simap_fetch",
            projects=len(rows),
            fetched=len(raw_notices),
            offices=len(offices),
            window=[day.isoformat() for day in window],
        )
        return raw_notices

    def search(self, client: httpx.Client, window: tuple[date, date], expected_types: frozenset[str]) -> list[dict]:
        """Every project in the window, walked by cursor.

        `pagination.lastItem` is the cursor and it is opaque - `20260912|42591`,
        the newest publication's date and a sequence. Paging is not a retry and not
        a fallback (rules 1 and 2): each page is one request for one distinct slice.
        """
        rows: list[dict] = []
        cursor = ""

        for _ in range(MAX_PAGES):
            response = client.get(self.source.api_url, params=self.params(window, cursor))
            response.raise_for_status()
            document = response.json()

            page = parse_page(document, window=window, expected_types=expected_types)
            rows.extend(page)

            pagination = document[PAGINATION]
            cursor = pagination.get("lastItem") or ""
            if not page or len(page) < pagination["itemsPerPage"] or not cursor:
                break
        else:
            log.warning("simap_max_pages_reached", found=len(rows), max_pages=MAX_PAGES)

        return rows

    def fetch_offices(self, client: httpx.Client) -> dict[str, dict]:
        """The public directory of publishing bodies, keyed by id. One request per run.

        This is the only place simap states a buyer's level of government: the
        publication payload carries the office's address and name but not its type.
        """
        response = client.get(PROC_OFFICES_URL)
        response.raise_for_status()
        return parse_offices(response.json())

    def to_raw_notice(self, client: httpx.Client, row: dict, offices: dict[str, dict]) -> RawNotice:
        """One project: its search row, its publication detail, and its office record.

        The three are stored together as fetched and nothing is derived from them
        here (rule 5). The mapper needs all three and has no network of its own.
        """
        detail = self.fetch_detail(client, row["id"], row["publicationId"])
        base = detail["base"]
        office_id = base.get("procOfficeId")
        office = offices.get(office_id)
        if office is None:
            raise ValueError(
                f"simap publication {base.get('publicationNumber', row['publicationNumber'])} names "
                f"procOfficeId {office_id!r}, which is not in the {len(offices)}-row public office directory; "
                "the buyer's level of government cannot be read and guessing it would mis-score the notice"
            )

        self.check_cpv(detail)

        payload = {"search": row, "detail": detail, "procOffice": office}
        return self.raw_notice(
            url=NOTICE_URL.format(lang=base["creationLanguage"], project_id=row["id"]),
            payload=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            mime="application/json",
        )

    def check_cpv(self, detail: dict) -> None:
        """Report a notice whose own CPV codes are all outside the asked-for divisions.

        Reported and not raised, unlike the window and type guards, and the
        difference is whether the server has an honest explanation. Those two have
        none: a row outside the window or outside the types can only mean the
        parameter was ignored. This one has one, because the search matches on the
        *project's* codes and a project can have matched through a sibling
        publication's classification rather than this publication's own. Measured
        over the recorded week: 0 of 26. A notice in this state would be dropped by
        the free CPV filter at step 5 anyway, so the warning is the only thing that
        would otherwise be lost.
        """
        divisions = {prefix[:2] for prefix in self.cpv_prefixes}
        codes = cpv_code_strings(detail)
        if codes and not any(code[:2] in divisions for code in codes):
            log.warning(
                "simap_cpv_outside_query",
                publication_number=detail["base"].get("publicationNumber"),
                codes=codes,
                asked_for=sorted(divisions),
            )

    def fetch_detail(self, client: httpx.Client, project_id: str, publication_id: str) -> dict:
        response = client.get(DETAIL_URL.format(project_id=project_id, publication_id=publication_id))
        response.raise_for_status()
        document = response.json()
        missing = [key for key in ("base", "project-info") if not document.get(key)]
        if missing:
            raise ValueError(f"simap publication {publication_id} detail is missing {missing}; got {sorted(document)}")
        return document


def cpv_query(prefixes: list[str]) -> str:
    """`cpvCodes` for the config's CPV prefixes: ['48', '72'] -> '48000000,72000000'.

    The filter takes eight-digit codes only (`cpvCodes=72` is a 400) and matches
    hierarchically, so a division prefix becomes its division root. Padding rather
    than a hand-written table means a narrower prefix in `thresholds.yaml` works
    too: '722' becomes the group root 72200000.
    """
    codes = []
    for prefix in prefixes:
        if not prefix.isdigit() or len(prefix) > 8:
            raise ValueError(f"CPV prefix {prefix!r} in config/thresholds.yaml is not 1 to 8 digits")
        codes.append(prefix.ljust(8, "0"))
    if not codes:
        raise ValueError("config/thresholds.yaml has no cpv_pass_prefixes; simap filters on CPV at the query")
    return ",".join(codes)


def cpv_code_strings(detail: dict) -> list[str]:
    """Every CPV code string on one publication: the project's and every lot's.

    Lives here rather than in the mapper because the connector's own CPV check and
    the mapper both read it and there is one definition of where a notice's codes
    are (`monitor/normalise/simap.py` imports it, the way the World Bank mapper
    imports `notice_date`).

    **The lot codes are not an extra.** Measured over the recorded week: 4 of 26
    publications carry a CPV code on a lot that the project level does not, and on
    one of them - 39760-02, a two-lot railway mandate - the only code in a passing
    division is lot 2's 79400000, while the project level says 71311230, railway
    engineering. Reading the project level alone would hand the free filter a code
    set with nothing passing in it, and step 5 drops a notice that has codes and no
    passing one. That is a 1-in-26 silent recall loss that would have looked
    exactly like the filter working.
    """
    procurement = detail.get("procurement") or {}
    blocks = [procurement, *(detail.get("lots") or [])]

    codes: list[str] = []
    for block in blocks:
        main = block.get("cpvCode") or {}
        if main.get("code"):
            codes.append(main["code"])
        for additional in block.get("additionalCpvCodes") or []:
            if additional.get("code"):
                codes.append(additional["code"])
    return codes


def parse_page(document: dict, *, window: tuple[date, date], expected_types: frozenset[str]) -> list[dict]:
    """The rows of one search response, checked for fields, window and type.

    Three checks, in the order a failure is cheapest to explain:

      1. The containers and the fields the walk, the detail fetch and the payload
         read, so a renamed field raises instead of yielding fewer notices (rule 4).
      2. That the server honoured the date window. Unknown parameter names are
         ignored in silence here, so a row outside the window means the date filter
         stopped filtering and the walk is reading the whole platform.
      3. That it honoured the type filter, against the measured filter-to-result
         mapping rather than the filter values themselves.
    """
    for key in (CONTAINER, PAGINATION):
        if key not in document:
            raise ValueError(f"simap search response has no {key!r} key; got {sorted(document)}")
    if "itemsPerPage" not in document[PAGINATION]:
        raise ValueError(f"simap pagination has no 'itemsPerPage'; got {sorted(document[PAGINATION])}")

    start, end = window
    rows = document[CONTAINER]
    for position, row in enumerate(rows):
        missing = [field for field in REQUIRED_ROW_FIELDS if field not in row]
        if missing:
            raise ValueError(f"simap search row {position} ({row.get('publicationNumber', '?')}) is missing {missing}")

        published = row_date(row)
        if not start <= published <= end:
            raise ValueError(
                f"simap row {row['publicationNumber']} was published {published.isoformat()}, outside the "
                f"{start.isoformat()}..{end.isoformat()} window that was asked for; the date parameters were "
                "ignored and the response is the unfiltered platform"
            )
        if row["pubType"] not in expected_types:
            raise ValueError(
                f"simap row {row['publicationNumber']} is a {row['pubType']!r}, which was not asked for; "
                f"the newestPubTypes filter was ignored. Asked for results in {sorted(expected_types)}"
            )
    return rows


def row_date(row: dict) -> date:
    """`publicationDate`, which is '2026-09-12'. Raises on anything else.

    The window guard reads this, so an unparseable value is a changed API rather
    than a row to skip.
    """
    raw = (row.get("publicationDate") or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as cause:
        raise ValueError(
            f"simap row {row.get('publicationNumber', '?')} has publicationDate {raw!r}, not yyyy-mm-dd: {cause}"
        ) from cause


def parse_offices(document: dict) -> dict[str, dict]:
    """The public office directory, keyed by office id.

    An empty directory raises rather than being carried forward: every notice would
    then fail its lookup one at a time, and the real failure is one request.
    """
    if OFFICES_CONTAINER not in document:
        raise ValueError(f"simap office directory has no {OFFICES_CONTAINER!r} key; got {sorted(document)}")

    offices = document[OFFICES_CONTAINER]
    if not offices:
        raise ValueError("simap office directory is empty; no notice's level of government could be read")

    by_id: dict[str, dict] = {}
    for position, office in enumerate(offices):
        missing = [field for field in REQUIRED_OFFICE_FIELDS if not office.get(field)]
        if missing:
            raise ValueError(f"simap office {position} ({office.get('id', '?')}) is missing {missing}")
        by_id[office["id"]] = office
    return by_id
