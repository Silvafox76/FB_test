"""Senegal APPEL tenders, against the pass recorded live on 2026-09-13.

The fixture (`tests/contract/fixtures/senegal.json`) is the verbatim response of
`GET https://api.achatspublics.sn/anon/tdo?size=200`: one page, the whole archive,
35 rows. There is no second document to record - see
`monitor/connectors/senegal.py`'s module docstring for why no per-record detail
call is made.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.senegal import (
    EXPECTED_STATUS,
    LOOKBACK_DAYS,
    NOTICE_URL,
    PAGE_SIZE,
    REQUIRED_FIELDS,
    SenegalConnector,
    parse_response,
    publication_date,
    within_window,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "senegal.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "senegal.yaml"

# The live pass this fixture was recorded from, 2026-09-13. cutoff() is relative to
# "today", so every window-cut test pins it to that day rather than the real one.
RECORDED_ON = date(2026, 9, 13)

# What the recorded pass actually held. Asserted rather than described, so a
# re-recorded fixture that quietly holds a different archive fails here and not
# somewhere subtler. Matches sources/senegal.yaml's own "last 30 days: 4" figure.
ARCHIVE_ROWS = 35
IN_WINDOW = 4


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> SenegalConnector:
    return SenegalConnector(source, [])


@pytest.fixture(scope="module")
def rows(document) -> list[dict]:
    return parse_response(document)


@pytest.fixture(scope="module")
def cutoff(connector) -> date:
    return connector.cutoff(today=RECORDED_ON)


@pytest.fixture(scope="module")
def wanted(rows, cutoff) -> list[dict]:
    return within_window(rows, cutoff)


# --- cpv_prefixes, taken and unused ---------------------------------------------


def test_cpv_prefixes_is_accepted_but_unused(connector):
    """No CPV or UNSPSC code appears on any row; see sources/senegal.yaml and the
    module docstring. Taken so every connector is built the same shape."""
    assert connector.cpv_prefixes == []


# --- the response container ------------------------------------------------------


def test_the_recorded_response_is_the_whole_archive(rows):
    assert len(rows) == ARCHIVE_ROWS


def test_every_row_has_what_the_window_cut_and_the_mapper_will_need(rows):
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert row.get(field), f"{row.get('uid', '?')} is missing {field}"
        assert row["status"] == EXPECTED_STATUS


def test_a_field_missing_from_a_row_raises(document):
    broken = json.loads(json.dumps(document))
    del broken["data"]["content"][0]["publicationDate"]

    with pytest.raises(ValueError, match="publicationDate"):
        parse_response(broken)


def test_an_unexpected_status_raises(document):
    """PUBLISHED is the only status measured across the recorded 35-row archive."""
    broken = json.loads(json.dumps(document))
    broken["data"]["content"][0]["status"] = "DRAFT"

    with pytest.raises(ValueError, match="DRAFT"):
        parse_response(broken)


def test_success_false_raises(document):
    broken = json.loads(json.dumps(document))
    broken["success"] = False

    with pytest.raises(ValueError, match="not successful"):
        parse_response(broken)


def test_a_renamed_container_raises(document):
    broken = json.loads(json.dumps(document))
    broken["data"]["results"] = broken["data"].pop("content")

    with pytest.raises(ValueError, match="data.content"):
        parse_response(broken)


def test_content_as_a_non_list_raises(document):
    broken = json.loads(json.dumps(document))
    broken["data"]["content"] = {"not": "a list"}

    with pytest.raises(ValueError, match="data.content"):
        parse_response(broken)


def test_more_than_one_page_raises(document):
    """The one-page-fits-all assumption this connector is built on; see the
    module docstring's PAGINATION HAZARD."""
    broken = json.loads(json.dumps(document))
    broken["data"]["totalPages"] = 2

    with pytest.raises(ValueError, match="one-page-fits-all"):
        parse_response(broken)


def test_a_totalelements_mismatch_raises(document):
    """A response reporting more rows than it actually returned is a partial
    archive dressed as a complete one."""
    broken = json.loads(json.dumps(document))
    broken["data"]["totalElements"] = broken["data"]["totalElements"] + 1

    with pytest.raises(ValueError, match="partial archive"):
        parse_response(broken)


def test_a_numberofelements_mismatch_raises(document):
    broken = json.loads(json.dumps(document))
    broken["data"]["numberOfElements"] = broken["data"]["numberOfElements"] + 1

    with pytest.raises(ValueError, match="partial archive"):
        parse_response(broken)


# --- the rows are not sorted -----------------------------------------------------


