"""Liberia PPCC e-GP OCDS records, against the pass recorded live on 2026-09-12.

The fixture (`tests/contract/fixtures/liberia.json`) holds both documents the
connector reads: `search_response`, the one sorted search page
(`POST searchRecords.action`, pagesize 60), and `compiled_releases`, a mapping of
record id to the full `downloadRecord/{id}/COMPILED.action` response for the 14
rows that fell inside that pass's own 7-day window. It is a fixture built for a
two-endpoint connector the way `tests/contract/test_simap.py`'s is, not a single
page: a test that replayed only the search would prove nothing, because the search
row carries no title, no buyer, no dates and no value at all - see
`monitor/connectors/liberia.py`'s module docstring.

**Personal data was found in the live response and is redacted here, not in the
connector.** `parties[].contactPoint` on the buyer party of every one of these 14
records carries a real phone number, and one - the Liberia Drug Enforcement
Agency's - carries a named individual's personal Gmail address rather than an
institutional one. This is the same hazard the Nigeria onboarding found inside an
OCDS `contactPoint`, described in this project's own build brief. Rule 19 and rule
20 forbid a contact detail landing in a fixture committed to git, so every
`contactPoint.email`, `.telephone` and `.faxNumber` value that was non-empty in the
live response has been replaced in `tests/contract/fixtures/liberia.json` with a
fixed placeholder (`redacted@example.invalid` / `000-000-0000`); a value that was
already empty in the live response was left empty rather than filled in, since
inventing a value where the source stated none would misrepresent what was
published. `test_the_committed_fixture_carries_no_real_contact_details` guards
against a re-recording quietly undoing this. The connector itself does not redact
anything - rule 5 leaves what becomes `Notice.body` to the normaliser that does not
exist yet, and rule 9 makes the record what the source published - this redaction
is scoped to the file checked into git, not to what the pipeline stores.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.liberia import (
    DOWNLOAD_URL,
    EXPECTED_STATUS,
    ITEM_CLASSIFICATION_SCHEME,
    PAGE_SIZE,
    REQUIRED_TENDER_FIELDS,
    LiberiaConnector,
    creation_date,
    parse_release,
    parse_search_response,
    within_window,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "liberia.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "liberia.yaml"

# The live pass this fixture was recorded from, 2026-09-12. cutoff() is relative to
# "today", so every window-cut test pins it to that day rather than the real one.
RECORDED_ON = date(2026, 9, 12)

# What the recorded pass actually held. Asserted rather than described, so a
# re-recorded fixture that quietly holds fewer records fails here and not
# somewhere subtler.
RECORDED_IN_WINDOW = 14

# The placeholders test_the_committed_fixture_carries_no_real_contact_details checks
# every non-empty contactPoint value against. See the module docstring.
REDACTED_EMAIL = "redacted@example.invalid"
REDACTED_PHONE = "000-000-0000"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> LiberiaConnector:
    return LiberiaConnector(source, ["48"])


@pytest.fixture(scope="module")
def rows(fixture) -> list[dict]:
    return parse_search_response(fixture["search_response"])


@pytest.fixture(scope="module")
def cutoff(connector) -> date:
    return connector.cutoff(today=RECORDED_ON)


@pytest.fixture(scope="module")
def wanted(rows, cutoff) -> list[dict]:
    return within_window(rows, cutoff)


@pytest.fixture(scope="module")
def releases(fixture, wanted) -> list[dict]:
    """The 14 in-window rows, each paired with its parsed compiled release."""
    return [
        parse_release(fixture["compiled_releases"][row["id"]], notice_id=row["id"], expected_ocid=row["ocid"])
        for row in wanted
    ]


# --- cpv_prefixes, taken and unused ------------------------------------------


def test_cpv_prefixes_is_accepted_but_unused(connector):
    """Every item here is ISIC-classified; see sources/liberia.yaml and the module
    docstring. Taken so every connector is built the same shape."""
    assert connector.cpv_prefixes == ["48"]


# --- the search page ----------------------------------------------------------


def test_the_recorded_page_is_a_full_page(rows):
    assert len(rows) == PAGE_SIZE


def test_every_listing_row_has_what_the_window_cut_and_the_next_request_need(rows):
    for row in rows:
        assert row["id"]
        assert row["ocid"]
        assert row["status"] == EXPECTED_STATUS
        assert isinstance(row["creationDate"], int)


def test_the_recorded_page_is_sorted_newest_first(rows):
    dates = [creation_date(row) for row in rows]

    assert dates == sorted(dates, reverse=True)


def test_a_renamed_container_raises(fixture):
    broken = {"total": fixture["search_response"]["total"], "results": fixture["search_response"]["items"]}

    with pytest.raises(ValueError, match="items"):
        parse_search_response(broken)


def test_a_row_missing_a_required_field_raises(fixture):
    broken = json.loads(json.dumps(fixture["search_response"]))
    del broken["items"][0]["creationDate"]

    with pytest.raises(ValueError, match="creationDate"):
        parse_search_response(broken)


def test_an_unexpected_status_raises(fixture):
    """GENERATED is the only status measured across the whole 1,385-row corpus;
    see the module docstring for what NEW or UPDATED would mean."""
    broken = json.loads(json.dumps(fixture["search_response"]))
    broken["items"][3]["status"] = "NEW"

    with pytest.raises(ValueError, match="NEW"):
        parse_search_response(broken)


def test_an_unsorted_page_raises(fixture):
    """The measured hazard: a wrong sortField 200s with a genuinely unsorted page."""
    broken = json.loads(json.dumps(fixture["search_response"]))
    broken["items"][0], broken["items"][30] = broken["items"][30], broken["items"][0]

    with pytest.raises(ValueError, match="not sorted"):
        parse_search_response(broken)


# --- the window cut -------------------------------------------------------------


def test_the_window_is_seven_days_before_the_run(connector):
    assert connector.cutoff(today=RECORDED_ON) == date(2026, 9, 5)


def test_the_window_cut_keeps_the_measured_count(wanted, cutoff, source):
    assert len(wanted) == RECORDED_IN_WINDOW
    assert all(creation_date(row) >= cutoff for row in wanted)
    assert source.expected_min <= len(wanted) <= source.expected_max


def test_creation_date_reads_epoch_milliseconds_as_a_utc_date():
    assert creation_date({"id": "x", "creationDate": 1789144149000}) == date(2026, 9, 11)


def test_creation_date_raises_when_absent():
    with pytest.raises(ValueError, match="no creationDate"):
        creation_date({"id": "x", "creationDate": None})


# --- the downloaded release ------------------------------------------------------


def test_every_in_window_record_has_a_compiled_release_in_the_fixture(wanted, fixture):
    assert len(wanted) == RECORDED_IN_WINDOW
    for row in wanted:
        assert row["id"] in fixture["compiled_releases"]


def test_every_recorded_release_has_the_required_tender_fields(releases):
    assert len(releases) == RECORDED_IN_WINDOW
    for release in releases:
        for field in REQUIRED_TENDER_FIELDS:
            assert release["tender"].get(field), f"{release['ocid']} tender is missing {field}"


def test_every_item_is_classified_isic(releases):
    for release in releases:
        for item in release["tender"]["items"]:
            assert item["classification"]["scheme"] == ITEM_CLASSIFICATION_SCHEME


def test_the_release_language_matches_the_registrys_declared_language(releases, source):
    """sources/liberia.yaml declares language: en; the live data has to agree."""
    assert source.language == "en"
    for release in releases:
        assert release["language"] == source.language


def test_a_release_with_no_releases_key_raises():
    with pytest.raises(ValueError, match="no releases"):
        parse_release({"releases": []}, notice_id="x", expected_ocid="ocds-1")


def test_a_release_naming_the_wrong_ocid_raises(fixture, wanted):
    row = wanted[0]
    broken = json.loads(json.dumps(fixture["compiled_releases"][row["id"]]))
    broken["releases"][0]["ocid"] = "ocds-dwjm7l-not-this-one"

    with pytest.raises(ValueError, match="wrong record"):
        parse_release(broken, notice_id=row["id"], expected_ocid=row["ocid"])


def test_a_release_missing_its_tender_block_raises(fixture, wanted):
    row = wanted[0]
    broken = json.loads(json.dumps(fixture["compiled_releases"][row["id"]]))
    del broken["releases"][0]["tender"]

    with pytest.raises(ValueError, match="no tender block"):
        parse_release(broken, notice_id=row["id"], expected_ocid=row["ocid"])


def test_a_release_missing_a_required_tender_field_raises(fixture, wanted):
    row = wanted[0]
    broken = json.loads(json.dumps(fixture["compiled_releases"][row["id"]]))
    del broken["releases"][0]["tender"]["value"]

    with pytest.raises(ValueError, match="value"):
        parse_release(broken, notice_id=row["id"], expected_ocid=row["ocid"])


def test_a_release_with_no_language_raises(fixture, wanted):
    row = wanted[0]
    broken = json.loads(json.dumps(fixture["compiled_releases"][row["id"]]))
    broken["releases"][0]["language"] = ""

    with pytest.raises(ValueError, match="no language"):
        parse_release(broken, notice_id=row["id"], expected_ocid=row["ocid"])


def test_a_non_isic_item_raises(fixture, wanted):
    row = wanted[0]
    broken = json.loads(json.dumps(fixture["compiled_releases"][row["id"]]))
    broken["releases"][0]["tender"]["items"][0]["classification"]["scheme"] = "CPV"

    with pytest.raises(ValueError, match="CPV"):
        parse_release(broken, notice_id=row["id"], expected_ocid=row["ocid"])


# --- personal data, redacted in the fixture ------------------------------------


def test_the_committed_fixture_carries_no_real_contact_details(fixture):
    """Guards the redaction described in this file's own docstring: a re-recorded
    fixture must be redacted again before it is committed, not committed as-is."""
    checked = 0
    for document in fixture["compiled_releases"].values():
        for release in document["releases"]:
            for party in release.get("parties", []):
                contact = party.get("contactPoint")
                if not contact:
                    continue
                for field, placeholder in (
                    ("email", REDACTED_EMAIL),
                    ("telephone", REDACTED_PHONE),
                    ("faxNumber", REDACTED_PHONE),
                ):
                    value = contact.get(field, "")
                    assert value in ("", placeholder), f"{party.get('name')} {field} is not redacted: {value!r}"
                    checked += 1
    assert checked > 0, "no contactPoint was found to check; the fixture may have changed shape"


# --- one whole pass, replayed ---------------------------------------------------


def replaying_client(fixture: dict) -> httpx.Client:
    """A client that serves the recorded documents and refuses anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/record/searchRecords.action"):
            return httpx.Response(200, json=fixture["search_response"])
        if "/record/downloadRecord/" in path:
            record_id = path.rsplit("/", 2)[1]
            return httpx.Response(200, json=fixture["compiled_releases"][record_id])
        raise AssertionError(f"the connector asked for {request.url}, which is not part of the recorded pass")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_yields_the_measured_count(connector, fixture, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 9, 5))

    with replaying_client(fixture) as client:
        raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == RECORDED_IN_WINDOW
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "liberia"
        assert raw.mime == "application/json"
        assert raw.url.startswith(DOWNLOAD_URL.split("{id}")[0])
        seen_urls.add(raw.url)

        payload = json.loads(raw.payload)
        assert sorted(payload) == ["detail", "listing"]
        assert payload["listing"]["ocid"] == payload["detail"]["releases"][0]["ocid"]

    assert len(seen_urls) == RECORDED_IN_WINDOW, "each notice must have its own download URL"


def test_the_ocid_download_mismatch_is_caught_end_to_end(connector, fixture, monkeypatch):
    """fetch_release's cross-check, exercised through fetch_raw rather than in
    isolation: a download endpoint answering for the wrong record must stop the
    whole run rather than silently mislabel one notice."""
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 9, 5))
    tampered = json.loads(json.dumps(fixture))
    any_id = next(iter(tampered["compiled_releases"]))
    tampered["compiled_releases"][any_id]["releases"][0]["ocid"] = "ocds-dwjm7l-not-this-one"

    with replaying_client(tampered) as client, pytest.raises(ValueError, match="wrong record"):
        connector.fetch_raw(client)
