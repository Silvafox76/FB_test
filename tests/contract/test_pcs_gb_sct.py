"""Public Contracts Scotland's contract, against the September 2026 package
recorded live on 2026-09-19 (`GET /v1/Notices?dateFrom=09-2026&noticeType=102
&outputType=0`, 62 releases).

No `monitor.normalise.pcs_gb_sct` exists yet - the normaliser is out of this
agent's lane - so this file checks the raw OCDS release at the field paths a
future mapper would read, and locks the two properties the registry entry
depends on: the connector reads noticeType=102 and only that population, and
it never asks anything of www.publiccontractsscotland.gov.uk, the
robots-disallowed host that carries every document link this payload names.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.pcs_gb_sct import (
    EXPECTED_STATUS,
    EXPECTED_TAG,
    NOTICE_TYPE,
    OUTPUT_TYPE,
    WWW_HOST,
    PcsGbSctConnector,
    months_covering,
    parse_releases,
    release_date,
    release_url,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "pcs_gb_sct.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "pcs_gb_sct.yaml"

# The two days before the fixture was recorded (2026-09-19): 3 on the 17th, 8 on
# the 18th, 0 so far on the 19th itself. Asserted rather than described so a
# re-recorded fixture that quietly changes shape fails here, not somewhere subtler.
RECORDED_ON = date(2026, 9, 19)
IN_TRAILING_TWO_DAYS = 11


@pytest.fixture(scope="module")
def package() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> PcsGbSctConnector:
    return PcsGbSctConnector(source, [])


@pytest.fixture(scope="module")
def releases(package) -> list[dict]:
    return parse_releases(package)


# --- the recorded package -----------------------------------------------------


def test_the_package_yields_within_the_registrys_expected_range(releases, source):
    assert source.expected_min <= len(releases) <= source.expected_max


def test_every_release_has_a_unique_id_and_ocid(releases):
    ids = [release["id"] for release in releases]
    ocids = [release["ocid"] for release in releases]

    assert len(ids) == len(set(ids))
    assert len(ocids) == len(set(ocids))


def test_every_release_has_the_fields_the_mapper_reads(releases):
    for release in releases:
        for field in ("ocid", "id", "date", "tag", "buyer", "parties", "tender", "language", "links"):
            assert field in release, f"{release.get('id')} has no {field}"
        assert release["tender"]["title"]
        assert release["tender"]["status"]


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


# --- noticeType=102 is one population: tender stage, active ------------------


def test_every_release_is_tender_stage_and_active(releases):
    """noticeType=102 ("Site CN") is the below-threshold, open, tender-stage
    population; sources/pcs_gb_sct.yaml records every one of the 62 sampled
    releases carrying exactly this tag and status."""
    for release in releases:
        assert release["tag"] == EXPECTED_TAG
        assert release["tender"]["status"] == EXPECTED_STATUS


def test_a_non_tender_tag_raises(package):
    """A drift away from tender-stage-only means noticeType=102 changed meaning,
    not that the day was quiet."""
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["tag"] = ["award"]

    with pytest.raises(ValueError, match="carries tag"):
        parse_releases(broken)


def test_a_non_active_status_raises(package):
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["tender"]["status"] = "complete"

    with pytest.raises(ValueError, match="tender.status"):
        parse_releases(broken)


# --- fields a future normaliser needs -----------------------------------------


def test_every_release_carries_a_title_and_a_buyer_name(releases):
    for release in releases:
        assert release["tender"]["title"].strip()
        assert (release.get("buyer") or {}).get("name", "").strip()


def test_every_release_carries_a_deadline_that_parses(releases):
    """`tenderPeriod.endDate` was present on all 62 recorded releases."""
    with_deadline = [r for r in releases if (r["tender"].get("tenderPeriod") or {}).get("endDate")]
    assert with_deadline == releases

    from datetime import datetime as dt

    for release in with_deadline:
        parsed = dt.fromisoformat(release["tender"]["tenderPeriod"]["endDate"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


def test_cpv_lives_in_additional_classifications_and_some_releases_carry_none(releases):
    """Like Find a Tender, not like Contracts Finder: CPV sits in
    `items[].additionalClassifications`, and 20 of 62 recorded releases carry
    none at all - not a failed match, the free filter's lexicon stage handles it."""

    def codes(release: dict) -> list[str]:
        found = []
        for item in release["tender"].get("items") or []:
            for extra in item.get("additionalClassifications") or []:
                if extra.get("scheme") == "CPV":
                    found.append(extra["id"])
        return found

    with_codes = [r for r in releases if codes(r)]
    without_codes = [r for r in releases if not codes(r)]
    assert with_codes and without_codes

    for release in with_codes:
        for code in codes(release):
            assert len(code) == 8 and code.isdigit()


