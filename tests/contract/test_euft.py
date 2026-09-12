"""The EU portal's contract, against the live set recorded on 2026-09-12.

Two hazards run through this file.

The first is the encoding: this API reads its query only when the query arrives
as a multipart file part, and answers a plain form field or a JSON body with
HTTP 200 and all 4,184,545 documents in the portal. So the request shape is
asserted here as a fact about the connector, and every filter it sends is
re-checked on the way back in; the tests that corrupt a row's type, datasource,
UI language, status or country are the point of the file.

The second is the deadline. The API stamps every closing time `+0000` whatever
zone the tender closes in, and the wall clock is the local one. The tests below
pin the three notices whose real offsets were read off TED's eForms fields, and
assert that a deadline arriving with a real offset raises rather than being moved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from monitor.connectors.euft import (
    API_KEY,
    DATASOURCE,
    DOCUMENT_TYPE,
    INDEX_LANGUAGE,
    LIVE_STATUSES,
    MATCH_ALL_TEXT,
    PAGE_SIZE,
    ZONE_CODES,
    EuftConnector,
    one,
    parse_results,
    zone_codes,
)
from monitor.models import Source
from monitor.normalise.euft import (
    ADMIN_LEVEL,
    DEFAULT_LANGUAGE,
    LANGUAGE_NOT_STATED,
    LANGUAGE_STATED,
    cpv_codes,
    deadline,
    lead_authority,
    map_notice,
    notice_language,
)

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "euft.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "euft.yaml"

# The three notices whose real closing offsets were read off TED's eForms
# `deadline-receipt-tender-time-lot` on 2026-09-12, keyed by the portal's own
# notice id. The fourth cross-check, TED 549932-2026 at 10:00:59+01:00, is the
# Benin notice below.
TED_CROSS_CHECKED = {
    # id                                       portal wall clock, TED's real offset
    "46bcc5de-acbf-4a60-ba7c-a66c47cab571-CN": ("2026-10-02T14:30:59", "Europe/Podgorica"),
    "7d2c262b-2f2c-491a-bd0a-296aa6d042de-CN": ("2026-10-08T14:30:59", "Europe/Podgorica"),
    "78daa744-3775-4f44-a10c-958822818fb1-CN": ("2026-09-17T12:00:59", "Europe/Skopje"),
}
BENIN_NOTICE = "2ea5f0f2-8a51-400a-8f69-4a4b81b4768f-CN"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def zones(source) -> frozenset[str]:
    return frozenset(zone_codes(source.covers))


@pytest.fixture(scope="module")
def results(document, zones) -> list[dict]:
    return parse_results(document, zones=zones)


@pytest.fixture(scope="module")
def notices(results) -> list:
    return [map_notice(result) for result in results]


def by_id(results: list[dict], identifier: str) -> dict:
    for result in results:
        if one(result["metadata"], "identifier") == identifier:
            return result
    raise AssertionError(f"{identifier} is not in the fixture")


# --- the request the connector sends ------------------------------------------


def test_the_query_goes_as_a_multipart_file_part(source):
    """The measured hazard: any other encoding returns the whole 4.18M-doc index."""
    files = EuftConnector(source, ["48"]).files()
    name, body, content_type = files["query"]

    assert name == "blob"
    assert content_type == "application/json"
    assert isinstance(body, bytes)
    assert json.loads(body) == EuftConnector(source, ["48"]).query()


def test_the_query_asks_for_live_covered_country_tenders_on_the_live_index(source):
    clauses = EuftConnector(source, ["48"]).query()["bool"]["must"]
    asked = {next(iter(clause["terms"])): list(clause["terms"].values())[0] for clause in clauses}

    assert asked["type"] == [DOCUMENT_TYPE]
    assert asked["DATASOURCE"] == [DATASOURCE]
    assert asked["language"] == [INDEX_LANGUAGE]
    assert asked["status"] == list(LIVE_STATUSES)
    assert asked["geographicalZones"] == zone_codes(source.covers)


def test_the_query_excludes_closed_and_cancelled_tenders():
    """31094503 is Closed; asking for it would read 330 decided tenders of 365."""
    assert "31094503" not in LIVE_STATUSES
    assert len(LIVE_STATUSES) == 2


def test_the_mandatory_parameters_are_sent_and_the_api_key_is_the_index_name(source):
    """`apiKey=SEDIA` names the index, not an identity: it is not a credential."""
    params = EuftConnector(source, ["48"]).params(page=1)

    assert params["apiKey"] == API_KEY == DATASOURCE
    assert params["text"] == MATCH_ALL_TEXT
    assert params["pageSize"] == PAGE_SIZE
    assert params["pageNumber"] == 1


def test_the_zone_table_covers_the_registry_in_the_registrys_own_order(source):
    """The order is the country rule: the first covered zone on a notice wins."""
    assert list(ZONE_CODES) == source.covers


def test_an_iso_code_with_no_zone_id_raises(source):
    """Sending 18 of 19 would read as a quiet week in the nineteenth."""
    with pytest.raises(ValueError, match="RS"):
        zone_codes([*source.covers, "RS"])


# --- the response the connector got back --------------------------------------


def test_the_recorded_set_is_the_whole_live_set(document, results, source):
    """No date window: every run reads all of it, so zero yield means it broke."""
    assert document["totalResults"] == len(results) == 35
    assert source.expected_min <= len(results) <= source.expected_max


def test_a_renamed_container_raises(document, zones):
    with pytest.raises(ValueError, match="results"):
        parse_results({"hits": document["results"]}, zones=zones)


def test_a_renamed_field_raises(document, zones):
    broken = json.loads(json.dumps(document))
    for result in broken["results"]:
        result["metadata"]["contractingAuthority"] = result["metadata"].pop("cftLeadContractingAuthorityCode")

    with pytest.raises(ValueError, match="cftLeadContractingAuthorityCode"):
        parse_results(broken, zones=zones)


def test_a_document_of_another_type_raises(document, zones):
    """A grant, an event or a person record means the type filter was dropped."""
    broken = json.loads(json.dumps(document))
    broken["results"][4]["metadata"]["type"] = ["1"]

    with pytest.raises(ValueError, match="the type filter was ignored"):
        parse_results(broken, zones=zones)


def test_a_row_from_the_archived_index_raises(document, zones):
    """SEDIA_PRD_CENTRICITY stops in 2023 and still calls those tenders open."""
    broken = json.loads(json.dumps(document))
    broken["results"][2]["metadata"]["DATASOURCE"] = ["SEDIA_PRD_CENTRICITY"]

    with pytest.raises(ValueError, match="the DATASOURCE filter was ignored"):
        parse_results(broken, zones=zones)


def test_another_ui_language_copy_raises(document, zones):
    """Without this filter every notice arrives 12 to 24 times over."""
    broken = json.loads(json.dumps(document))
    broken["results"][9]["metadata"]["language"] = ["bg"]

    with pytest.raises(ValueError, match="the language filter was ignored"):
        parse_results(broken, zones=zones)


def test_a_status_that_was_not_asked_for_raises(document, zones):
    broken = json.loads(json.dumps(document))
    broken["results"][1]["metadata"]["status"] = ["31094503"]

    with pytest.raises(ValueError, match="was not asked for"):
        parse_results(broken, zones=zones)


def test_a_notice_for_no_covered_country_raises(document, zones):
    """A region roll-up alone is not a covered country, and neither is Vietnam."""
    broken = json.loads(json.dumps(document))
    broken["results"][3]["metadata"]["geographicalZones"] = ["31085111", "20001042"]

    with pytest.raises(ValueError, match="none of them covered"):
        parse_results(broken, zones=zones)


def test_a_single_valued_field_with_two_values_raises(document, zones):
    broken = json.loads(json.dumps(document))
    broken["results"][5]["metadata"]["status"] = ["31094501", "31094502"]

    with pytest.raises(ValueError, match="2 values for 'status'"):
        parse_results(broken, zones=zones)


# --- the mapping ---------------------------------------------------------------


def test_every_recorded_notice_maps(results, notices):
    for result, mapped in zip(results, notices, strict=True):
        metadata = result["metadata"]
        notice = mapped.notice

        assert notice.title == one(metadata, "title").strip()
        assert notice.body == one(metadata, "description").strip()
        assert notice.external_id == one(metadata, "identifier")
        assert notice.url == one(metadata, "url")
        assert notice.external_id in notice.url
        assert notice.admin_level == ADMIN_LEVEL
        assert notice.status == "detected"


def test_the_registry_and_the_mapper_agree(source):
    assert ADMIN_LEVEL == source.admin_level
    assert DEFAULT_LANGUAGE == source.language


def test_the_buyer_is_the_lead_authority_and_never_the_title(results, notices):
    """caName is the procurement title on this datasource, on 35 of 35 notices."""
    for result, mapped in zip(results, notices, strict=True):
        metadata = result["metadata"]

        assert one(metadata, "caName") == one(metadata, "title")
        assert mapped.notice.buyer != mapped.notice.title
        assert mapped.notice.buyer

    buyers = {mapped.notice.buyer for mapped in notices}
    assert "MINISTRY OF FINANCE / NAO" in buyers


def test_two_lead_authorities_raise(results):
    """The record builder proposes one buyer; picking one here would invent it."""
    metadata = json.loads(json.dumps(by_id(results, BENIN_NOTICE)["metadata"]))
    authorities = json.loads(one(metadata, "cftLeadContractingAuthorityCode"))
    metadata["cftLeadContractingAuthorityCode"] = [json.dumps(authorities + authorities)]

    with pytest.raises(ValueError, match="2 lead authorities"):
        lead_authority(metadata, external_id="x")


def test_the_country_is_the_first_covered_one_in_the_registrys_order(results, notices, source):
    """The rule for every notice, single-country or regional."""
    for result, mapped in zip(results, notices, strict=True):
        on_notice = set(result["metadata"]["geographicalZones"])
        covered = [code for code in source.covers if ZONE_CODES[code] in on_notice]

        assert covered
        assert mapped.notice.country == covered[0]


def test_a_regional_notice_names_several_covered_countries(results, notices, source):
    """Seven of the 35; the widest is an ECOWAS tender naming all thirteen."""
    multi = [
        (result, mapped)
        for result, mapped in zip(results, notices, strict=True)
        if len(set(result["metadata"]["geographicalZones"]) & set(ZONE_CODES.values())) > 1
    ]

    def covered(result: dict) -> list[str]:
        zones = set(result["metadata"]["geographicalZones"])
        return [code for code in source.covers if ZONE_CODES[code] in zones]

    assert len(multi) == 7
    widest_result, widest = max(multi, key=lambda pair: len(covered(pair[0])))

    assert len(covered(widest_result)) == 13
    assert widest.notice.country == "BJ"
    assert "ECOWAS" in widest.notice.title


# --- the deadline ---------------------------------------------------------------


def test_the_deadline_is_the_wall_clock_in_the_notices_own_zone(results):
    """Pinned to TED's eForms offsets; see the module docstring."""
    for identifier, (wall_clock, zone) in TED_CROSS_CHECKED.items():
        metadata = by_id(results, identifier)["metadata"]

        assert deadline(metadata, external_id=identifier) == datetime.fromisoformat(wall_clock).replace(
            tzinfo=ZoneInfo(zone)
        )


