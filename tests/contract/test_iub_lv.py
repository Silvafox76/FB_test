"""IUB Open Data's contract, against the whole day 2026-09-18 recorded live
2026-09-19 (`GET /data/notice/2026/09/18-09-2026.json`, 215 records of every
notice type IUB published that day).

No `monitor.normalise.iub_lv` exists yet -- the normaliser is out of this
agent's lane -- so this file checks the raw record at the field paths a future
mapper would read, and locks the two properties `sources/iub_lv.yaml`
depends on: only the five confirmed live-call-for-tenders types survive
`select()`, and a day with no file yet fails loudly rather than reading as a
quiet day (see monitor/connectors/iub_lv.py's module docstring for why that
is not the 404 the registry entry assumed).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.iub_lv import (
    KEPT_NOTICE_TYPES,
    LOOKBACK_DAYS,
    IubLvConnector,
    day_url,
    notice_url,
    select,
    wrap,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "iub_lv.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "iub_lv.yaml"

RECORDED_DAY = date(2026, 9, 18)

# sources/iub_lv.yaml's own measured counts for this exact day, kept types only.
EXPECTED_TYPE_COUNTS = {
    "pil-contract": 33,
    "pil-contract-social": 8,
    "mk-contract": 6,
    "sps-contract": 1,
    "sps-iv-contract": 1,
}


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> IubLvConnector:
    return IubLvConnector(source, [])


@pytest.fixture(scope="module")
def kept(records, source) -> list[dict]:
    return select(records, exclude_notice_types=source.exclude_notice_types)


# --- the recorded day, and the registry's own count of it --------------------


def test_the_fixture_is_the_whole_day_not_pre_filtered(records):
    """215 records of every notice type IUB published on 2026-09-18, not only
    the kept ones -- a connector that only ever saw pre-filtered data could
    not prove its own filter works."""
    assert len(records) == 215
    assert len(set(record["noticeType"] for record in records)) > len(KEPT_NOTICE_TYPES)


def test_the_day_yields_within_the_registrys_expected_range(kept, source):
    assert source.expected_min <= len(kept) <= source.expected_max


def test_the_count_per_type_matches_the_registrys_own_measurement(kept):
    assert Counter(record["noticeType"] for record in kept) == Counter(EXPECTED_TYPE_COUNTS)
    assert sum(EXPECTED_TYPE_COUNTS.values()) == 49


def test_only_kept_types_survive_selection(kept):
    assert {record["noticeType"] for record in kept} == set(EXPECTED_TYPE_COUNTS)
    assert KEPT_NOTICE_TYPES == frozenset(EXPECTED_TYPE_COUNTS)


def test_excluded_and_unresolved_types_are_dropped(records, kept, source):
    """pil-award and its post-decision siblings (sources/iub_lv.yaml's
    exclude_notice_types) and the registry's own unresolved fourth group
    (pil-planned-contract, pil-prior-information, pil-prior-shorten,
    sps-periodic-information, pil-discussion) both vanish, for two different
    reasons the module docstring explains. Checked as a superset the day's
    actual types must fall inside, not an exact set: pil-exante, adjil-award,
    pil-design-result and sps-discussion are in the registry's own vocabulary
    but did not happen to publish on this particular day."""
    dropped_types = {record["noticeType"] for record in records} - {record["noticeType"] for record in kept}
    unresolved = {
        "pil-planned-contract",
        "pil-prior-information",
        "pil-prior-shorten",
        "sps-periodic-information",
        "pil-discussion",
        "sps-discussion",
    }
    assert dropped_types <= frozenset(source.exclude_notice_types) | unresolved
    # Every excluded type actually present on this day was in fact dropped.
    assert dropped_types & frozenset(source.exclude_notice_types) == {
        "pil-award",
        "pil-award-social",
        "pil-concluded-contract",
        "sps-award",
        "sps-iv-result",
        "mk-result",
        "contract-modification",
        "contract-execution",
    }
    assert dropped_types & unresolved == {
        "pil-planned-contract",
        "pil-prior-information",
        "pil-prior-shorten",
        "sps-periodic-information",
        "pil-discussion",
    }


def test_a_record_with_no_noticetype_raises(records):
    broken = [dict(records[0])]
    del broken[0]["noticeType"]

    with pytest.raises(ValueError, match="no noticeType"):
        select(broken, exclude_notice_types=[])


def test_a_registry_edit_excluding_a_kept_type_is_honoured(kept, source):
    """exclude_notice_types is still checked alongside KEPT_NOTICE_TYPES, so a
    future registry edit that excludes one of the five is not silently
    overridden by this module's own constant."""
    pil_contract_count = sum(1 for record in kept if record["noticeType"] == "pil-contract")
    assert pil_contract_count > 0

    tightened = select(
        [dict(record) for record in kept], exclude_notice_types=[*source.exclude_notice_types, "pil-contract"]
    )
    assert all(record["noticeType"] != "pil-contract" for record in tightened)
    assert len(tightened) == len(kept) - pil_contract_count


