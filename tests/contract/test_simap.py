"""simap's contract, against the pass recorded on 2026-09-12.

The fixture holds all three documents the connector reads, because it reads three
and a test that replayed only the search would prove nothing: the search row
carries no language, no body, no deadline, no CPV code and no level of government.

Most of this file is about two hazards, and they are not the same hazard.

  - simap accepts query parameters it does not recognise with a 200 and ignores
    them in silence, so "the window and the types were honoured" has to be
    asserted rather than assumed. The tests that push a row outside the window or
    change its `pubType` are the point of the file.
  - The endpoint is project-centric: one row per project, carrying that project's
    newest publication. That is what makes a *longer* lookback lose notices, and
    it is asserted here as a property of the recorded pass rather than left in a
    docstring.

The rest checks that the parser and the mapper read what is actually in the
fixture, absences included: 3 publications with no `lots` key, 1 with no deadline,
4 whose CPV codes are only complete once the lots are read, and two original
languages.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.simap import (
    FILTER_PUB_TYPES,
    LOOKBACK_DAYS,
    RESULT_PUB_TYPES,
    SimapConnector,
    cpv_code_strings,
    cpv_query,
    parse_offices,
    parse_page,
    row_date,
)
from monitor.models import Source
from monitor.normalise.simap import (
    ADMIN_LEVEL_BY_OFFICE_TYPE,
    PUBLICATION_LANGUAGES,
    deadline,
    map_notice,
    notice_language,
    strip_html,
)

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "simap.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "simap.yaml"
THRESHOLDS_YAML = Path(__file__).resolve().parents[2] / "config" / "thresholds.yaml"

# What the recorded pass actually contained. Asserted rather than described, so a
# re-recorded fixture that quietly holds less fails here instead of downstream.
RECORDED_NOTICES = 26
RECORDED_PAGES = 2


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def prefixes() -> list[str]:
    return yaml.safe_load(THRESHOLDS_YAML.read_text(encoding="utf-8"))["cpv_pass_prefixes"]


@pytest.fixture(scope="module")
def connector(source, prefixes) -> SimapConnector:
    return SimapConnector(source, prefixes)


@pytest.fixture(scope="module")
def window(fixture) -> tuple[date, date]:
    start, end = fixture["window"]
    return date.fromisoformat(start), date.fromisoformat(end)


@pytest.fixture(scope="module")
def expected_types(fixture) -> frozenset[str]:
    return frozenset(fixture["expected_result_types"])


@pytest.fixture(scope="module")
def rows(fixture, window, expected_types) -> list[dict]:
    pages = fixture["search_pages"]
    return [row for page in pages for row in parse_page(page, window=window, expected_types=expected_types)]


@pytest.fixture(scope="module")
def offices(fixture) -> dict[str, dict]:
    return parse_offices(fixture["proc_offices"])


@pytest.fixture(scope="module")
def payloads(fixture, rows, offices) -> list[dict]:
    """What the connector composes and stores: the three documents, kept apart."""
    return [
        {
            "search": row,
            "detail": fixture["details"][row["publicationId"]],
            "procOffice": offices[fixture["details"][row["publicationId"]]["base"]["procOfficeId"]],
        }
        for row in rows
    ]


# --- the query the connector sends -------------------------------------------


def test_the_query_asks_for_the_complement_of_the_registrys_exclusions(connector, source):
    """simap offers an include list and no exclude list, so the registry's is inverted."""
    included = connector.include_pub_types()

    assert set(included) == FILTER_PUB_TYPES - set(source.exclude_notice_types)
    assert set(included).isdisjoint(source.exclude_notice_types)
    assert included == ["advance_notice", "competition", "request_for_information", "study_contract", "tender"]


def test_an_exclusion_the_filter_does_not_accept_raises(source, prefixes):
    """`award` reads like a filter value and is a 400; a typo must not widen the query."""
    widened = source.model_copy(update={"exclude_notice_types": ["award"]})

    with pytest.raises(ValueError, match="newestPubTypes does not accept"):
        SimapConnector(widened, prefixes).include_pub_types()