def test_a_value_parses_where_stated_and_most_releases_state_none(releases):
    """28 of 62 recorded releases state a value; all of them in GBP."""
    stated = [r for r in releases if (r["tender"].get("value") or {}).get("amount")]
    unstated = [r for r in releases if not (r["tender"].get("value") or {}).get("amount")]
    assert stated, "expected at least one valued release in the recorded package"
    assert unstated, "expected at least one release stating no value in the recorded package"

    for release in stated:
        value = release["tender"]["value"]
        assert value["currency"] == "GBP"
        assert Decimal(str(value["amount"])) > 0


# --- the one URL this connector will ever record, and the host it must not touch ---


def test_the_url_is_the_ocds_packages_own_canonical_link_on_the_api_host(releases):
    for release in releases:
        url = release_url(release)
        assert url.startswith("https://api.publiccontractsscotland.gov.uk/")
        assert WWW_HOST not in url


def test_a_canonical_link_naming_the_www_host_raises_rather_than_being_used(package):
    """The one thing this connector must never do: build a request, or even
    record a URL, on the robots-disallowed www host."""
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["links"] = [
        {"rel": "canonical", "href": f"https://{WWW_HOST}/search/show/search_view.aspx?ID=1"}
    ]

    with pytest.raises(ValueError, match="disallowed www host"):
        release_url(broken["releases"][0])


def test_a_release_with_no_canonical_link_raises(package):
    broken = json.loads(json.dumps(package))
    broken["releases"][0]["links"] = [{"rel": "self", "href": "https://api.publiccontractsscotland.gov.uk/v1/x"}]

    with pytest.raises(ValueError, match="no canonical link"):
        release_url(broken["releases"][0])


def test_every_document_url_named_in_the_payload_is_on_the_disallowed_www_host(releases):
    """Confirms the hazard `release_url` and the connector's docstring describe:
    every document link this payload carries points at the one host this
    connector must never fetch, which is exactly why it reads none of them."""
    document_urls = [
        doc["url"] for release in releases for doc in (release["tender"].get("documents") or []) if doc.get("url")
    ]
    assert document_urls, "expected at least one document url in the recorded package"
    assert all(WWW_HOST in url for url in document_urls)


# --- personal data, redacted in the fixture -----------------------------------


def test_named_individual_contact_details_were_stripped_from_the_fixture(releases):
    """Rule 19: no staff or buyer-contact personal detail sits in a committed fixture.

    37 of the 62 recorded releases carried a `contactPoint.name` naming an
    individual (see monitor/connectors/pcs_gb_sct.py's module docstring); this
    checks the redaction held rather than trusting the docstring.
    """
    checked_a_named_individual = False
    for release in releases:
        for party in release.get("parties") or []:
            contact = party.get("contactPoint") or {}
            if not contact.get("name"):
                continue
            checked_a_named_individual = True
            assert contact["name"] == "REDACTED (named individual, rule 19)"
            assert contact.get("email", "redacted@redacted.invalid") == "redacted@redacted.invalid"
            assert contact.get("telephone", "REDACTED") == "REDACTED"
    assert checked_a_named_individual, "expected the recorded package to carry a redacted individual contact"


# --- months_covering ------------------------------------------------------------


def test_months_covering_a_window_within_one_month():
    assert months_covering(date(2026, 9, 1), date(2026, 9, 19)) == [date(2026, 9, 1)]


def test_months_covering_a_window_spanning_two_months():
    assert months_covering(date(2026, 8, 30), date(2026, 9, 2)) == [date(2026, 8, 1), date(2026, 9, 1)]


def test_months_covering_a_window_spanning_a_year_boundary():
    assert months_covering(date(2026, 12, 15), date(2027, 1, 5)) == [date(2026, 12, 1), date(2027, 1, 1)]


