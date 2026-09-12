"""The OPEC Fund current-opportunities board, against the page recorded on
2026-09-12.

The whole source is one server-rendered `<table>`, so most of what can go
wrong here is the table changing shape under an unattended run: a renamed
class, a reordered header, a shifted column, or a table found empty. Rule 4
says a selector matching nothing must raise rather than report a healthy
zero-item run, and most of this file checks exactly that - against a
deliberately altered copy of the real fixture, never against the fixture
itself edited to make a test pass.

The rest of this file checks the three traps `monitor/connectors/opec_fund.py`
and `sources/opec_fund.yaml` both document: the shallow framework page that
holds no notices, the sibling self-procurement paths that must never be read,
and the two non-biddable notice types the registry excludes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from selectolax.parser import HTMLParser

from monitor.connectors.opec_fund import (
    CELLS_PER_ROW,
    RESULTS_TABLE,
    OpecFundConnector,
    cell_text,
    documents,
    in_scope,
    notice_title,
    parse_opportunities,
)
from monitor.models import RawNotice, Source

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "opec_fund.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "opec_fund.yaml"

# What the page held on the day it was recorded. Asserted rather than computed
# so a re-recorded fixture that lost rows fails here and not somewhere subtler.
ROW_COUNT = 48
EXCLUDED_TYPES = ["General Procurement Notice (GPN)", "Contract Award Notice (CAN)"]
KEPT_COUNT = 38  # ROW_COUNT minus 9 General Procurement Notices and 1 Contract Award Notice


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def listing_html(document) -> str:
    return document["listing_html"]


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def rows(listing_html, source) -> list[dict]:
    return parse_opportunities(listing_html, language=source.language)


@pytest.fixture(scope="module")
def wanted(rows, source) -> list[dict]:
    return in_scope(rows, excluded_types=source.exclude_notice_types)


# --- the registry and the fixture agree -----------------------------------------


def test_the_registry_entry_is_disabled_pending_a_terms_sign_off(source):
    assert source.id == "opec_fund"
    assert source.country == "multi"
    assert source.covers == ["LR", "NE", "MR"]
    assert source.admin_level == "donor"
    assert source.language == "en"
    assert source.connector_class == "FeedConnector"
    assert source.access_type == "public_listing"
    assert source.tos_status == "reviewed_restricted"
    assert source.enabled is False
    assert source.list_url == "https://opecfund.org/work-with-us/project-procurement/current-opportunities"


def test_the_registry_reads_the_deep_path_not_the_shallow_framework_page(source):
    """TRAP ONE: the page one segment shallower than list_url holds framework
    documents and zero notices; this asserts the registry points past it."""
    assert source.list_url.endswith("/current-opportunities")
    assert "project-procurement" in source.list_url


def test_the_registry_never_names_the_sibling_self_procurement_paths(source):
    """TRAP TWO: corporate-procurement/bidding and consultants/current-
    opportunities are the Fund buying for itself and must never be this
    source's list_url."""
    assert "corporate-procurement" not in source.list_url
    assert "consultants" not in source.list_url


def test_the_excluded_types_are_the_two_with_no_closing_date(source):
    """TRAP THREE: the vocabulary lives in the registry (rule 6), not the
    connector, and only names the two types that never have anything to bid
    on - not every row past its own deadline (see sources/opec_fund.yaml)."""
    assert source.exclude_notice_types == EXCLUDED_TYPES


def test_no_cpv_or_row_selector_is_declared(source):
    """No shared classification scheme exists on this board; a row_selector
    belongs to BrowserConnector sources only."""
    assert source.row_selector == ""


def test_the_recording_matches_the_registrys_own_url(document, source):
    assert document["list_url"] == source.list_url
    assert document["recorded_on"] == "2026-09-12"


# --- item count within the health range -----------------------------------------


def test_the_full_board_size_matches_what_was_measured(rows):
    assert len(rows) == ROW_COUNT