# --- ids -----------------------------------------------------------------


def test_every_kept_record_has_a_unique_identifier(kept):
    ids = [record["identifier"] for record in kept]
    assert len(ids) == len(set(ids)) == 49
    assert all(isinstance(record_id, str) and record_id.strip() for record_id in ids)


# --- fields a future normaliser needs ----------------------------------------


def test_every_kept_record_has_the_fields_the_mapper_reads(kept):
    for record in kept:
        for field in ("identifier", "name", "noticeType", "organizationData", "lots", "tenderingTerms", "cpvType"):
            assert field in record, f"{record.get('identifier')} has no {field}"
        assert record["name"].strip()
        assert record["organizationData"]["name"].strip()


def test_a_missing_required_field_raises_rather_than_yielding_fewer(records, source):
    broken = [dict(record) for record in records]
    for record in broken:
        if record["noticeType"] == "pil-contract":
            del record["organizationData"]
            break

    with pytest.raises(ValueError, match="organizationData"):
        select(broken, exclude_notice_types=source.exclude_notice_types)


def test_the_buyer_country_is_always_latvia(kept):
    """organizationData.countryCode, ISO alpha-3 on this source (like TED's
    own three-letter codes) -- LV is the two-letter form the registry, the
    lexicons and config/thresholds.yaml all use, so a normaliser maps it."""
    codes = {record["organizationData"]["countryCode"] for record in kept}
    assert codes == {"LVA"}


# --- CPV: present where the data has it, absent where it does not ------------


def test_cpv_is_present_on_most_kept_records_and_absent_on_the_rest(kept):
    """43 of 49: cpvType, an 8-digit-plus-check-digit code. The other 6 are
    every mk-contract record in the day, which publishes none -- absence, not
    a failed match, the same asymmetry monitor/filter/cpv.py already assumes
    for every other source."""
    with_cpv = [record for record in kept if record.get("cpvType")]
    without_cpv = [record for record in kept if not record.get("cpvType")]

    assert len(with_cpv) == 43
    assert {record["noticeType"] for record in without_cpv} == {"mk-contract"}
    for record in with_cpv:
        code = record["cpvType"]
        assert len(code) == 10 and code[:8].isdigit() and code[8] == "-"


def test_additional_cpv_codes_are_a_list_when_present(kept):
    with_additional = [record for record in kept if record.get("additionalCpvType")]
    assert with_additional, "expected at least one kept record with additionalCpvType"
    for record in with_additional:
        assert isinstance(record["additionalCpvType"], list)
        assert all(isinstance(code, str) for code in record["additionalCpvType"])


# --- deadlines, present on every kept record ---------------------------------


def test_every_kept_record_states_a_lot_deadline(kept):
    """lots[].tenderingProcess.deadlineReceiptTendersEndDate, DD/MM/YYYY: every
    one of the 49 kept records states one on at least one lot, matching
    sources/iub_lv.yaml's claim that the five kept types carry no award
    decision."""
    for record in kept:
        lots = record.get("lots") or []
        dates = [
            (lot.get("tenderingProcess") or {}).get("deadlineReceiptTendersEndDate")
            for lot in lots
            if isinstance(lot.get("tenderingProcess"), dict)
        ]
        assert any(dates), f"{record['identifier']} states no lot deadline"


# --- value: present on 46 of 49 kept records' lots, never with a currency ---