def test_excluding_everything_raises_rather_than_asking_for_everything(source, prefixes):
    """An empty include list would be sent as an absent filter, which is the opposite."""
    everything = source.model_copy(update={"exclude_notice_types": sorted(FILTER_PUB_TYPES)})

    with pytest.raises(ValueError, match="excludes every publication type"):
        SimapConnector(everything, prefixes).include_pub_types()


def test_the_expected_result_types_are_not_the_filter_values(connector):
    """The filter's vocabulary is not the result's; the guard has to use the mapping."""
    assert connector.expected_result_types() == frozenset(
        {"advance_notice", "competition", "request_for_information", "study_contract", "tender"}
    )
    # Measured 2026-09-12: asking for an award type answers with the coarser
    # `award` and with `direct_award`, neither of which is a filter value.
    assert RESULT_PUB_TYPES["award_tender"] == frozenset({"award", "direct_award"})
    assert "award" not in FILTER_PUB_TYPES


def test_cpv_prefixes_become_eight_digit_division_roots(prefixes):
    """`cpvCodes=72` is a 400; the filter takes eight digits and matches the division."""
    assert cpv_query(prefixes) == "48000000,72000000,79000000"
    assert cpv_query(["722"]) == "72200000"

    with pytest.raises(ValueError, match="not 1 to 8 digits"):
        cpv_query(["48", "software"])
    with pytest.raises(ValueError, match="no cpv_pass_prefixes"):
        cpv_query([])


def test_the_recorded_params_are_the_ones_the_connector_builds(connector, fixture, window):
    """The fixture is the connector's own request, not a hand-written one."""
    assert connector.params(window) == fixture["params"]


def test_the_window_is_short_because_a_long_one_loses_notices(connector):
    """One run of overlap on a daily schedule, and no more. See the module docstring."""
    assert LOOKBACK_DAYS == 2

    start, end = connector.window(today=date(2026, 9, 12))
    assert (start, end) == (date(2026, 9, 10), date(2026, 9, 12))


# --- the search response, and what it does not say ---------------------------


def test_the_recorded_pass_parses(fixture, rows):
    assert len(fixture["search_pages"]) == RECORDED_PAGES
    assert len(rows) == RECORDED_NOTICES
    for row in rows:
        assert row["id"] and row["publicationId"] and row["publicationNumber"]


def test_the_search_is_project_centric_not_publication_centric(rows):
    """One row per project. This is why a longer lookback would lose tenders.

    Recorded: 26 rows, 26 distinct project ids, 26 distinct publication ids. A
    project never appears twice, so a tender superseded inside the window is
    answered as whatever superseded it - and then dropped by the type filter,
    project and all.
    """
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["publicationId"] for row in rows}) == len(rows)


def test_a_renamed_container_raises(fixture, window, expected_types):
    """Zero rows would read as a quiet week on a source that publishes every weekday."""
    broken = {"results": fixture["search_pages"][0]["projects"], "pagination": {"itemsPerPage": 20}}

    with pytest.raises(ValueError, match="no 'projects' key"):
        parse_page(broken, window=window, expected_types=expected_types)


def test_a_renamed_row_field_raises(fixture, window, expected_types):
    broken = json.loads(json.dumps(fixture["search_pages"][0]))
    for row in broken["projects"]:
        row["pubDate"] = row.pop("publicationDate")

    with pytest.raises(ValueError, match="publicationDate"):
        parse_page(broken, window=window, expected_types=expected_types)


def test_a_row_outside_the_asked_for_window_raises(fixture, window, expected_types):
    """Unknown parameter names are ignored in silence, so this is the only guard.

    A renamed date parameter would answer with the whole platform and every row
    would look perfectly well formed.
    """
    broken = json.loads(json.dumps(fixture["search_pages"][0]))
    broken["projects"][3]["publicationDate"] = "2024-01-15"

    with pytest.raises(ValueError, match="outside the .* window that was asked for"):
        parse_page(broken, window=window, expected_types=expected_types)


def test_a_row_of_a_type_that_was_not_asked_for_raises(fixture, window, expected_types):
    """An `award` row means the type filter stopped filtering; awards are decided."""
    broken = json.loads(json.dumps(fixture["search_pages"][0]))
    broken["projects"][0]["pubType"] = "award"

    with pytest.raises(ValueError, match="which was not asked for"):
        parse_page(broken, window=window, expected_types=expected_types)