def test_the_kept_count_after_exclusion_is_within_the_registrys_expected_range(wanted, source):
    """expected_items_per_run models what this connector actually returns
    (post-exclusion), not the raw table size (see HEALTH in
    sources/opec_fund.yaml): 38 measured rows sit inside [15, 80]."""
    assert len(wanted) == KEPT_COUNT
    assert source.expected_min <= len(wanted) <= source.expected_max


def test_excluded_rows_are_gone_and_only_the_two_named_types(rows, wanted):
    dropped = [row for row in rows if row not in wanted]
    assert len(dropped) == ROW_COUNT - KEPT_COUNT
    assert {row["notice_type"] for row in dropped} == set(EXCLUDED_TYPES)
    assert not any(row["notice_type"] in EXCLUDED_TYPES for row in wanted)


def test_a_typo_in_exclude_notice_types_raises_rather_than_excluding_nothing(rows):
    """The guard monitor/connectors/ebrd.py uses for the same reason: a name
    that matches no row would otherwise silently keep every GPN and CAN."""
    with pytest.raises(ValueError, match="matches nothing"):
        in_scope(rows, excluded_types=["General Procurement Notice (GPN typo)"])


# --- every required field is present --------------------------------------------


def test_every_row_carries_its_required_text_fields(rows):
    for row in rows:
        assert row["date_posted"].strip()
        assert row["country"].strip()
        assert row["title"].strip()
        assert row["sector"].strip()
        assert row["notice_type"].strip()


def test_closing_date_is_present_as_a_key_even_when_the_row_states_none(rows):
    """Closing Date is not required to be non-empty (four different spellings
    of "no deadline" are real published values, not a parse failure), but the
    key itself is always present."""
    for row in rows:
        assert "closing_date" in row
        assert isinstance(row["closing_date"], str)


def test_the_four_spellings_of_no_closing_date_are_kept_exactly_as_published(rows):
    closing_dates = [row["closing_date"] for row in rows]

    not_applicable_row = next(row for row in rows if row["closing_date"] == "Not Applicable")
    assert not_applicable_row["title"] == "Liberia Special Agro Industrial Processing Zone (SAPZ) Project"
    assert "N/A (Project not yet approved)" in closing_dates
    assert "" in closing_dates  # the row whose cell is a lone non-breaking space
    assert closing_dates.count("N/A") == 6


def test_a_row_missing_a_required_field_raises(listing_html, source):
    """An empty country cell would otherwise map a notice with no geography
    and no way to tell a real gap from a changed page."""
    broken = listing_html.replace("<td>Nicaragua</td>", "<td></td>", 1)

    with pytest.raises(ValueError, match="country"):
        parse_opportunities(broken, language=source.language)


def test_every_row_states_at_least_one_document_link(rows):
    for row in rows:
        assert row["documents"]
        for doc in row["documents"]:
            assert doc["url"].startswith("https://")
            assert "label" in doc


def test_a_row_with_no_document_link_raises(listing_html, source):
    """Unlike sierra_leone.yaml's bid table, every one of the 48 rows measured
    here carries a link; a row with none is a shape this source has not shown
    and raises rather than silently falling back to the board's own URL. The
    Liberia fisheries row (no <p>, a single anchor) is the one with exactly
    one link to strip."""
    broken = listing_html.replace(
        '<a href="https://publications.opecfund.org/view/224020224/">'
        "<strong><u>Liberia Integrated Fisheries Sector Strengthening Project (LIFSSP)</u></strong></a>",
        "<strong><u>Liberia Integrated Fisheries Sector Strengthening Project (LIFSSP)</u></strong>",
        1,
    )

    with pytest.raises(ValueError, match="no document link at all"):
        parse_opportunities(broken, language=source.language)


# --- the two rows with no <p> in the notice cell, and the multi-document row ----