def test_the_benin_deadline_matches_its_ted_corrigendum(results):
    """TED 549932-2026 moved lot 1 to 10:00:59+01:00; the portal carries that."""
    metadata = by_id(results, BENIN_NOTICE)["metadata"]
    closing = deadline(metadata, external_id=BENIN_NOTICE)

    assert closing == datetime(2026, 9, 18, 10, 0, 59, tzinfo=ZoneInfo("Africa/Porto-Novo"))
    assert closing.astimezone(UTC) == datetime(2026, 9, 18, 9, 0, 59, tzinfo=UTC)


def test_taking_the_stated_offset_would_run_the_deadline_late(results):
    """The whole reason for the rule: +0000 is an hour or two of false runway."""
    metadata = by_id(results, BENIN_NOTICE)["metadata"]
    as_published = datetime.strptime(one(metadata, "deadlineDate"), "%Y-%m-%dT%H:%M:%S.%f%z")

    assert as_published > deadline(metadata, external_id=BENIN_NOTICE)


def test_a_deadline_with_a_real_offset_raises(results):
    """If the API starts publishing the instant, this rule would be moving it."""
    metadata = json.loads(json.dumps(by_id(results, BENIN_NOTICE)["metadata"]))
    metadata["deadlineDate"] = ["2026-09-18T10:00:59.000+0100"]

    with pytest.raises(ValueError, match="carries a real offset"):
        deadline(metadata, external_id=BENIN_NOTICE)


