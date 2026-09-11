"""TED's contract, against the response recorded on 2026-09-11.

A contract test answers one question: has the source changed under us? It runs
against the committed fixture, never the network, so it is deterministic and it
fails the moment TED renames a field rather than the morning nobody notices the
queue is empty.

The property that matters most is the last one: a renamed field must raise. A
parser that returns zero notices on a changed API is indistinguishable from a
quiet day, and quiet days are exactly when nobody looks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from monitor.connectors.ted import REQUIRED_FIELDS, TedConnector, notice_url, parse_notices
from monitor.models import Source
from monitor.normalise.ted import map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "ted.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "ted.yaml"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def notices(document) -> list[dict]:
    return parse_notices(document)


def test_the_fixture_yields_within_the_registrys_expected_range(notices, source):
    """The registry says what a healthy run looks like; the fixture has to agree."""
    assert source.expected_min <= len(notices) <= source.expected_max


def test_every_notice_has_the_fields_the_pipeline_reads(notices):
    for notice in notices:
        for field in REQUIRED_FIELDS:
            assert field in notice, f"{notice.get('publication-number')} has no {field}"


def test_every_notice_maps_to_a_title_url_country_and_external_id(notices):
    for raw in notices:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.url.startswith("https://ted.europa.eu/")
        assert len(mapped.country) == 2
        assert mapped.external_id.strip()


def test_a_renamed_field_raises_rather_than_yielding_zero(document):
    """The whole point of a contract test. Zero notices reads as a quiet day."""
    broken = json.loads(json.dumps(document))
    for notice in broken["notices"]:
        notice["noticeTitle"] = notice.pop("notice-title")

    with pytest.raises(ValueError, match="notice-title"):
        parse_notices(broken)


def test_a_renamed_container_raises(document):
    broken = {"results": document["notices"]}

    with pytest.raises(ValueError, match="no 'notices' key"):
        parse_notices(broken)


def test_the_original_language_is_what_is_stored(notices):
    """Rule 9: TED translates into 24 languages and we store the published one."""
    spanish = [n for n in notices if n["official-language"] == ["SPA"]]
    assert spanish, "expected at least one Spanish notice in the fixture"

    mapped = map_notice(spanish[0])

    assert mapped.notice.language == "es"
    assert mapped.notice.title == spanish[0]["notice-title"]["spa"]
    assert mapped.title_en == spanish[0]["notice-title"]["eng"]
    assert mapped.notice.title != mapped.title_en


def test_three_letter_codes_are_converted(notices):
    """ESP -> ES and SPA -> es, or the geography weights silently miss."""
    countries = {map_notice(raw).notice.country for raw in notices}
    languages = {map_notice(raw).notice.language for raw in notices}

    assert all(len(code) == 2 and code.isupper() for code in countries)
    assert all(len(code) == 2 and code.islower() for code in languages)


def test_cpv_codes_are_extracted_and_deduplicated(notices):
    """The fixture's first notice carries the same code twice."""
    for raw in notices:
        codes = map_notice(raw).notice.cpv_codes
        assert codes, f"{raw['publication-number']} has no CPV code"
        assert len(codes) == len(set(codes))
        assert all(len(code) == 8 and code.isdigit() for code in codes)


def test_deadlines_parse_when_present_and_are_none_when_not(notices):
    """Only some notices carry a tender deadline; absence is normal, not a failure."""
    with_deadline = [n for n in notices if n.get("deadline-receipt-tender-date-lot")]
    without = [n for n in notices if not n.get("deadline-receipt-tender-date-lot")]

    assert with_deadline and without, "fixture should exercise both paths"
    assert all(map_notice(raw).notice.deadline_at is not None for raw in with_deadline)
    assert all(map_notice(raw).notice.deadline_at is None for raw in without)


def test_the_deadline_is_the_earliest_lot():
    """A reviewer needs the soonest date they could miss, not the last."""
    raw = {
        "publication-number": "1-2026",
        "notice-title": {"eng": "A notice"},
        "description-proc": {"eng": "A description"},
        "buyer-name": {"eng": ["A buyer"]},
        "buyer-country": ["DEU"],
        "classification-cpv": ["48000000"],
        "publication-date": "2026-09-09+02:00",
        "deadline-receipt-tender-date-lot": ["2026-10-31+02:00", "2026-09-30+02:00"],
        "official-language": ["ENG"],
        "notice-type": "cn-standard",
        "links": {"html": {"ENG": "https://ted.europa.eu/en/notice/-/detail/1-2026"}},
    }

    deadline = map_notice(raw).notice.deadline_at

    assert deadline is not None
    assert (deadline.month, deadline.day) == (9, 30)


def test_a_non_usd_value_is_not_converted(notices):
    """An invented exchange rate would reach the reviewer looking researched."""
    euro_valued = [n for n in notices if n.get("estimated-value-cur-proc") == "EUR"]
    assert euro_valued, "expected EUR-denominated notices in the fixture"

    assert all(map_notice(raw).notice.estimated_value_usd is None for raw in euro_valued)


def test_award_notices_are_carried_through_not_dropped(notices):
    """Rule 5: dropping already-awarded tenders is the filter's job at step 5."""
    types = {raw["notice-type"] for raw in notices}

    assert "can-standard" in types, "fixture should contain award notices"
    assert all(map_notice(raw).notice.status == "detected" for raw in notices)


def test_the_url_is_the_english_permalink(notices):
    for raw in notices:
        assert notice_url(raw) == raw["links"]["html"]["ENG"]


def test_the_query_asks_for_the_configured_prefixes_only(source):
    """Rule 6: the CPV prefixes come from config, not from this module."""
    from datetime import date

    connector = TedConnector(source, ["48", "72"])
    query = connector.query(today=date(2026, 9, 11))

    assert "classification-cpv=48*" in query
    assert "classification-cpv=72*" in query
    assert "79" not in query
    assert "publication-date>=20260909" in query