def test_every_recorded_row_is_a_live_publication_type(rows, expected_types):
    """What the week actually held: 22 tenders, 3 RFIs, 1 advance notice."""
    kinds = {row["pubType"] for row in rows}

    assert kinds <= expected_types
    assert kinds == {"tender", "request_for_information", "advance_notice"}


def test_an_unparseable_publication_date_raises():
    with pytest.raises(ValueError, match="not yyyy-mm-dd"):
        row_date({"publicationNumber": "1-01", "publicationDate": "12.09.2026"})


# --- the office directory, which is where the level of government lives ------


def test_the_office_directory_states_every_buyers_level(fixture, offices):
    """4,966 offices on 2026-09-12, eight types, all eight mapped."""
    assert len(offices) == 4966
    types = {office["type"] for office in offices.values()}
    assert types == set(ADMIN_LEVEL_BY_OFFICE_TYPE)
    assert {ADMIN_LEVEL_BY_OFFICE_TYPE[kind] for kind in types} == {"national", "regional", "local"}


def test_every_recorded_notice_resolves_to_an_office(fixture, rows, offices):
    """26 of 26. The connector raises on a miss, and this is why that is safe."""
    for row in rows:
        assert fixture["details"][row["publicationId"]]["base"]["procOfficeId"] in offices


def test_an_empty_directory_raises_once_rather_than_per_notice():
    with pytest.raises(ValueError, match="directory is empty"):
        parse_offices({"procOffices": []})


def test_a_directory_row_without_a_type_raises():
    """A type is the whole reason the directory is fetched."""
    with pytest.raises(ValueError, match=r"\['type'\]"):
        parse_offices({"procOffices": [{"id": "x", "name": "Gemeinde Irgendwo"}]})


# --- the mapper --------------------------------------------------------------


def test_every_recorded_notice_maps(payloads):
    for payload in payloads:
        notice = map_notice(payload).notice

        assert notice.source_id == "simap"
        assert notice.title
        assert notice.buyer
        assert notice.country == "CH"
        assert notice.language in PUBLICATION_LANGUAGES
        assert notice.language_confidence == 1.0
        assert notice.admin_level in {"national", "regional", "local"}
        assert notice.url.startswith(f"https://www.simap.ch/{notice.language}/project-detail/")
        assert notice.estimated_value_usd is None
        assert notice.status == "detected"


def test_the_notice_is_stored_in_its_own_language_and_more_than_one_is_published(payloads):
    """20 German originals and 6 French. On 17 of 26, more than one language is filled.

    Rule 9: the record is the original. `creationLanguage` is the only field that
    says which of the filled keys that is.
    """
    notices = [map_notice(payload).notice for payload in payloads]
    languages = {notice.language for notice in notices}
    assert languages == {"de", "fr"}
    assert sum(1 for notice in notices if notice.language == "de") == 20
    assert sum(1 for notice in notices if notice.language == "fr") == 6

    multilingual = [
        payload for payload in payloads if sum(1 for value in payload["detail"]["base"]["title"].values() if value) > 1
    ]
    assert len(multilingual) == 17
    for payload in multilingual:
        base = payload["detail"]["base"]
        assert map_notice(payload).notice.title == base["title"][base["creationLanguage"]].strip()


def test_no_english_rendering_is_carried_even_though_simap_supplies_some(payloads):
    """7 of 26 carry a publisher English rendering; fetch.py would stamp it 'ted-eforms'.

    Rule 9 makes the stamp part of the record. See the mapper's docstring.
    """
    with_english = [
        payload
        for payload in payloads
        if any(entry["language"] == "en" for entry in payload["detail"]["base"]["translationLanguages"] or [])
    ]
    assert len(with_english) == 7

    for payload in payloads:
        mapped = map_notice(payload)
        assert mapped.title_en == ""
        assert mapped.body_en == ""


def test_a_language_simap_does_not_publish_in_raises():
    """Romansh is a Swiss official language and not a simap publication language."""
    with pytest.raises(ValueError, match="creationLanguage is 'rm'"):
        notice_language({"creationLanguage": "rm"}, "1-01")