def test_a_notice_cell_with_no_paragraph_falls_back_to_the_whole_cell(rows):
    """Two of the 48 rows are a single linked title with no <p> wrapper at all."""
    liberia_fisheries = next(row for row in rows if "LIFSSP" in row["title"])
    assert liberia_fisheries["title"] == "Liberia Integrated Fisheries Sector Strengthening Project (LIFSSP)"
    assert liberia_fisheries["documents"] == [
        {
            "label": "Liberia Integrated Fisheries Sector Strengthening Project (LIFSSP)",
            "url": "https://publications.opecfund.org/view/224020224/",
        }
    ]

    mano_river = next(row for row in rows if "Mano River" in row["title"])
    assert mano_river["documents"][0]["url"] == "https://publications.opecfund.org/view/224351812/"


def test_a_row_with_several_documents_keeps_every_one_in_published_order(rows):
    """Row 0: a title linked to a national e-procurement portal, plus four
    FlippingBook documents in a <ul> below it - all five kept, in order,
    nothing ranked or deduplicated."""
    nicaragua_row = next(row for row in rows if row["country"] == "Nicaragua" and row["sector"] == "Transport")
    labels = [doc["label"] for doc in nicaragua_row["documents"]]

    assert labels == [
        "Construction of the Masaya – Sabana Grande Road, Interurban Section, Phase 1",
        "REOI",
        "Amendment 1",
        "Terms of Reference",
        "Clarification 1",
    ]
    assert nicaragua_row["documents"][0]["url"].startswith("https://www.gestion.nicaraguacompra.gob.ni/")
    assert nicaragua_row["documents"][1]["url"] == "https://publications.opecfund.org/view/984283216/"


def test_a_type_cell_with_a_trailing_blank_paragraph_still_reads_one_line(rows):
    """The oldest row's Type cell is `<p>...(REOI)</p><p>&nbsp;</p>`; the blank
    second paragraph must not survive as an empty second line."""
    oldest = next(row for row in rows if row["date_posted"] == "July 11, 2025")

    assert oldest["notice_type"] == "Request For Expression Interest (REOI)"
    assert "\n" not in oldest["notice_type"]


# --- declared language matching --------------------------------------------------


def test_the_page_declares_the_registrys_language(listing_html, source):
    assert '<html lang="en">' in listing_html
    assert source.language == "en"
    parse_opportunities(listing_html, language=source.language)


def test_a_page_declaring_another_language_raises(listing_html, source):
    changed = listing_html.replace('<html lang="en">', '<html lang="fr">', 1)

    with pytest.raises(ValueError, match="fr"):
        parse_opportunities(changed, language=source.language)


# --- a changed layout fails loudly, not silently ---------------------------------


def test_a_renamed_table_class_raises(listing_html, source):
    """Without this check a re-theme that dropped the `table` class would read
    as a board of zero rows, the exact silent failure rule 4 forbids."""
    renamed = listing_html.replace('<table class="table">', '<table class="opportunities">', 1)

    with pytest.raises(ValueError, match=RESULTS_TABLE):
        parse_opportunities(renamed, language=source.language)


def test_a_reordered_header_raises_even_though_cell_counts_still_match(listing_html, source):
    """A column reorder would keep six <td> per row and pass the cell-count
    check; only the header-text check catches it."""
    reordered = listing_html.replace(
        "<tr><th>Date</th><th>Country</th><th>Project / Notice</th><th>Sector</th>"
        "<th>Closing Date</th><th>Type</th></tr>",
        "<tr><th>Country</th><th>Date</th><th>Project / Notice</th><th>Sector</th>"
        "<th>Closing Date</th><th>Type</th></tr>",
        1,
    )

    with pytest.raises(ValueError, match="header"):
        parse_opportunities(reordered, language=source.language)


def test_a_row_that_lost_a_column_raises(listing_html, source):
    broken = listing_html.replace("<td>Transport</td>", "<span>Transport</span>", 1)

    with pytest.raises(ValueError, match=f"not {CELLS_PER_ROW}"):
        parse_opportunities(broken, language=source.language)