def test_lot_value_is_stated_without_a_currency(kept, records):
    """lots[].additionalInformation.estimatedValue: a bare numeric string on
    46 of the 49 kept records' lots, no currency field anywhere in the whole
    day's payload (checked on all 215 records, not just the kept 49, since
    the absence is a property of the source, not of the filter)."""
    stated = []
    for record in kept:
        for lot in record.get("lots") or []:
            value = (lot.get("additionalInformation") or {}).get("estimatedValue")
            if value not in (None, ""):
                stated.append(value)

    assert len(stated) == 46
    for value in stated:
        float(value)  # a bare number, parses without a currency symbol or code

    # "recurrence"/"recurrenceDescription" (a framework-agreement field, nothing
    # to do with money) are the only "curr"-containing keys anywhere in the
    # whole day; excluded by name rather than by a looser substring check.
    all_keys = {key for record in records for key in _all_keys(record)}
    currency_like = {key for key in all_keys if "curr" in key.lower()} - {"recurrence", "recurrenceDescription"}
    assert currency_like == set()


def _all_keys(obj) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(key)
            found |= _all_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _all_keys(item)
    return found


# --- the url: EIS where a buyer runs one, the day file otherwise ------------


def test_the_url_is_the_eis_procedure_page_for_every_type_but_mk_contract(kept, source):
    eis_typed = [record for record in kept if record["noticeType"] != "mk-contract"]
    assert eis_typed

    for record in eis_typed:
        url = notice_url(source.api_url, RECORDED_DAY, record)
        assert url.startswith("https://www.eis.gov.lv/EKEIS/Supplier/Procurement/")
        assert url == record["tenderingProcess"]["documentsURL"]


def test_mk_contract_has_no_eis_page_and_falls_back_to_the_day_file(kept, source):
    """mk-contract publishes tenderingProcess as an empty list, not an object
    -- a real shape of this source, not a fixture defect -- and its
    submissionURL is free text, never a URL."""
    mk_records = [record for record in kept if record["noticeType"] == "mk-contract"]
    assert len(mk_records) == 6

    for record in mk_records:
        assert record["tenderingProcess"] == []
        assert not str(record["tenderingTerms"].get("submissionURL", "")).startswith("http")

        url = notice_url(source.api_url, RECORDED_DAY, record)
        assert url == f"{day_url(source.api_url, RECORDED_DAY)}?identifier={record['identifier']}"


def test_day_url_matches_the_live_path(source):
    assert day_url(source.api_url, RECORDED_DAY) == "https://open.iub.gov.lv/data/notice/2026/09/18-09-2026.json"


# --- the default window -------------------------------------------------


def test_the_default_window_is_the_trailing_two_days_ending_yesterday(connector):
    assert connector.days(today=date(2026, 9, 19)) == [date(2026, 9, 18), date(2026, 9, 17)]
    assert len(connector.days(today=date(2026, 9, 19))) == LOOKBACK_DAYS


def test_since_after_until_raises(connector):
    with pytest.raises(ValueError, match="is after"):
        connector.days(since=date(2026, 9, 19), until=date(2026, 9, 17))


def test_an_explicit_window_is_honoured(connector):
    assert connector.days(since=date(2026, 9, 1), until=date(2026, 9, 3)) == [
        date(2026, 9, 3),
        date(2026, 9, 2),
        date(2026, 9, 1),
    ]


# --- redaction held -----------------------------------------------------


def test_named_individual_contact_details_were_stripped_from_the_fixture(records):
    """Rule 19: no staff or buyer-contact personal detail sits in a committed
    fixture. Checks the redaction held rather than trusting the module
    docstring's count."""
    checked_a_named_contact = False

    def walk(obj):
        nonlocal checked_a_named_contact
        if isinstance(obj, dict):
            keys = frozenset(obj.keys())
            if keys in (
                frozenset({"electronicMail", "id", "telephone"}),
                frozenset({"electronicMail", "name", "telephone"}),
                frozenset({"electronicMail", "id", "isDefault", "name", "noticeId", "telephone"}),
            ):
                if obj.get("name"):
                    checked_a_named_contact = True
                    assert obj["name"] == "REDACTED (named individual, rule 19)"
                if obj.get("electronicMail"):
                    assert obj["electronicMail"] == "redacted@redacted.invalid"
                if obj.get("telephone"):
                    assert obj["telephone"] == "REDACTED"
                return
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(records)
    assert checked_a_named_contact, "expected the recorded day to carry a redacted individual contact"