def test_an_unknown_timezone_raises(results):
    metadata = json.loads(json.dumps(by_id(results, BENIN_NOTICE)["metadata"]))
    metadata["cftTimezone"] = ["Africa/Nowhere"]

    with pytest.raises(ValueError, match="not a known time zone"):
        deadline(metadata, external_id=BENIN_NOTICE)


def test_a_prior_information_notice_has_no_deadline_and_states_no_language(results, notices):
    """Ten of the 35. A PIN has no tender documents yet, so it has neither."""
    pins = [
        (result, mapped)
        for result, mapped in zip(results, notices, strict=True)
        if one(result["metadata"], "callIdentifier").endswith("-PIN")
    ]

    assert len(pins) == 10
    for result, mapped in pins:
        assert not result["metadata"].get("deadlineDate")
        assert mapped.notice.deadline_at is None
        assert mapped.notice.language == DEFAULT_LANGUAGE
        assert mapped.notice.language_confidence == LANGUAGE_NOT_STATED


def test_every_other_notice_carries_the_deadline_it_published(results, notices):
    dated = [mapped for result, mapped in zip(results, notices, strict=True) if result["metadata"].get("deadlineDate")]

    assert len(dated) == 25
    assert all(mapped.notice.deadline_at is not None for mapped in dated)