def test_a_title_that_is_missing_in_the_notices_own_language_raises(payloads):
    """Substituting a translation would store French text under language 'de'."""
    broken = json.loads(json.dumps(payloads[0]))
    language = broken["detail"]["base"]["creationLanguage"]
    broken["detail"]["base"]["title"][language] = None

    with pytest.raises(ValueError, match="no text in the notice's own language"):
        map_notice(broken)


def test_the_buyer_is_the_office_not_the_place_of_performance(payloads):
    """`orderAddress` is where the work happens and said FR on 2 of 151 rows."""
    for payload in payloads:
        office = payload["detail"]["project-info"]["procOfficeAddress"]
        language = payload["detail"]["base"]["creationLanguage"]
        notice = map_notice(payload).notice

        assert notice.buyer == office["name"][language].strip()
        assert notice.country == office["countryId"]


def test_the_admin_level_comes_from_the_directory_and_not_from_the_canton(payloads):
    """Seven of the eight office types appear in the recorded week.

    A federal office sits in a canton like any other, so the canton on the address
    says nothing about the level. The directory's `type` is what says it.
    """
    levels = {}
    for payload in payloads:
        notice = map_notice(payload).notice
        levels[payload["procOffice"]["type"]] = notice.admin_level

    assert levels == {
        "central_federation": "national",
        "decentral_federation": "national",
        "other_federation": "national",
        "cantonal": "regional",
        "other_cantonal": "regional",
        "communal": "local",
        "other_communal": "local",
    }
    # A cantonal office in one canton and a federal office in another: the level
    # tracks the type and not the address.
    cantons = {
        (payload["procOffice"]["type"], payload["detail"]["project-info"]["procOfficeAddress"].get("cantonId"))
        for payload in payloads
    }
    assert len({canton for _, canton in cantons}) > 1


def test_an_unknown_office_type_raises(payloads):
    broken = json.loads(json.dumps(payloads[0]))
    broken["procOffice"]["type"] = "intercantonal"

    with pytest.raises(ValueError, match="add it to ADMIN_LEVEL_BY_OFFICE_TYPE"):
        map_notice(broken)


def test_the_lots_carry_cpv_codes_the_project_level_does_not(fixture, payloads):
    """4 of 26, and on one of them the only passing code is a lot's.

    39760-02 is a two-lot railway mandate: the project says 71311230, railway
    engineering, and lot 2 says 79400000, management consultancy. Reading the
    project level alone hands step 5 a code set with nothing passing in it, and a
    notice with codes and no passing one is dropped before any model call.
    """
    lot_only = []
    for payload in payloads:
        detail = payload["detail"]
        procurement = detail.get("procurement") or {}
        project_level = {(procurement.get("cpvCode") or {}).get("code")} | {
            code["code"] for code in procurement.get("additionalCpvCodes") or []
        }
        everything = set(cpv_code_strings(detail))
        if everything - project_level:
            lot_only.append(detail["base"]["publicationNumber"])

    assert len(lot_only) == 4
    assert "39760-02" in lot_only

    railway = next(payload for payload in payloads if payload["detail"]["base"]["publicationNumber"] == "39760-02")
    detail = railway["detail"]
    assert detail["procurement"]["cpvCode"]["code"] == "71311230"
    assert detail["procurement"]["additionalCpvCodes"] is None
    assert map_notice(railway).notice.cpv_codes == ["71311230", "79400000"]


def test_every_recorded_notice_carries_a_code_in_an_asked_for_division(payloads, prefixes):
    """26 of 26, once the lots are read. This is what makes the connector's warning quiet."""
    divisions = tuple(prefixes)
    for payload in payloads:
        codes = map_notice(payload).notice.cpv_codes
        assert codes, payload["detail"]["base"]["publicationNumber"]
        assert any(code.startswith(divisions) for code in codes), payload["detail"]["base"]["publicationNumber"]


def test_three_of_the_recorded_notices_have_no_lots_key_at_all(fixture, rows):
    """Absence, not an empty list. The code collector reads it as absent either way."""
    without = [row for row in rows if fixture["details"][row["publicationId"]].get("lots") is None]
    assert len(without) == 3
    for row in without:
        assert cpv_code_strings(fixture["details"][row["publicationId"]])


