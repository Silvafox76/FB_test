"""The IUB Open Data (Latvia) normaliser: one day file's kept records to Notices.

`monitor/normalise/iub_lv.py` has no fixture of its own; it is written and
measured directly against the 49 records `monitor/connectors/iub_lv.py`'s own
`select()` keeps from the whole day recorded in
`tests/contract/fixtures/iub_lv.json` (18-09-2026, 215 records of every notice
type), wrapped the same way `fetch_raw` wraps them (`wrap(day, record)`), so
this test runs the fixture through the connector's own selection and wrapping
rather than duplicating either.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest

from monitor.connectors.iub_lv import select, wrap
from monitor.normalise.iub_lv import map_notice
from monitor.registry import load_sources

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures" / "iub_lv.json"
DAY = date(2026, 9, 18)

# What the recorded day actually holds, through the connector's own select().
# Asserted rather than assumed, so a re-recorded fixture that quietly changed
# shape fails here and not somewhere subtler.
KEPT_COUNT = 49
CPV_PRESENT_COUNT = 43
CPV_EMPTY_COUNT = 6
MK_CONTRACT_COUNT = 6
SINGLE_LOT_VALUED_COUNT = 21
SINGLE_LOT_UNVALUED_COUNT = 18
MULTI_LOT_COUNT = 10
MULTI_LOT_WITH_A_VALUE_NOTE_COUNT = 3
MULTI_LOT_WITH_NO_VALUE_NOTE_COUNT = 7


@pytest.fixture(scope="module")
def source_yaml():
    return next(candidate for candidate in load_sources() if candidate.id == "iub_lv")


@pytest.fixture(scope="module")
def payloads(source_yaml) -> list[dict]:
    """The 49 kept payloads, selected and wrapped by the connector's own
    functions - not reconstructed here - from the whole recorded day."""
    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    kept = select(records, exclude_notice_types=source_yaml.exclude_notice_types)
    return [wrap(DAY, record) for record in kept]


@pytest.fixture()
def payload(payloads) -> dict:
    """The first payload (a `pil-contract`), deep-copied so a test can mutate it."""
    return deepcopy(payloads[0])


# --- every kept record maps ---------------------------------------------------


def test_all_49_kept_records_map_without_raising(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert len(mapped) == KEPT_COUNT


def test_all_49_produce_distinct_content_hashes(payloads):
    """All 49 kept titles are distinct in the recorded day, so 49 distinct
    hashes - reported here rather than assumed, the same discipline
    test_normalise_ejn_ba.py applies to its own (lower) count."""
    mapped = [map_notice(p) for p in payloads]

    hashes = {m.notice.content_hash for m in mapped}
    assert len(hashes) == KEPT_COUNT


# --- country, admin level, language: one value across the whole source -------


def test_country_is_lv_on_all_49(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert {m.notice.country for m in mapped} == {"LV"}


def test_admin_level_is_national_on_all_49(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert {m.notice.admin_level for m in mapped} == {"national"}


def test_language_is_lv_at_full_confidence_on_all_49(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert {m.notice.language for m in mapped} == {"lv"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}


def test_no_english_is_carried_because_none_is_published(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


# --- country fallback, on a synthetic case the recorded day never exercises --


def test_country_falls_back_to_lv_when_countrycode_is_absent(payload):
    """Every one of the 49 recorded records states LVA; the fallback branch
    (`DEFAULT_COUNTRY`) is exercised here by mutation rather than by the
    fixture."""
    del payload["record"]["organizationData"]["countryCode"]

    mapped = map_notice(payload)

    assert mapped.notice.country == "LV"


# --- dates: published from the wrapper, deadline parsed by rule --------------


def test_published_at_is_the_wrappers_day_for_all_49(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert all(m.notice.published_at is not None for m in mapped)
    assert all(m.notice.published_at.date() == DAY for m in mapped)


def test_deadline_is_parsed_on_all_49(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert all(m.notice.deadline_at is not None for m in mapped)


def test_one_real_multi_lot_deadline_reshapes_correctly(payloads):
    """7b0d80d5...: 4 lots, every one DD/MM/YYYY 05/10/2026 10:00, day-first
    confirmed against the recorded data (see the module docstring)."""
    multi = next(p for p in payloads if len(p["record"]["lots"]) == 4)

    mapped = map_notice(multi)

    deadline = mapped.notice.deadline_at
    assert (deadline.year, deadline.month, deadline.day) == (2026, 10, 5)
    assert (deadline.hour, deadline.minute) == (10, 0)


def test_a_deadline_not_shaped_like_dd_mm_yyyy_raises(payload):
    payload["record"]["lots"][0]["tenderingProcess"]["deadlineReceiptTendersEndDate"] = "2026-10-05"

    with pytest.raises(ValueError, match="deadlineReceiptTendersEndDate"):
        map_notice(payload)


# --- value: single lot carries EUR, several lots carry a note ----------------


def test_single_lot_valued_and_unvalued_counts(payloads):
    single = [p for p in payloads if len(p["record"]["lots"]) == 1]
    mapped = [map_notice(p) for p in single]

    valued = [m for m in mapped if m.notice.estimated_value is not None]
    unvalued = [m for m in mapped if m.notice.estimated_value is None]
    assert len(valued) == SINGLE_LOT_VALUED_COUNT
    assert len(unvalued) == SINGLE_LOT_UNVALUED_COUNT
    assert all(m.notice.value_currency == "EUR" for m in valued)
    assert all(m.notice.value_note == "" for m in mapped)


def test_a_single_lot_notices_value_matches_the_lots_own_figure(payloads):
    single = [p for p in payloads if len(p["record"]["lots"]) == 1]
    valued = next(
        p for p in single if (p["record"]["lots"][0].get("additionalInformation") or {}).get("estimatedValue")
    )

    mapped = map_notice(valued)

    stated = valued["record"]["lots"][0]["additionalInformation"]["estimatedValue"]
    assert mapped.notice.estimated_value == pytest.approx(float(stated))
    assert mapped.notice.value_currency == "EUR"


def test_multi_lot_notices_never_carry_an_estimated_value(payloads):
    multi = [p for p in payloads if len(p["record"]["lots"]) > 1]
    assert len(multi) == MULTI_LOT_COUNT

    mapped = [map_notice(p) for p in multi]
    assert all(m.notice.estimated_value is None for m in mapped)
    assert all(m.notice.value_currency is None for m in mapped)


def test_multi_lot_value_note_counts(payloads):
    """3 of the 10 multi-lot notices have at least one valued lot and get a
    value_note quoting every lot; the other 7 have none valued and get an
    empty note (see the module docstring for why an all-unvalued multi-lot
    notice is not given a note saying so)."""
    multi = [p for p in payloads if len(p["record"]["lots"]) > 1]
    mapped = [map_notice(p) for p in multi]

    with_note = [m for m in mapped if m.notice.value_note != ""]
    without_note = [m for m in mapped if m.notice.value_note == ""]
    assert len(with_note) == MULTI_LOT_WITH_A_VALUE_NOTE_COUNT
    assert len(without_note) == MULTI_LOT_WITH_NO_VALUE_NOTE_COUNT


def test_a_multi_lot_value_note_quotes_every_lot_never_summed(payloads):
    multi_with_value = [
        p
        for p in payloads
        if len(p["record"]["lots"]) > 1
        and any((lot.get("additionalInformation") or {}).get("estimatedValue") for lot in p["record"]["lots"])
    ]
    assert multi_with_value

    payload = multi_with_value[0]
    mapped = map_notice(payload)

    assert mapped.notice.estimated_value is None
    for position, _lot in enumerate(payload["record"]["lots"], start=1):
        assert f"lot {position}" in mapped.notice.value_note


def test_a_zero_or_missing_lot_value_is_not_a_price(payload):
    """`published_value`'s own rule (monitor/normalise/value.py): a lot stating
    0 is as unstated as a missing one. a4f8c957 states 0.01, so this mutates
    the first payload's single lot to a bare zero instead."""
    payload["record"]["lots"][0]["additionalInformation"]["estimatedValue"] = "0"

    mapped = map_notice(payload)

    assert mapped.notice.estimated_value is None
    assert mapped.notice.value_currency is None


