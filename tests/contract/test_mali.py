"""Mali's SIGMAP `dossier-sigmap` listing, against the pass recorded live on 2026-09-13.

The fixture (`tests/contract/fixtures/mali.json`) is the whole response of one
`GET https://marchespublics.ml/portail/api/sigmap/dossiers/dossier-sigmap`, 98
rows, exactly as it arrived - see `monitor/connectors/mali.py`'s module
docstring for the field-by-field measurement this file's tests hold it to.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.mali import (
    EXPECTED_DOS_STATUS,
    EXPECTED_DTP_CODES,
    LOOKBACK_DAYS,
    NOTICE_URL,
    REQUIRED_FIELDS,
    MaliConnector,
    dossier_date,
    parse_dossiers,
    within_window,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "mali.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "mali.yaml"

# The live pass this fixture was recorded from, 2026-09-13. cutoff() is
# relative to "today", so every window-cut test pins it to that day rather
# than the real one.
RECORDED_ON = date(2026, 9, 13)

# What the recorded pass actually held. Asserted rather than described, so a
# re-recorded fixture that quietly holds a different corpus fails here and not
# somewhere subtler. Matches sources/mali.yaml's own two live measurements
# (2026-09-12 and 2026-09-13), both 26.
RECORDED_TOTAL = 98
RECORDED_IN_WINDOW = 26


@pytest.fixture(scope="module")
def fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> MaliConnector:
    return MaliConnector(source, [])


@pytest.fixture(scope="module")
def rows(fixture) -> list[dict]:
    return parse_dossiers(fixture)


@pytest.fixture(scope="module")
def cutoff(connector) -> date:
    return connector.cutoff(today=RECORDED_ON)


@pytest.fixture(scope="module")
def wanted(rows, cutoff) -> list[dict]:
    return within_window(rows, cutoff)


# --- cpv_prefixes, taken and unused --------------------------------------------


def test_cpv_prefixes_is_accepted_but_unused(connector):
    """No CPV code appears anywhere on this platform; see the module docstring."""
    assert connector.cpv_prefixes == []


# --- the registry entry, read as this connector reads it -----------------------


def test_the_registry_declares_no_cpv_scope_and_no_row_selector(source):
    assert source.access_type == "api"
    assert source.connector_class == "FeedConnector"
    assert source.api_url == "https://marchespublics.ml/portail/api/sigmap/dossiers/dossier-sigmap"
    assert source.tos_status == "reviewed_ok"
    assert source.enabled is False, "enabling follows a passing contract test, per the onboarding brief"


def test_the_window_is_seven_days_before_the_run(connector):
    assert connector.cutoff(today=RECORDED_ON) == date(2026, 9, 6)
    assert LOOKBACK_DAYS == 7


# --- the corpus, as fetched -----------------------------------------------------


def test_the_recorded_response_is_the_whole_measured_corpus(rows):
    assert len(rows) == RECORDED_TOTAL


def test_every_row_has_what_the_window_cut_and_the_mapper_need(rows):
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert field in row


def test_every_row_is_pre_decision_and_a_known_notice_type(rows):
    """sources/mali.yaml: every one of the 98 rows carries dosStatus 1 and dtpCode
    AAO (52) or AMI (46)."""
    assert {row["dosStatus"] for row in rows} == {EXPECTED_DOS_STATUS}
    types = [row["dtpCode"] for row in rows]
    assert set(types) == set(EXPECTED_DTP_CODES)
    assert types.count("AAO") == 52
    assert types.count("AMI") == 46


def test_no_row_carries_a_value_currency_or_contact_field(rows):
    """No montant/devise/price field and no email/phone/named-individual field
    exists anywhere in this listing; see the module docstring. Guards a
    re-recorded fixture quietly picking up a field this connector was never
    built to see, which the normaliser would need to know about before a model
    call ever reaches it (rule 19)."""
    forbidden_substrings = ("mail", "phone", "tel", "fax", "montant", "devise", "prix")
    for row in rows:
        for key in row:
            lowered = key.lower()
            assert not any(token in lowered for token in forbidden_substrings), key


# --- shape failures raise loudly -------------------------------------------------


def test_a_response_that_is_not_a_list_raises():
    with pytest.raises(ValueError, match="not a JSON array"):
        parse_dossiers({"items": []})


def test_a_row_that_is_not_an_object_raises(fixture):
    broken = json.loads(json.dumps(fixture))
    broken[0] = "not-a-row"

    with pytest.raises(ValueError, match="not a JSON object"):
        parse_dossiers(broken)


def test_a_row_missing_a_required_field_raises(fixture):
    broken = json.loads(json.dumps(fixture))
    del broken[0]["dosDate"]

    with pytest.raises(ValueError, match="dosDate"):
        parse_dossiers(broken)


def test_an_unexpected_dos_status_raises(fixture):
    broken = json.loads(json.dumps(fixture))
    broken[3]["dosStatus"] = 2

    with pytest.raises(ValueError, match="dosStatus"):
        parse_dossiers(broken)


def test_an_unexpected_dtp_code_raises(fixture):
    broken = json.loads(json.dumps(fixture))
    broken[5]["dtpCode"] = "AVIS-ANNULATION"

    with pytest.raises(ValueError, match="dtpCode"):
        parse_dossiers(broken)


# --- the window cut ---------------------------------------------------------------


def test_the_window_cut_keeps_the_measured_count(wanted, cutoff, source):
    assert len(wanted) == RECORDED_IN_WINDOW
    assert all(dossier_date(row) >= cutoff for row in wanted)
    assert source.expected_min <= len(wanted) <= source.expected_max


def test_dossier_date_reads_an_eight_digit_yyyymmdd_string():
    assert dossier_date({"id": "x", "dosDate": "20260911"}) == date(2026, 9, 11)


def test_dossier_date_raises_on_a_non_eight_digit_string():
    with pytest.raises(ValueError, match="not an 8-digit"):
        dossier_date({"id": "x", "dosDate": "2026-09-11"})


def test_dossier_date_raises_on_an_impossible_date():
    with pytest.raises(ValueError, match="day is out of range"):
        dossier_date({"id": "x", "dosDate": "20260231"})


def test_the_rows_are_not_reliably_sorted_which_is_why_the_whole_array_is_scanned(rows):
    """The measured hazard this connector's within_window is shaped around: a walk
    that stopped at the first old row would happen to work on this recording, but
    nothing here promises it holds, so the whole array is filtered instead."""
    dates = [dossier_date(row) for row in rows]

    assert dates != sorted(dates, reverse=True)
    out_of_order = [i for i in range(1, len(dates)) if dates[i] > dates[i - 1]]
    assert out_of_order, "the recorded fixture no longer demonstrates the hazard this test guards"


# --- one whole pass, replayed ------------------------------------------------------


def replaying_client(fixture: list[dict]) -> httpx.Client:
    """A client that serves the recorded corpus and refuses anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/portail/api/sigmap/dossiers/dossier-sigmap"):
            return httpx.Response(200, json=fixture)
        raise AssertionError(f"the connector asked for {request.url}, which is not part of the recorded pass")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_yields_the_measured_count(connector, fixture, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 9, 6))

    with replaying_client(fixture) as client:
        raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == RECORDED_IN_WINDOW
    for raw in raw_notices:
        assert raw.source_id == "mali"
        assert raw.mime == "application/json"
        assert raw.url == NOTICE_URL

        payload = json.loads(raw.payload)
        for field in REQUIRED_FIELDS:
            assert field in payload
        assert dossier_date(payload) >= date(2026, 9, 6)


def test_a_run_makes_exactly_one_request(connector, fixture, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 9, 6))
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=fixture)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector.fetch_raw(client)

    assert requested == ["https://marchespublics.ml/portail/api/sigmap/dossiers/dossier-sigmap"]
