"""Find a Tender's contract, against the package recorded on 2026-09-12."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from monitor.connectors.fts import FtsConnector, parse_releases, release_url
from monitor.models import Source
from monitor.normalise.fts import cpv_codes, map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "fts.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "fts.yaml"


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


def test_every_release_maps_to_a_title_url_country_and_external_id(releases):
    for raw in releases:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.url.startswith("https://www.find-tender.service.gov.uk/Notice/")
        assert mapped.country == "GB"
        assert mapped.external_id.strip()


def test_the_release_is_english_and_carries_no_separate_rendering(releases):
    """Filling title_en with a copy would say a translation happened when none did."""
    for raw in releases:
        mapped = map_notice(raw)

        assert mapped.notice.language == "en"
        assert mapped.title_en == ""


def test_cpv_comes_from_additional_classifications(releases):
    """On this publisher items[].classification is the buyer's own scheme, not CPV."""
    with_codes = [raw for raw in releases if cpv_codes(raw["tender"])]

    assert with_codes, "the recorded package should carry CPV codes"
    for raw in with_codes:
        for code in cpv_codes(raw["tender"]):
            assert len(code) == 8 and code.isdigit()


def test_some_releases_carry_no_cpv_at_all(releases):
    """Four of the 25 recorded. They go on to the lexicon stage, not to a drop."""
    without = [raw for raw in releases if not cpv_codes(raw["tender"])]

    assert without, "expected releases with no CPV in the recorded package"
    for raw in without:
        assert map_notice(raw).notice.cpv_codes == []


def test_deadlines_parse_when_the_tender_period_states_one(releases):
    with_deadline = [raw for raw in releases if (raw["tender"].get("tenderPeriod") or {}).get("endDate")]

    assert with_deadline
    for raw in with_deadline:
        assert map_notice(raw).notice.deadline_at is not None


def test_a_sterling_value_is_carried_as_sterling(releases):
    """Find a Tender states GBP, and GBP is what is stored.

    This used to assert the value was dropped, because decision 7 had no rate to
    convert it with. Migration 012 gives the pipeline a stamped rate, so the
    published figure is kept in the published currency and the conversion is a
    separate, recorded step at staging.
    """
    sterling = [raw for raw in releases if (raw["tender"].get("value") or {}).get("currency") == "GBP"]
    assert sterling, "expected GBP-denominated releases in the fixture"

    for raw in sterling:
        notice = map_notice(raw).notice
        stated = raw["tender"]["value"]["amount"]
        assert notice.value_currency == "GBP"
        # A stated zero is the publisher's "not stated" and is dropped, everywhere.
        assert notice.estimated_value == (Decimal(str(stated)).quantize(Decimal("0.01")) if stated > 0 else None)


def test_the_buyer_name_is_carried(releases):
    named = [raw for raw in releases if (raw.get("buyer") or {}).get("name")]

    assert named
    for raw in named:
        assert map_notice(raw).notice.buyer == raw["buyer"]["name"]


def test_the_url_is_built_from_the_notice_id(releases):
    for raw in releases:
        assert release_url(raw).endswith(raw["id"])


def test_the_window_is_a_date_the_api_accepts(source):
    """Verified against the live API: seconds precision, no timezone suffix."""
    connector = FtsConnector(source, ["48"])
    window = connector.updated_from(now=datetime(2026, 9, 12, 10, 30, tzinfo=UTC))

    assert window == "2026-09-10T10:30:00"