def test_no_personal_email_survives_in_free_text_submission_fields(records):
    """The 6 mk-contract records that name a bidding contact inside free-text
    submissionURL rather than a structured contact object."""
    mk_records = [record for record in records if record["noticeType"] == "mk-contract"]
    submission_texts = [record["tenderingTerms"].get("submissionURL", "") for record in mk_records]

    assert any("@" in text for text in submission_texts)
    for text in submission_texts:
        if "@" in text:
            local_part = text.split("@")[0].rsplit(" ", 1)[-1].rsplit(":", 1)[-1]
            # Every address in the fixture is the placeholder since 2026-09-19 (rule 19).
            assert local_part == "redacted", f"a personal local part survived: {text!r}"


# --- language: declared by the registry, not by the payload ------------------


def test_the_registry_declares_latvian_and_the_payload_confirms_it(source, kept):
    assert source.language == "lv"
    sample_titles = " ".join(record["name"] for record in kept[:10])
    assert any(letter in sample_titles for letter in "āēīūčģķļņšž")


# --- one whole pass, replayed --------------------------------------------


def replaying_client(document_by_day: dict[date, object]) -> httpx.Client:
    """A client that serves only the recorded days and answers anything else
    with the real, live-observed shape of a day IUB has no file for: HTTP 200,
    `text/html`, not JSON. No 404 anywhere -- see the module docstring for why
    that would be the wrong thing to simulate.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "open.iub.gov.lv"
        for day, document in document_by_day.items():
            if request.url.path.endswith(day_url("", day)):
                return httpx.Response(200, json=document)
        return httpx.Response(200, html="<html><body>Not Found</body></html>")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_keeps_only_the_five_confirmed_types(source, records):
    connector = IubLvConnector(source, [])
    with replaying_client({RECORDED_DAY: records, date(2026, 9, 17): []}) as client:
        raw_notices = connector.fetch_raw(client, since=RECORDED_DAY, until=RECORDED_DAY)

    assert len(raw_notices) == 49
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "iub_lv"
        assert raw.mime == "application/json"
        payload = json.loads(raw.payload)

        # The payload is not the bare record: the record carries no publication
        # date at all (see the module docstring), so fetch_raw wraps it with
        # the day of the file it was read from before it is stored.
        assert set(payload) == {"published_day", "record"}
        assert payload["published_day"] == RECORDED_DAY.isoformat()
        assert payload["record"]["noticeType"] in KEPT_NOTICE_TYPES

        seen_urls.add(raw.url)

    assert len(seen_urls) == 49, "each notice must have its own url"


def test_wrap_carries_the_days_own_file_date_beside_the_untouched_record():
    record = {"identifier": "abc", "noticeType": "pil-contract"}

    wrapped = wrap(RECORDED_DAY, record)

    assert wrapped == {"published_day": "2026-09-18", "record": record}
    assert wrapped["record"] is record


def test_a_day_with_no_file_yet_raises_rather_than_reading_as_quiet(source):
    """The real, live-observed shape: HTTP 200 with an HTML body, not a 404
    and not JSON. `.json()` on that body is what raises."""
    connector = IubLvConnector(source, [])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<html><body>Not Found</body></html>")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(json.JSONDecodeError):
        connector.fetch_raw(client, since=RECORDED_DAY, until=RECORDED_DAY)


def test_a_non_json_list_response_raises(source):
    """A response that parses as JSON but is not the list every real day
    publishes -- a changed API shape, not a quiet day."""
    connector = IubLvConnector(source, [])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"notices": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="not a JSON list"):
            connector.fetch_raw(client, since=RECORDED_DAY, until=RECORDED_DAY)


def test_a_non_200_response_raises(source):
    """No try/except in the connector: an HTTP error propagates from raise_for_status."""
    connector = IubLvConnector(source, [])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        connector.fetch_raw(client, since=RECORDED_DAY, until=RECORDED_DAY)