# --- language, CPV and value -----------------------------------------------------


def test_the_language_is_the_lots_tender_document_language(notices):
    """21 English, 4 French, 10 not stated. The French ones reach step 14."""
    stated = [mapped for mapped in notices if mapped.notice.language_confidence == LANGUAGE_STATED]
    french = [mapped for mapped in stated if mapped.notice.language == "fr"]

    assert len(stated) == 25
    assert len(french) == 4
    assert all(mapped.notice.language in {"en", "fr"} for mapped in stated)


def test_two_declared_languages_in_one_notice_raise(results):
    """One notice cannot be filtered against two lexicons."""
    metadata = json.loads(json.dumps(by_id(results, BENIN_NOTICE)["metadata"]))
    lots = json.loads(one(metadata, "lots"))
    lots["procurementProjectLots"][1]["tenderingTerms"]["callForTendersDocumentReference"]["languageID"] = "ENG"
    metadata["lots"] = [json.dumps(lots)]

    with pytest.raises(ValueError, match="cannot be filtered against two lexicons"):
        notice_language(metadata)


def test_no_english_rendering_is_claimed(notices):
    """The `language: en` filter picks a UI copy, not a translation (rule 9)."""
    assert all(mapped.title_en == "" and mapped.body_en == "" for mapped in notices)


def test_the_main_cpv_leads_the_code_list(results, notices):
    """It is the code the filter quotes when it drops a notice."""
    for result, mapped in zip(results, notices, strict=True):
        metadata = result["metadata"]
        main = json.loads(one(metadata, "mainCpvCode"))["mainCode"]

        assert mapped.notice.cpv_codes[0] == main
        assert set(mapped.notice.cpv_codes) == set(metadata["mainCpv"])
        assert len(mapped.notice.cpv_codes) == len(set(mapped.notice.cpv_codes))


def test_a_main_cpv_code_with_no_code_raises(results):
    metadata = json.loads(json.dumps(by_id(results, BENIN_NOTICE)["metadata"]))
    metadata["mainCpvCode"] = ['{"mainCode":""}']

    with pytest.raises(ValueError, match="names no mainCode"):
        cpv_codes(metadata, external_id="x")


def test_no_value_is_carried_through(results, notices):
    """24 of the 35 state an amount and every one of them is in EUR."""
    priced = [result for result in results if result["metadata"].get("cftEstimatedOverallContractAmount")]

    assert len(priced) == 24
    assert {one(result["metadata"], "cftEstimatedOverallContractCurrency") for result in priced} == {"EUR"}
    assert all(mapped.notice.estimated_value_usd is None for mapped in notices)


def test_the_publication_date_is_a_calendar_day(results, notices):
    for result, mapped in zip(results, notices, strict=True):
        metadata = result["metadata"]
        published = mapped.notice.published_at

        assert published.tzinfo is UTC
        assert (published.hour, published.minute, published.second) == (0, 0, 0)
        assert published.date().isoformat() == one(metadata, "startDate")[:10]


def test_the_content_hash_is_stable_across_two_reads(results):
    assert [map_notice(result).notice.content_hash for result in results] == [
        map_notice(result).notice.content_hash for result in results
    ]