# --- the deadline, parsed by rule from the original (rule 10) ----------------


def test_the_deadline_keeps_simaps_own_zurich_offset(payloads):
    """18 summer-time and 7 winter-time offsets in the recorded week; nothing converted.

    The platform's terms say every time it records is Europe/Zurich and the API
    applies the offset itself, so there is no timezone to invent here.
    """
    offsets = []
    for payload in payloads:
        stated = (payload["detail"].get("dates") or {}).get("offerDeadline")
        parsed = deadline(payload["detail"])
        if stated is None:
            assert parsed is None
            continue
        assert parsed == datetime.fromisoformat(stated)
        offsets.append(stated[-6:])

    assert sorted(set(offsets)) == ["+01:00", "+02:00"]
    assert offsets.count("+02:00") == 18
    assert offsets.count("+01:00") == 7


def test_the_one_notice_without_a_deadline_is_the_advance_notice(payloads):
    """A prior information notice announces an intention and has nothing to close."""
    without = [payload for payload in payloads if map_notice(payload).notice.deadline_at is None]

    assert len(without) == 1
    assert without[0]["search"]["pubType"] == "advance_notice"


def test_a_publication_with_no_dates_block_has_no_deadline(payloads):
    """`dates` is null on every decided type, so the lookup is written for an absent block."""
    broken = json.loads(json.dumps(payloads[0]))
    broken["detail"]["dates"] = None

    assert deadline(broken["detail"]) is None
    assert map_notice(broken).notice.deadline_at is None


def test_the_publication_date_keeps_the_day_the_platform_published(payloads):
    for payload in payloads:
        notice = map_notice(payload).notice
        stated = payload["detail"]["base"]["publicationDate"]

        assert notice.published_at == datetime.combine(date.fromisoformat(stated), datetime.min.time(), tzinfo=UTC)
        # Switzerland is east of UTC all year, so UTC midnight is still that day in
        # Zurich and the calendar day the platform published survives.
        assert notice.published_at.date() == date.fromisoformat(stated)


def test_six_of_the_recorded_notices_are_corrections_and_keep_their_own_date(fixture, payloads):
    """A correction republishes the whole notice under a new number; it is the record."""
    corrections = [payload for payload in payloads if payload["detail"]["base"]["corrected"]]
    assert len(corrections) == 6

    for payload in corrections:
        base = payload["detail"]["base"]
        assert base["correctedPubId"]
        assert base["initialPublicationDate"] != base["publicationDate"]
        assert map_notice(payload).notice.published_at.date() == date.fromisoformat(base["publicationDate"])


# --- the body ----------------------------------------------------------------


def test_the_body_is_the_projects_own_description_as_text(payloads):
    """All 26 recorded descriptions are the publisher's HTML."""
    for payload in payloads:
        language = payload["detail"]["base"]["creationLanguage"]
        markup = payload["detail"]["procurement"]["orderDescription"][language]
        notice = map_notice(payload).notice

        assert "<" in markup
        assert "<" not in notice.body
        assert notice.body


def test_html_stripping_keeps_the_prose_and_drops_the_layout():
    markup = "<p>Beschaffung eines&nbsp;IFMIS</p>\n\n\n<ul><li>Los 1</li><li>Los 2</li></ul>"

    assert strip_html(markup) == "Beschaffung eines IFMIS\n\nLos 1\nLos 2"
    assert strip_html("") == ""


def test_a_payload_missing_one_of_its_three_documents_raises(payloads):
    """The mapper needs all three and has no network of its own."""
    for part in ("search", "detail", "procOffice"):
        broken = {key: value for key, value in payloads[0].items() if key != part}
        with pytest.raises(ValueError, match=f"missing '{part}'"):
            map_notice(broken)


def test_the_content_hash_is_stable_across_two_passes_over_one_notice(payloads):
    """Change detection is the hash and nothing else, so a second run inserts nothing."""
    first = map_notice(json.loads(json.dumps(payloads[0]))).notice
    second = map_notice(json.loads(json.dumps(payloads[0]))).notice

    assert first.content_hash == second.content_hash
    assert len({map_notice(payload).notice.content_hash for payload in payloads}) == len(payloads)