# --- CPV: cpvType plus additionalCpvType --------------------------------------


def test_cpv_present_on_43_and_empty_on_6(payloads):
    mapped = [map_notice(p) for p in payloads]

    present = [m for m in mapped if m.notice.cpv_codes]
    empty = [m for m in mapped if not m.notice.cpv_codes]
    assert len(present) == CPV_PRESENT_COUNT
    assert len(empty) == CPV_EMPTY_COUNT


def test_additional_cpv_types_are_included(payloads):
    with_additional = next(p for p in payloads if p["record"].get("additionalCpvType"))

    mapped = map_notice(with_additional)

    main = with_additional["record"]["cpvType"].split("-")[0]
    for extra in with_additional["record"]["additionalCpvType"]:
        assert extra.split("-")[0] in mapped.notice.cpv_codes
    assert main in mapped.notice.cpv_codes


# --- mk-contract: no tier/CPV field, and the buyer contradiction -------------


def test_the_6_mk_contract_records_map_with_no_cpv(payloads):
    mk = [p for p in payloads if p["record"]["noticeType"] == "mk-contract"]
    assert len(mk) == MK_CONTRACT_COUNT

    mapped = [map_notice(p) for p in mk]
    assert all(m.notice.cpv_codes == [] for m in mapped)
    assert all(m.notice.admin_level == "national" for m in mapped)


