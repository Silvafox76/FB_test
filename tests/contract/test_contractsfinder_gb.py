"""Contracts Finder's contract, against the package recorded on 2026-09-19.

No `monitor.normalise.contractsfinder_gb` exists yet - the normaliser is out of
this agent's lane (it is main-session work, once the source is wired in) - so
every assertion below reads the raw OCDS release the way a future mapper would,
at the field paths named in `monitor/connectors/contractsfinder_gb.py`'s
docstring and in the report back to the calling agent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.contractsfinder_gb import (
    ContractsfinderGbConnector,
    parse_releases,
    release_url,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "contractsfinder_gb.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "contractsfinder_gb.yaml"

# Named individuals whose contact details were redacted from the fixture before
# commit (rule 19; see fixtures/contractsfinder_gb.notes.txt). These two named a
# role or a team rather than a person and were left as published.
ROLE_CONTACT_NAMES = ("Procurement Team", "ICT Category Manager")


@pytest.fixture(scope="module")
def package() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def releases(package) -> list[dict]:
    return parse_releases(package)


def test_the_package_yields_within_the_registrys_expected_range(releases, source):
    assert source.expected_min <= len(releases) <= source.expected_max


def test_every_release_has_a_unique_id_and_ocid(releases):
    ids = [release["id"] for release in releases]
    ocids = [release["ocid"] for release in releases]

    assert len(ids) == len(set(ids))
    assert len(ocids) == len(set(ocids))


def test_every_release_has_the_fields_the_mapper_reads(releases):
    for release in releases:
        for field in ("ocid", "id", "date", "tender"):
            assert field in release
        assert release["tender"]["title"]


def test_a_renamed_container_raises(package):
    with pytest.raises(ValueError, match="no 'releases' key"):
        parse_releases({"results": package["releases"]})


def test_a_renamed_release_field_raises(package):
    broken = json.loads(json.dumps(package))
    for release in broken["releases"]:
        release["identifier"] = release.pop("id")

    with pytest.raises(ValueError, match="id"):
        parse_releases(broken)


def test_a_renamed_tender_field_raises(package):
    """Zero releases would read as a quiet day rather than a changed API."""
    broken = json.loads(json.dumps(package))
    for release in broken["releases"]:
        release["tender"]["name"] = release["tender"].pop("title")

    with pytest.raises(ValueError, match="title"):
        parse_releases(broken)


def test_every_release_is_tender_stage(releases):
    """`stages=tender` was asked for at the query.

    Three of the fourteen recorded releases carry `tag: ["tenderAmendment"]`
    rather than `["tender"]` - still pre-award, so still the tender stage.
    """
    tender_stage_tags = {"tender", "tenderUpdate", "tenderAmendment", "tenderCancellation"}
    for release in releases:
        assert set(release.get("tag") or []) & tender_stage_tags


def test_some_releases_are_amendments_and_that_is_not_a_layout_change(releases):
    amendments = [r for r in releases if r.get("tag") == ["tenderAmendment"]]
    assert amendments, "expected the recorded package to carry the tenderAmendment tag observed live"


def test_an_award_stage_tag_raises(package):
    """A release outside the tender stage means the query, not just the day, changed."""
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["tag"] = ["award"]

    with pytest.raises(ValueError, match="tender-stage tag"):
        parse_releases(broken)


def test_every_release_carries_a_title_and_a_buyer_name(releases):
    for release in releases:
        assert release["tender"]["title"].strip()
        assert (release.get("buyer") or {}).get("name", "").strip()


def test_every_release_carries_a_deadline_that_parses(releases):
    """`tenderPeriod.endDate` was present on all 14 recorded releases."""
    with_deadline = [r for r in releases if (r["tender"].get("tenderPeriod") or {}).get("endDate")]
    assert with_deadline == releases

    for release in with_deadline:
        raw = release["tender"]["tenderPeriod"]["endDate"]
        parsed = datetime.fromisoformat(raw)
        assert parsed.tzinfo is not None


def test_cpv_lives_at_tender_classification_and_parses(releases):
    """Unlike Find a Tender, CPV sits at `tender.classification` on this
    publisher, present on every one of the 14 recorded releases - confirmed
    live, not assumed from Find a Tender's shape.
    """
    with_cpv = [r for r in releases if (r["tender"].get("classification") or {}).get("scheme") == "CPV"]
    assert with_cpv == releases

    for release in with_cpv:
        code = release["tender"]["classification"]["id"]
        assert len(code) == 8 and code.isdigit()


def test_a_value_parses_where_stated_and_some_releases_state_none(releases):
    stated = [r for r in releases if r["tender"].get("value")]
    unstated = [r for r in releases if not r["tender"].get("value")]
    assert stated, "expected at least one valued release in the recorded package"
    assert unstated, "expected at least one release stating no value in the recorded package"

    for release in stated:
        value = release["tender"]["value"]
        assert value["currency"] == "GBP"
        assert Decimal(str(value["amount"])) >= 0


def test_the_url_comes_from_the_releases_own_html_document(releases):
    for release in releases:
        html_docs = [
            doc["url"]
            for doc in release["tender"].get("documents") or []
            if doc.get("documentType") == "tenderNotice" and doc.get("format") == "text/html"
        ]
        assert release_url(release) in html_docs
        assert release_url(release).startswith("https://www.contractsfinder.service.gov.uk/Notice/")


def test_a_release_with_no_html_document_raises(package):
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["tender"]["documents"] = []

    with pytest.raises(ValueError, match="no tenderNotice html document url"):
        release_url(broken["releases"][0])


def test_named_individual_contact_details_were_stripped_from_the_fixture(releases):
    """Rule 19: no reviewer or staff personal contact detail sits in a fixture.

    Twelve of the fourteen recorded releases named an individual buyer contact
    (see fixtures/contractsfinder_gb.notes.txt); this checks the redaction held
    rather than trusting the note.
    """
    checked_a_named_individual = False
    for release in releases:
        for party in release.get("parties") or []:
            contact = party.get("contactPoint") or {}
            if not contact or contact.get("name") in ROLE_CONTACT_NAMES:
                continue
            checked_a_named_individual = True
            assert contact["name"] == "REDACTED (named individual, rule 19)"
            assert contact.get("email", "redacted@redacted.invalid") == "redacted@redacted.invalid"
            assert contact.get("telephone", "REDACTED") == "REDACTED"
    assert checked_a_named_individual, "expected the recorded package to carry a redacted individual contact"


def test_a_non_200_response_raises(source):
    """No try/except in the connector: an HTTP error propagates from raise_for_status."""
    connector = ContractsfinderGbConnector(source, [])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "service unavailable"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        connector.fetch_raw(client)


def test_an_explicit_published_from_is_a_date_the_api_accepts(source):
    """Verified against the live API: seconds precision, no timezone suffix.

    `since`/`until` on `fetch_raw` are the window edges themselves, not a "now"
    the method subtracts a lookback from - so an explicit `since` is formatted
    as given.
    """
    connector = ContractsfinderGbConnector(source, [])
    window = connector.published_from(datetime(2026, 9, 17, 10, 30, tzinfo=UTC))

    assert window == "2026-09-17T10:30:00"


def test_an_explicit_published_to_is_a_date_the_api_accepts(source):
    connector = ContractsfinderGbConnector(source, [])
    window = connector.published_to(datetime(2026, 9, 19, 10, 30, tzinfo=UTC))

    assert window == "2026-09-19T10:30:00"


def test_the_default_window_is_the_trailing_two_days(source):
    """No `since`/`until`: the default the registry describes, same as TED and FTS."""
    connector = ContractsfinderGbConnector(source, [])
    before = datetime.now(UTC)

    since = datetime.fromisoformat(connector.published_from()).replace(tzinfo=UTC)
    until = datetime.fromisoformat(connector.published_to()).replace(tzinfo=UTC)

    after = datetime.now(UTC)

    # Formatted at seconds precision, so allow the truncated fraction of a second.
    slack = timedelta(seconds=1)
    assert before - timedelta(days=2) - slack <= since <= after - timedelta(days=2)
    assert before - slack <= until <= after