# --- the default window, and an explicit one --------------------------------


def test_the_default_window_is_the_trailing_two_days(connector):
    assert connector.window(today=RECORDED_ON) == (date(2026, 9, 17), date(2026, 9, 19))


def test_since_after_until_raises(connector):
    with pytest.raises(ValueError, match="is after"):
        connector.window(since=date(2026, 9, 19), until=date(2026, 9, 17))


def test_release_date_reads_the_ocds_date_as_a_date():
    assert release_date({"date": "2026-09-18T00:00:00Z"}) == date(2026, 9, 18)


# --- one whole pass, replayed ---------------------------------------------------


def replaying_client(package: dict) -> httpx.Client:
    """A client that serves only the recorded September 2026 call and refuses
    anything else - in particular anything to the www host, which is the
    property this whole source entry exists to protect."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == WWW_HOST:
            raise AssertionError(f"the connector asked the disallowed www host for {request.url}")
        if request.url.host != "api.publiccontractsscotland.gov.uk":
            raise AssertionError(f"the connector asked an unexpected host: {request.url}")

        params = dict(request.url.params)
        if params.get("dateFrom") == "09-2026":
            assert params.get("noticeType") == NOTICE_TYPE
            assert params.get("outputType") == OUTPUT_TYPE
            return httpx.Response(200, json=package)
        # A month with nothing recorded: a genuinely empty OCDS package, not a
        # missing 'releases' key, so the parser's own contract still holds.
        return httpx.Response(200, json={"releases": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_keeps_only_releases_inside_the_window(connector, package):
    with replaying_client(package) as client:
        raw_notices = connector.fetch_raw(client, since=date(2026, 9, 17), until=date(2026, 9, 19))

    assert len(raw_notices) == IN_TRAILING_TWO_DAYS
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "pcs_gb_sct"
        assert raw.mime == "application/json"
        assert raw.url.startswith("https://api.publiccontractsscotland.gov.uk/")
        assert WWW_HOST not in raw.url
        seen_urls.add(raw.url)

        payload = json.loads(raw.payload)
        assert date(2026, 9, 17) <= release_date(payload) <= date(2026, 9, 19)

    assert len(seen_urls) == IN_TRAILING_TWO_DAYS, "each notice must have its own url"


def test_a_wider_window_yields_the_whole_recorded_month(connector, package, releases):
    with replaying_client(package) as client:
        raw_notices = connector.fetch_raw(client, since=date(2026, 9, 1), until=date(2026, 9, 30))

    assert len(raw_notices) == len(releases)


def test_a_window_spanning_two_months_calls_the_api_once_per_month(connector, package):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.publiccontractsscotland.gov.uk"
        params = dict(request.url.params)
        calls.append(params["dateFrom"])
        if params["dateFrom"] == "09-2026":
            return httpx.Response(200, json=package)
        return httpx.Response(200, json={"releases": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        raw_notices = connector.fetch_raw(client, since=date(2026, 8, 30), until=date(2026, 9, 2))

    assert calls == ["08-2026", "09-2026"]
    # Only the September releases from the 1st (3) and 2nd (6) fall inside the window.
    assert len(raw_notices) == 9
    for raw in raw_notices:
        payload = json.loads(raw.payload)
        assert date(2026, 8, 30) <= release_date(payload) <= date(2026, 9, 2)


def test_a_non_200_response_raises(connector, package):
    """No try/except in the connector: an HTTP error propagates from raise_for_status."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        connector.fetch_raw(client, since=date(2026, 9, 17), until=date(2026, 9, 19))


def test_no_request_is_ever_made_to_the_www_host(connector, package):
    """The property the registry entry is built around, asserted directly on
    the mock transport rather than only inferred from the URLs returned."""
    requested_hosts = set()

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.add(request.url.host)
        if request.url.host == WWW_HOST:
            raise AssertionError("the connector must never request the www host")
        is_september = dict(request.url.params).get("dateFrom") == "09-2026"
        return httpx.Response(200, json=package if is_september else {"releases": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector.fetch_raw(client, since=date(2026, 9, 1), until=date(2026, 9, 19))

    assert requested_hosts == {"api.publiccontractsscotland.gov.uk"}