def test_a_table_emptied_of_rows_raises_rather_than_yielding_zero(listing_html, source):
    emptied = re.sub(r"(<tbody>).*?(</tbody>)", r"\1\2", listing_html, flags=re.DOTALL)
    assert 'class="table"' in emptied  # the container survives; only its rows are gone

    with pytest.raises(ValueError, match="zero rows"):
        parse_opportunities(emptied, language=source.language)


# --- notice_title, documents, cell_text in isolation ------------------------------


def test_notice_title_reads_the_first_paragraph():
    cell = _cell("<td><p>Title text</p><p>Downloads:</p><ul><li><a href='https://x'>A</a></li></ul></td>")

    assert notice_title(cell) == "Title text"


def test_notice_title_falls_back_to_the_whole_cell_with_no_paragraph():
    cell = _cell('<td><a href="https://x"><strong><u>Bare title</u></strong></a></td>')

    assert notice_title(cell) == "Bare title"


def test_documents_raises_on_an_empty_href():
    cell = _cell('<td><a href="">Download</a></td>')

    with pytest.raises(ValueError, match="no href"):
        documents(cell, position=0)


def test_documents_raises_on_no_link_at_all():
    cell = _cell("<td><p>Nothing to click</p></td>")

    with pytest.raises(ValueError, match="no document link at all"):
        documents(cell, position=0)


def test_a_non_breaking_space_does_not_survive_into_a_cell():
    cell = _cell("<td>public&nbsp;financial   management</td>")

    assert cell_text(cell) == "public financial management"


def test_a_whitespace_only_line_is_dropped():
    cell = _cell("<td><p>Request For Expression Interest (REOI)</p><p>&nbsp;</p></td>")

    assert cell_text(cell) == "Request For Expression Interest (REOI)"


# --- what the connector actually fetches -----------------------------------------


class _FixtureClient:
    """A stand-in httpx client serving exactly one page, recording the request."""

    def __init__(self, listing_html: str):
        self._html = listing_html
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        assert not kwargs, f"the connector sent {kwargs} with a request; this source has no query parameters"
        self.requested.append(url)
        return httpx.Response(status_code=200, text=self._html, request=httpx.Request("GET", url))


def test_a_run_requests_the_board_once_and_fetches_only_the_kept_rows(listing_html, source):
    connector = OpecFundConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)

    assert client.requested == [source.list_url]
    assert len(raw_notices) == KEPT_COUNT
    assert all(isinstance(raw, RawNotice) for raw in raw_notices)
    assert all(raw.source_id == "opec_fund" for raw in raw_notices)
    assert all(raw.mime == "application/json" for raw in raw_notices)


def test_every_raw_notice_url_is_the_rows_first_document_link(listing_html, source):
    connector = OpecFundConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)

    for raw in raw_notices:
        payload = json.loads(raw.payload)
        assert raw.url == payload["documents"][0]["url"]
        assert payload["notice_type"] not in EXCLUDED_TYPES


def test_the_payload_round_trips_every_field_the_row_parsed(listing_html, source, wanted):
    connector = OpecFundConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)

    for raw, row in zip(raw_notices, wanted, strict=True):
        assert json.loads(raw.payload) == row


def test_nothing_here_translates_or_parses_a_date(listing_html, source):
    """Rule 5 and rule 9: what a connector returns is the response as fetched.
    Closing dates are stored as the page's own strings, not a canonical form."""
    connector = OpecFundConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)
    first = json.loads(raw_notices[0].payload)

    assert first["date_posted"] == "September 9, 2026"
    assert first["closing_date"] == "October 2, 2026, 15:00 (Nicaragua time)"
    assert isinstance(first["closing_date"], str)


# --- helpers ---------------------------------------------------------------


def _cell(markup: str):
    """One `<td>` parsed the way it actually arrives: inside a table and a row."""
    return HTMLParser(f"<table><tr>{markup}</tr></table>").css_first("td")
