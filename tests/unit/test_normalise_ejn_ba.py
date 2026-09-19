"""The EJN e-Nabavke BiH normaliser: one already-joined payload to a Notice.

`monitor/normalise/ejn_ba.py` has no fixture of its own; it is written and
measured directly against the 672 payloads `monitor/connectors/ejn_ba.py`'s own
`build_payload` produces from the notices, lots, links and CPV codes recorded in
`tests/contract/fixtures/ejn_ba.json.gz` (the same 62-request transcript
`tests/contract/test_ejn_ba.py` replays), so this test runs the fixture through
the connector's own join rather than duplicating it or guessing at payload shape.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from monitor.connectors.ejn_ba import build_payload
from monitor.normalise.ejn_ba import CYRILLIC, map_notice

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures" / "ejn_ba.json.gz"

# What the recorded pass actually holds. Asserted rather than assumed, so a
# re-recorded fixture that quietly changed shape fails here and not somewhere
# subtler.
NOTICE_COUNT = 672
# Two pairs of notices - different Ids, different buyers, sharing the same
# generic title ("Nabavka pelleta" / "Nabavka kancelarijskog namjestaja") and the
# same lot description text - collide on content_hash. This is a measured
# property of the recorded window, not a defect in this module: content_hash is
# title-plus-body by design (monitor/normalise/hashing.py) and deduplication
# beyond that is a later stage's job, not the normaliser's.
DISTINCT_HASH_COUNT = 670
ADMIN_UNIT_TYPES = {"Country", "Entity", "Canton", "District", "City", "Municipality"}


def _rows_from(fixture: dict, path_fragment: str) -> list[dict]:
    """Every `value` row from every recorded request whose URL names this entity.
    The same helper `tests/contract/test_ejn_ba.py` uses for its own fixtures."""
    rows: list[dict] = []
    for record in fixture["requests"]:
        if path_fragment in record["url"]:
            rows.extend(record["body"]["value"])
    return rows


@pytest.fixture(scope="module")
def payloads() -> list[dict]:
    """The 672 recorded payloads, built by the connector's own `build_payload` -
    not reconstructed here - from the notices, lots, links and CPV codes recorded
    in the fixture."""
    fixture = json.load(gzip.open(FIXTURE, "rt", encoding="utf-8"))
    notices = _rows_from(fixture, "open.ejn.gov.ba/ProcurementNotices")
    lots = _rows_from(fixture, "/Lots?")
    links = _rows_from(fixture, "/LotCpvCodeLinks?")
    codes = _rows_from(fixture, "/CpvCodes?")
    return [build_payload(notice, lots, links, codes) for notice in notices]


@pytest.fixture()
def payload(payloads) -> dict:
    """The first payload, deep-copied so a test can mutate it."""
    return json.loads(json.dumps(payloads[0]))


# --- every payload maps ------------------------------------------------------


def test_all_672_payloads_map_without_raising(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert len(mapped) == NOTICE_COUNT


def test_the_distinct_content_hash_count_is_670_not_672(payloads):
    """Reported rather than assumed: two genuine title+body collisions between
    distinct notices (see DISTINCT_HASH_COUNT above)."""
    mapped = [map_notice(p) for p in payloads]

    hashes = {m.notice.content_hash for m in mapped}
    assert len(hashes) == DISTINCT_HASH_COUNT


# --- admin level, all six enum values ----------------------------------------


def test_the_six_administrative_unit_values_all_map(payloads):
    mapped = [map_notice(p) for p in payloads]

    seen_unit_types = {p["notice"]["ContractingAuthorityAdministrativeUnitType"] for p in payloads}
    assert seen_unit_types == ADMIN_UNIT_TYPES

    seen_admin_levels = {m.notice.admin_level for m in mapped}
    assert seen_admin_levels == {"national", "regional", "local"}

    by_unit_type = {
        p["notice"]["ContractingAuthorityAdministrativeUnitType"]: m.notice.admin_level
        for p, m in zip(payloads, mapped, strict=True)
    }
    assert by_unit_type == {
        "Country": "national",
        "Entity": "national",
        "Canton": "regional",
        "District": "regional",
        "City": "local",
        "Municipality": "local",
    }


def test_an_unrecognised_administrative_unit_type_raises(payload):
    payload["notice"]["ContractingAuthorityAdministrativeUnitType"] = "Province"

    with pytest.raises(ValueError, match="Province"):
        map_notice(payload)


# --- language detection by script --------------------------------------------


def test_cyrillic_detection_on_a_real_cyrillic_notice(payloads):
    """180 of the 672 recorded notices carry Cyrillic script in ProcedureName or
    ContractingAuthorityName (monitor/connectors/ejn_ba.py's own docstring)."""
    cyrillic_payloads = [
        p for p in payloads if CYRILLIC.search(p["notice"]["ProcedureName"] + p["notice"]["ContractingAuthorityName"])
    ]
    assert len(cyrillic_payloads) == 180

    mapped = map_notice(cyrillic_payloads[0])
    assert mapped.notice.language == "sr"
    assert mapped.notice.language_confidence == 0.9


def test_a_latin_script_notice_is_bosnian_at_lower_confidence(payloads):
    latin_payload = next(
        p
        for p in payloads
        if not CYRILLIC.search(p["notice"]["ProcedureName"] + p["notice"]["ContractingAuthorityName"])
    )

    mapped = map_notice(latin_payload)
    assert mapped.notice.language == "bs"
    assert mapped.notice.language_confidence == 0.6


# --- value: single lot carries BAM, several lots carry a note ----------------


def test_a_single_lot_notices_value_carries_bam(payloads):
    single_lot = [(p, map_notice(p)) for p in payloads if len(p["lots"]) == 1]
    valued = [(p, m) for p, m in single_lot if p["lots"][0]["EstimatedValue"] is not None]
    assert valued, "expected at least one single-lot notice with a stated value"

    payload, mapped = valued[0]
    assert mapped.notice.value_currency == "BAM"
    assert mapped.notice.estimated_value == payload["lots"][0]["EstimatedValue"]
    assert mapped.notice.value_note == ""


def test_a_multi_lot_notice_has_no_estimated_value_and_a_value_note_quoting_each_lot(payloads):
    multi_lot = [(p, map_notice(p)) for p in payloads if len(p["lots"]) > 1]
    assert multi_lot, "expected at least one multi-lot notice"

    payload, mapped = multi_lot[0]
    assert mapped.notice.estimated_value is None
    assert mapped.notice.value_currency is None
    assert mapped.notice.value_note != ""
    # Every lot's figure is quoted, none summed (rule 9).
    for position, _lot in enumerate(payload["lots"], start=1):
        assert f"lot {position}" in mapped.notice.value_note


def test_no_multi_lot_notice_ever_carries_an_estimated_value(payloads):
    multi_lot_mapped = [map_notice(p) for p in payloads if len(p["lots"]) > 1]
    assert len(multi_lot_mapped) == 116

    assert all(m.notice.estimated_value is None for m in multi_lot_mapped)
    assert all(m.notice.value_currency is None for m in multi_lot_mapped)
    assert all(m.notice.value_note != "" for m in multi_lot_mapped)


# --- CPV present on every recorded notice ------------------------------------


def test_cpv_present_on_all_672(payloads):
    mapped = [map_notice(p) for p in payloads]

    assert all(m.notice.cpv_codes for m in mapped)


# --- raise cases --------------------------------------------------------------


def test_a_payload_missing_a_top_level_key_raises(payload):
    del payload["cpv_codes"]

    with pytest.raises(ValueError, match="cpv_codes"):
        map_notice(payload)


def test_a_missing_title_raises_naming_the_id(payload):
    notice_id = payload["notice"]["Id"]
    payload["notice"]["ProcedureName"] = ""

    with pytest.raises(ValueError, match=str(notice_id)):
        map_notice(payload)


def test_a_notice_with_no_lots_raises(payload):
    payload["lots"] = []

    with pytest.raises(ValueError, match="no lots"):
        map_notice(payload)