# --- one whole pass, replayed ------------------------------------------------
#
# The three parsers above are tested one at a time. This replays the recorded pass
# through `fetch_raw` itself, because the joint this design turns on is not in any
# of them: the connector composes a payload out of three documents and the mapper
# reads that composition. A test of the parsers alone would pass with the payload
# assembled wrongly.


def replaying_client(fixture: dict) -> httpx.Client:
    """A client that serves the recorded documents and refuses anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/project-search"):
            cursor = request.url.params.get("lastItem", "")
            page = next(
                (
                    candidate
                    for index, candidate in enumerate(fixture["search_pages"])
                    if (index == 0 and not cursor)
                    or (index > 0 and fixture["search_pages"][index - 1]["pagination"]["lastItem"] == cursor)
                ),
                None,
            )
            assert page is not None, f"no recorded page for cursor {cursor!r}"
            return httpx.Response(200, json=page)
        if path.endswith("/po/public"):
            return httpx.Response(200, json=fixture["proc_offices"])
        if "/publication-details/" in path:
            return httpx.Response(200, json=fixture["details"][path.rsplit("/", 1)[1]])
        raise AssertionError(f"the connector asked for {request.url}, which is not part of the recorded pass")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_yields_payloads_the_mapper_can_read(connector, fixture, window, monkeypatch):
    """Fetch, page, look up the levels, compose, and map - over the recorded documents."""
    monkeypatch.setattr(connector, "window", lambda today=None: window)

    with replaying_client(fixture) as client:
        raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == RECORDED_NOTICES
    for raw in raw_notices:
        assert raw.source_id == "simap"
        assert raw.mime == "application/json"

        payload = json.loads(raw.payload)
        assert sorted(payload) == ["detail", "procOffice", "search"]

        notice = map_notice(payload).notice
        # The URL the connector stamped on the raw notice and the one the mapper
        # derives are the same link, in the notice's own language, and neither is
        # ever fetched: robots.txt disallows /{lang}/project-detail (rule 21).
        assert raw.url == notice.url
        assert notice.external_id == payload["search"]["publicationNumber"]


def test_a_publication_naming_an_office_that_is_not_in_the_directory_raises(connector, fixture, window, monkeypatch):
    """The level of government would be unknown, and guessing it would mis-score the notice."""
    monkeypatch.setattr(connector, "window", lambda today=None: window)
    thinned = json.loads(json.dumps(fixture))
    first = thinned["search_pages"][0]["projects"][0]
    wanted = thinned["details"][first["publicationId"]]["base"]["procOfficeId"]
    thinned["proc_offices"]["procOffices"] = [
        office for office in thinned["proc_offices"]["procOffices"] if office["id"] != wanted
    ]

    with replaying_client(thinned) as client, pytest.raises(ValueError, match="public office directory"):
        connector.fetch_raw(client)


def test_the_detail_endpoint_losing_a_block_raises(connector, fixture, window, monkeypatch):
    """`base` carries the language, the ids and the dates; without it nothing maps."""
    monkeypatch.setattr(connector, "window", lambda today=None: window)
    thinned = json.loads(json.dumps(fixture))
    for detail in thinned["details"].values():
        detail.pop("base")

    with replaying_client(thinned) as client, pytest.raises(ValueError, match="detail is missing"):
        connector.fetch_raw(client)


# --- the registry entry agrees with the code ---------------------------------


def test_the_registry_entry_and_the_connector_agree(source):
    assert source.id == "simap"
    assert source.country == "CH"
    assert source.access_type == "api"
    assert source.connector_class == "FeedConnector"
    assert source.api_url == "https://www.simap.ch/api/publications/v2/project/project-search"
    assert set(source.exclude_notice_types) <= FILTER_PUB_TYPES
    # Not enabled by this step: no live fetch was run, and the terms position
    # (AGB «Nutzung des API» clause 5) needs a named person's sign-off first.
    assert source.enabled is False
    assert source.tos_status == "reviewed_restricted"