def test_the_recorded_rows_are_not_sorted_by_publication_date(rows):
    """`data.sort` is an empty list on the live response; measured directly rather
    than assumed, so nobody 'optimises' the window cut into a walk that stops at
    the first old row the way monitor/connectors/ebrd.py's own hazard warns against."""
    dates = [publication_date(row) for row in rows]

    assert dates != sorted(dates)
    assert dates != sorted(dates, reverse=True)


# --- the window cut ---------------------------------------------------------------


def test_the_window_is_thirty_days_before_the_run(connector):
    assert connector.cutoff(today=RECORDED_ON) == RECORDED_ON - timedelta(days=LOOKBACK_DAYS)
    assert LOOKBACK_DAYS == 30


def test_the_window_cut_keeps_the_measured_count_and_is_within_the_registrys_health_band(wanted, cutoff, source):
    assert len(wanted) == IN_WINDOW
    assert all(publication_date(row) >= cutoff for row in wanted)
    assert source.expected_min <= len(wanted) <= source.expected_max


def test_publication_date_parses_the_recorded_format():
    row = {"uid": "x", "publicationDate": "2026-09-11T13:23:14.740+00:00"}
    assert publication_date(row) == date(2026, 9, 11)


def test_publication_date_raises_when_absent():
    with pytest.raises(ValueError, match="no publicationDate"):
        publication_date({"uid": "x", "publicationDate": None})


def test_publication_date_raises_on_an_unparseable_value():
    with pytest.raises(ValueError, match="publicationDate"):
        publication_date({"uid": "x", "publicationDate": "11/09/2026"})


# --- declared language ------------------------------------------------------------


def test_the_registrys_declared_language_is_french_and_the_data_agrees(source, rows):
    """No row carries an explicit language field, so this is checked against
    closed-vocabulary French phrases the platform itself writes on every row -
    sources/senegal.yaml's own LANGUAGE AND CODES finding."""
    assert source.language == "fr"

    market_types = {row["marketType"]["libelle"] for row in rows}
    passation_modes = {row["passationMode"]["libelle"] for row in rows}

    assert market_types == {"Services", "Fourniture", "Travaux"}
    assert any("Appel d'offres" in mode for mode in passation_modes)


# --- what one whole pass yields ----------------------------------------------------


def replaying_client(document: dict) -> httpx.Client:
    """A client that serves the recorded response and refuses anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/anon/tdo") and request.url.params.get("size") == str(PAGE_SIZE):
            return httpx.Response(200, json=document)
        raise AssertionError(f"the connector asked for {request.url}, which is not part of the recorded pass")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_yields_the_measured_count(connector, document, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: RECORDED_ON - timedelta(days=LOOKBACK_DAYS))

    with replaying_client(document) as client:
        raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == IN_WINDOW
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "senegal"
        assert raw.mime == "application/json"
        assert raw.url.startswith(NOTICE_URL.split("{uid}")[0])
        seen_urls.add(raw.url)

        payload = json.loads(raw.payload)
        for field in REQUIRED_FIELDS:
            assert payload.get(field)

    assert len(seen_urls) == IN_WINDOW, "each notice must have its own uid-built URL"


def test_only_one_request_is_made_for_a_whole_pass(connector, document, monkeypatch):
    """rule 21: one polite pass. No per-record detail call - see the module
    docstring for why the listing row already carries everything needed."""
    monkeypatch.setattr(connector, "cutoff", lambda today=None: RECORDED_ON - timedelta(days=LOOKBACK_DAYS))
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(200, json=document)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector.fetch_raw(client)

    assert len(requested) == 1


# --- rule 4: a changed layout fails loudly, exercised end to end -----------------


def test_a_broken_response_fails_the_whole_pass_rather_than_yielding_partial_results(connector, document, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: RECORDED_ON - timedelta(days=LOOKBACK_DAYS))
    broken = json.loads(json.dumps(document))
    broken["data"]["totalPages"] = 3

    with replaying_client(broken) as client, pytest.raises(ValueError, match="one-page-fits-all"):
        connector.fetch_raw(client)


# --- the registry and this connector agree ----------------------------------------


def test_the_registry_entry_matches_what_this_connector_is_built_for(source):
    assert source.id == "senegal"
    assert source.access_type == "api"
    assert source.connector_class == "FeedConnector"
    assert source.country == "SN"
    assert source.api_url == "https://api.achatspublics.sn/anon/tdo"
    # Still false as of this build; enabling this source is a later step's act,
    # not this one's (see sources/senegal.yaml's own note).
    assert source.tos_status == "reviewed_ok"