def test_the_6_mk_contract_records_actually_carry_a_populated_buyer(payloads):
    """The brief this module was built to says organizationData.name is absent
    on the 6 mk-contract records and that buyer should fall back to the empty
    string there. The recorded fixture contradicts that: every mk-contract
    record's organizationData.name is populated (the private-law body itself,
    e.g. `"Latvijas Samariešu apvienība"`, which is the buyer for a
    Cabinet-Regulation-104 procedure) - see the module docstring. This test
    records what the fixture actually contains rather than the brief's
    assumption."""
    mk = [p for p in payloads if p["record"]["noticeType"] == "mk-contract"]

    mapped = [map_notice(p) for p in mk]
    assert all(m.notice.buyer != "" for m in mapped)


def test_buyer_falls_back_to_empty_string_when_organizationdata_name_is_absent(payload):
    """The fallback branch the brief expected mk-contract to exercise, tested
    here by mutation since no record in the fixture actually triggers it."""
    del payload["record"]["organizationData"]["name"]

    mapped = map_notice(payload)

    assert mapped.notice.buyer == ""


# --- body: procurementProject.description, carried verbatim ------------------


def test_body_is_the_procurement_project_description(payload):
    mapped = map_notice(payload)

    expected = payload["record"]["procurementProject"]["description"].strip()
    assert mapped.notice.body == expected


def test_a_record_with_no_description_maps_with_an_empty_body(payloads):
    no_description = next(p for p in payloads if not (p["record"].get("procurementProject") or {}).get("description"))

    mapped = map_notice(no_description)

    assert mapped.notice.body == ""


# --- url and external id come from the connector ------------------------------


def test_url_matches_the_connectors_own_notice_url(payloads):
    from monitor.connectors.iub_lv import notice_url

    for p in payloads:
        mapped = map_notice(p)
        expected = notice_url("https://open.iub.gov.lv/data/notice", DAY, p["record"])
        assert mapped.notice.url == expected


def test_external_id_is_identifier(payload):
    mapped = map_notice(payload)

    assert mapped.notice.external_id == payload["record"]["identifier"]


# --- raise cases ---------------------------------------------------------------


def test_a_payload_missing_a_top_level_key_raises(payload):
    del payload["published_day"]

    with pytest.raises(ValueError, match="published_day"):
        map_notice(payload)


def test_a_missing_title_raises_naming_the_id(payload):
    identifier = payload["record"]["identifier"]
    payload["record"]["name"] = ""

    with pytest.raises(ValueError, match=identifier):
        map_notice(payload)


def test_a_record_with_no_lots_raises(payload):
    payload["record"]["lots"] = []

    with pytest.raises(ValueError, match="no lots"):
        map_notice(payload)


def test_a_missing_identifier_raises(payload):
    payload["record"]["identifier"] = ""

    with pytest.raises(ValueError, match="identifier"):
        map_notice(payload)
