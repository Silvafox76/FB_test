"""Prozorro's contract, against the responses recorded on 2026-09-12.

The fixture holds both halves of what this connector does, because the connector
does two things: one listing page of 100 rows, and the detail records it fetched
for the open ones. A contract test that only replayed the listing would prove
nothing about the parser, since the listing carries no title.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from monitor.connectors.prozorro import OPEN_STATUSES, ProzorroConnector, oldest_modified, parse_listing
from monitor.models import Source
from monitor.normalise.prozorro import cpv_from_dk021, map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "prozorro.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "prozorro.yaml"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def details(fixture) -> list[dict]:
    return [document["data"] for document in fixture["details"].values()]


def test_the_listing_parses_and_carries_what_the_walk_reads(fixture):
    rows = parse_listing(fixture["listing"])

    assert len(rows) == 100
    for row in rows:
        assert row["id"] and row["dateModified"]


def test_a_renamed_listing_container_raises(fixture):
    """Zero rows would read as a quiet day on a feed that never has one."""
    broken = {"results": fixture["listing"]["data"]}

    with pytest.raises(ValueError, match="no 'data' key"):
        parse_listing(broken)


def test_a_renamed_listing_field_raises(fixture):
    broken = json.loads(json.dumps(fixture["listing"]))
    for row in broken["data"]:
        row["modified"] = row.pop("dateModified")

    with pytest.raises(ValueError, match="dateModified"):
        parse_listing(broken)


def test_most_of_the_feed_is_not_an_opportunity(fixture):
    """The reason the listing is filtered before any detail is fetched."""
    rows = parse_listing(fixture["listing"])
    open_rows = [row for row in rows if row.get("status") in OPEN_STATUSES]

    assert 0 < len(open_rows) < len(rows) / 2, "if most of the feed were open, the status filter would be pointless"


def test_the_feed_is_newest_first_but_not_strictly_ordered(fixture):
    """descending=1 turns a feed that otherwise starts in 2015.

    It is descending in aggregate and not strictly monotonic: in the recorded page
    two rows a few microseconds apart come back the wrong way round. That is why
    the walk takes the minimum of a page rather than reading the last row as the
    oldest, and why this asserts the shape rather than a strict sort.
    """
    rows = parse_listing(fixture["listing"])
    stamps = [datetime.fromisoformat(row["dateModified"]) for row in rows]

    assert stamps[0] > stamps[-1], "the page is not descending at all"
    assert oldest_modified(rows) == min(stamps)
    assert oldest_modified(rows) > datetime.now(UTC) - timedelta(days=365)


def test_the_walk_does_not_assume_the_last_row_is_the_oldest(fixture):
    """A strict-order assumption would stop the walk early on this feed."""
    rows = parse_listing(fixture["listing"])
    last = datetime.fromisoformat(rows[-1]["dateModified"])

    assert oldest_modified(rows) <= last


def test_every_detail_maps_to_a_title_url_country_and_external_id(details):
    for raw in details:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.url.startswith("https://prozorro.gov.ua/tender/")
        assert mapped.country == "UA"
        assert mapped.external_id.strip()


def test_titles_stay_in_ukrainian(details):
    """Rule 9. They stop at 'needs translation' unless a CPV prefix passes."""
    for raw in details:
        mapped = map_notice(raw)

        assert mapped.notice.language == "uk"
        assert mapped.title_en == "", "Prozorro supplies no English; empty is not 'it was English'"


def test_a_detail_with_no_title_raises(details):
    broken = json.loads(json.dumps(details[0]))
    broken["title"] = "   "

    with pytest.raises(ValueError, match="no title"):
        map_notice(broken)


def test_dk021_codes_are_carried_across(details):
    """DK021 is CPV-shaped and shares CPV's top-level divisions."""
    with_codes = [raw for raw in details if cpv_from_dk021(raw)]
    assert with_codes, "the recorded details should carry classifications"

    for raw in with_codes:
        for code in cpv_from_dk021(raw):
            assert len(code) == 8 and code.isdigit()


def test_the_check_digit_is_dropped_from_a_dk021_code():
    """'34350000-5' is how Prozorro writes it; the filter tests a prefix."""
    raw = {"items": [{"classification": {"scheme": "ДК021", "id": "34350000-5"}}]}

    assert cpv_from_dk021(raw) == ["34350000"]


def test_a_classification_in_another_scheme_is_ignored():
    raw = {"items": [{"classification": {"scheme": "UNSPSC", "id": "43230000"}}]}

    assert cpv_from_dk021(raw) == []


def test_deadlines_come_from_the_tender_period(details):
    with_deadline = [raw for raw in details if (raw.get("tenderPeriod") or {}).get("endDate")]
    assert with_deadline, "the recorded details should carry a tender period"

    for raw in with_deadline:
        assert map_notice(raw).notice.deadline_at is not None


def test_a_hryvnia_value_is_carried_as_hryvnia(details):
    """Prozorro states UAH on essentially everything, and UAH is what is stored.

    UAH is the reason the rate publisher is the National Bank of Ukraine rather
    than the ECB: the ECB does not publish it, and it is the largest single block
    of stated values in the corpus. Carried here as published; converted at
    staging at a rate that travels with the figure.
    """
    hryvnia = [raw for raw in details if (raw.get("value") or {}).get("currency") == "UAH"]
    assert hryvnia, "expected UAH-denominated tenders in the fixture"

    for raw in hryvnia:
        notice = map_notice(raw).notice
        stated = raw["value"]["amount"]
        assert notice.value_currency == "UAH"
        assert notice.estimated_value == (Decimal(str(stated)).quantize(Decimal("0.01")) if stated > 0 else None)


def test_the_top_level_value_is_the_total_and_not_one_lot(details):
    """Reading a lot value would understate a multi-lot opportunity.

    Measured across the 743 stored notices: 531 carry lot-level values as well, and
    on every one of those the lot amounts sum to exactly the top-level figure. So
    the top level is the total, and that is what the normaliser reads.
    """
    with_lots = [
        raw
        for raw in details
        if (raw.get("value") or {}).get("amount") is not None
        and [lot for lot in (raw.get("lots") or []) if (lot.get("value") or {}).get("amount") is not None]
    ]
    assert with_lots, "expected multi-lot tenders in the fixture"

    for raw in with_lots:
        amounts = [
            Decimal(str(lot["value"]["amount"]))
            for lot in raw["lots"]
            if (lot.get("value") or {}).get("amount") is not None
        ]
        lots = sum(amounts)
        assert lots == Decimal(str(raw["value"]["amount"]))


def test_the_connector_bounds_its_detail_requests(source):
    """Prozorro is the whole of Ukrainian public procurement; a quiet assumption
    about volume is how a polite pass becomes an impolite one."""
    from monitor.connectors.prozorro import MAX_DETAIL_REQUESTS, MAX_LIST_PAGES

    connector = ProzorroConnector(source, ["48", "72", "79"])

    assert MAX_DETAIL_REQUESTS > 0
    assert MAX_LIST_PAGES > 0
    assert connector.source.expected_max > 0
