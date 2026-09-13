"""GHANEPS Current Tenders, against the two pages recorded live on 2026-09-13.

`tests/contract/fixtures/ghana.html` and `ghana_page2.html` are the verbatim
bodies of two anonymous, cookie-free GETs (see `ghana.json` for the exact URLs
and byte counts). Two pages, not one, because the 7-day window this connector
reads (`sources/ghana.yaml`'s daily schedule) spans a page boundary: all 10
rows of page 1 are inside the window, and only the first 6 of page 2's 10 are.
That is the 16-row count `sources/ghana.yaml`'s VOLUME section measured, and
this file locks it so a re-recorded fixture that lost rows fails here rather
than three stages downstream.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.ghana import (
    CELLS_PER_ROW,
    EXPECTED_STATUS,
    RESULTS_TABLE,
    GhanaConnector,
    cell_text,
    parse_listing_page,
    publication_datetime,
    within_window,
)
from monitor.models import RawNotice, Source

pytestmark = pytest.mark.contract

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "ghana.yaml"

# What the recorded pass actually held. Asserted rather than described, so a
# re-recorded fixture that quietly holds fewer rows fails here, not somewhere
# subtler.
ROWS_PER_PAGE = 10
IN_WINDOW_COUNT = 16
RECORDED_ON = date(2026, 9, 13)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((FIXTURES / "ghana.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def page1_html() -> str:
    return (FIXTURES / "ghana.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page2_html() -> str:
    return (FIXTURES / "ghana_page2.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> GhanaConnector:
    return GhanaConnector(source, [])


@pytest.fixture(scope="module")
def page1(page1_html, source) -> dict:
    return parse_listing_page(page1_html, expected_page=1, language=source.language)


@pytest.fixture(scope="module")
def page2(page2_html, source) -> dict:
    return parse_listing_page(page2_html, expected_page=2, language=source.language)


# --- the registry and the fixture agree -----------------------------------------


def test_the_recording_matches_the_registrys_own_url(manifest, source):
    assert manifest["list_url"] == source.list_url
    assert manifest["recorded_on"] == "2026-09-13"


def test_cpv_prefixes_is_accepted_but_unused(connector):
    """No CPV or other classification code appears on this listing (see the
    module docstring); taken so every connector is built the same shape."""
    assert connector.cpv_prefixes == []


def test_no_row_selector_is_declared(source):
    """row_selector belongs to BrowserConnector sources only (monitor/models.py's
    Source validator rejects it on any other class); GHANEPS needs no browser."""
    assert source.row_selector == ""
    assert source.connector_class == "PageConnector"
    assert source.access_type == "public_listing"
    assert source.tos_status == "reviewed_ok"


# --- the listing page itself ------------------------------------------------


def test_page_one_has_ten_rows_and_states_the_full_archive_size(page1):
    assert len(page1["rows"]) == ROWS_PER_PAGE
    assert page1["current_page"] == 1
    assert page1["total_pages"] == 7
    assert page1["total_results"] == 69


def test_page_two_has_ten_rows_and_agrees_on_pagination_state(page2):
    assert len(page2["rows"]) == ROWS_PER_PAGE
    assert page2["current_page"] == 2
    assert page2["total_pages"] == 7
    assert page2["total_results"] == 69


def test_every_row_carries_its_required_fields(page1, page2):
    for row in page1["rows"] + page2["rows"]:
        assert row["resource_id"].isdigit()
        assert row["title"].strip()
        assert row["procuring_entity"].strip()
        assert row["description"].strip()
        assert row["deadline"].strip()
        assert row["procedure"].strip()
        assert row["status"] == EXPECTED_STATUS
        assert row["notice_pdf_url"].startswith("https://www.ghaneps.gov.gh/epps/cft/downloadNoticeForAdvSearch.do")
        assert row["publication_date"].strip()


def test_one_real_row_matches_the_live_values_recorded_in_the_module_docstring(page1):
    row = page1["rows"][0]

    assert row["resource_id"] == "3403640"
    assert row["title"] == "Construction of Akim Oda Branch Manager's bungalow at Akim Swedru"
    assert row["procuring_entity"] == "Social Security And National Insurance Trust"
    assert row["description"] == "Construction of Akim Oda Branch Manager's bungalow at Akim Swedru."
    assert row["deadline"] == "Fri Oct 02 10:00:00 GMT 2026"
    assert row["procedure"] == "National Competitive Tendering"
    assert row["publication_date"] == "Fri Sep 11 15:58:07 GMT 2026"
    assert (
        row["notice_pdf_url"] == "https://www.ghaneps.gov.gh/epps/cft/downloadNoticeForAdvSearch.do?resourceId=3403640"
    )


def test_the_procedure_column_carries_more_than_one_free_text_value(page1, page2):
    """No CPV or other shared code exists on this listing (sources/ghana.yaml's
    own grep); procedure is free text and at least three distinct values are
    measured across the two recorded pages."""
    procedures = {row["procedure"] for row in page1["rows"] + page2["rows"]}
    assert procedures == {
        "National Competitive Tendering",
        "Least Cost Selection with EOI",
        "Quality-Cost Based Selection with EOI",
    }


def test_a_row_missing_a_required_field_raises(page1_html, source):
    broken = page1_html.replace("Social Security And National Insurance Trust", "", 1)

    with pytest.raises(ValueError, match="procuring entity"):
        parse_listing_page(broken, expected_page=1, language=source.language)


def test_an_unexpected_status_raises(page1_html, source):
    broken = page1_html.replace("Bid Submission", "Awarded", 1)

    with pytest.raises(ValueError, match="Awarded"):
        parse_listing_page(broken, expected_page=1, language=source.language)


def test_a_resource_id_mismatch_between_title_and_pdf_link_raises(page1_html, source):
    broken = page1_html.replace(
        'href="/epps/cft/downloadNoticeForAdvSearch.do?resourceId=3403640"',
        'href="/epps/cft/downloadNoticeForAdvSearch.do?resourceId=9999999"',
        1,
    )

    with pytest.raises(ValueError, match="wrong notice"):
        parse_listing_page(broken, expected_page=1, language=source.language)


def test_a_row_that_lost_a_column_raises(page1_html, source):
    broken = page1_html.replace(
        "<td>\n\t\t\t\t\t\tSocial Security And National Insurance Trust\n\t\t\t\t\t</td>",
        "<span>Social Security And National Insurance Trust</span>",
        1,
    )

    with pytest.raises(ValueError, match=f"not {CELLS_PER_ROW}"):
        parse_listing_page(broken, expected_page=1, language=source.language)


def test_a_renamed_results_table_raises(page1_html, source):
    broken = page1_html.replace('id="T01"', 'id="T02"')

    with pytest.raises(ValueError, match="T01"):
        parse_listing_page(broken, expected_page=1, language=source.language)


def test_a_redirect_to_the_home_page_raises_rather_than_reading_a_healthy_zero(source):
    """The transient hazard sources/ghana.yaml records for 2026-09-12: an
    occasional redirect lands on GHANEPS's own home page, which has no
    RESULTS_TABLE at all. No retry is built for this (see the module
    docstring); it must raise rather than read as an empty success (rule 4)."""
    home_page = "<html lang='en'><body><h1>Welcome to GHANEPS</h1></body></html>"

    with pytest.raises(ValueError, match=RESULTS_TABLE.split("#")[1]):
        parse_listing_page(home_page, expected_page=1, language=source.language)


def test_a_table_emptied_of_rows_raises_rather_than_yielding_zero(page1_html, source):
    import re

    emptied = re.sub(r"(<tbody>).*?(</tbody>)", r"\1\2", page1_html, flags=re.DOTALL)
    assert 'id="T01"' in emptied  # the container survives; only its rows are gone

    with pytest.raises(ValueError, match="zero rows"):
        parse_listing_page(emptied, expected_page=1, language=source.language)


def test_requesting_page_two_but_receiving_page_one_content_raises(page1_html, source):
    """Guards the PAGINATION hazard in the module docstring: if the dynamic
    `d-3680175-p` parameter this connector relies on ever stopped working,
    GHANEPS would silently answer page 2's request with page 1's own rows."""
    with pytest.raises(ValueError, match="requested page 2 but GHANEPS returned page 1"):
        parse_listing_page(page1_html, expected_page=2, language=source.language)


def test_a_page_declaring_another_language_raises(page1_html, source):
    changed = page1_html.replace('lang="en" xml:lang="en"', 'lang="fr" xml:lang="fr"', 1)

    with pytest.raises(ValueError, match="'fr'"):
        parse_listing_page(changed, expected_page=1, language=source.language)


def test_an_unsorted_page_raises(page1):
    """The measured default sort is publication date descending; this is
    checked on the response received, not trusted because it was not
    explicitly requested (same reasoning as monitor/connectors/liberia.py)."""
    rows = list(page1["rows"])
    rows[0], rows[5] = rows[5], rows[0]

    with pytest.raises(ValueError, match="not sorted"):
        within_window(rows, datetime(2026, 9, 6, tzinfo=UTC))


# --- publication_datetime ----------------------------------------------------


def test_publication_datetime_reads_javas_date_tostring_format():
    row = {"resource_id": "x", "publication_date": "Fri Sep 11 15:58:07 GMT 2026"}
    assert publication_datetime(row) == datetime(2026, 9, 11, 15, 58, 7, tzinfo=UTC)


def test_publication_datetime_raises_when_absent():
    with pytest.raises(ValueError, match="no publication_date"):
        publication_datetime({"resource_id": "x", "publication_date": ""})


def test_publication_datetime_raises_on_an_unparseable_string():
    with pytest.raises(ValueError, match="publication_date"):
        publication_datetime({"resource_id": "x", "publication_date": "2026-09-11"})


# --- cell_text ---------------------------------------------------------------


def test_cell_text_collapses_template_whitespace_and_keeps_non_breaking_spaces_ordinary():
    from selectolax.parser import HTMLParser

    cell = HTMLParser("<table><tr><td>public&nbsp;financial   management</td></tr></table>").css_first("td")
    assert cell_text(cell) == "public financial management"


# --- the window cut (health-band assertion the task requires) ---------------


def test_the_window_cut_lands_inside_the_registrys_expected_band(connector, source):
    cutoff = connector.cutoff(today=RECORDED_ON)
    assert cutoff == datetime(2026, 9, 6, tzinfo=UTC)

    page1_kept = within_window(
        parse_listing_page(
            (FIXTURES / "ghana.html").read_text(encoding="utf-8"), expected_page=1, language=source.language
        )["rows"],
        cutoff,
    )
    page2_kept = within_window(
        parse_listing_page(
            (FIXTURES / "ghana_page2.html").read_text(encoding="utf-8"), expected_page=2, language=source.language
        )["rows"],
        cutoff,
    )

    total = len(page1_kept) + len(page2_kept)
    assert len(page1_kept) == ROWS_PER_PAGE, "the whole first page is inside the 7-day window"
    assert len(page2_kept) == IN_WINDOW_COUNT - ROWS_PER_PAGE
    assert total == IN_WINDOW_COUNT
    assert source.expected_min <= total <= source.expected_max, (
        f"{total} in-window rows must sit inside sources/ghana.yaml's expected_items_per_run "
        f"[{source.expected_min}, {source.expected_max}]"
    )


# --- one whole pass, replayed -------------------------------------------------


class _FixtureClient:
    """A stand-in httpx client serving exactly the two recorded pages by URL."""

    def __init__(self, connector: GhanaConnector, pages: dict[int, str]):
        self._urls = {connector.page_url(number): html for number, html in pages.items()}
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        assert not kwargs, f"the connector sent {kwargs} with a request; GHANEPS takes no other parameters here"
        self.requested.append(url)
        if url not in self._urls:
            raise AssertionError(f"the connector asked for {url}, which is not one of the two recorded pages")
        return httpx.Response(status_code=200, text=self._urls[url], request=httpx.Request("GET", url))


def test_one_whole_pass_yields_the_measured_count_and_stops_at_two_pages(connector, page1_html, page2_html):
    connector.cutoff = lambda today=None: datetime(2026, 9, 6, tzinfo=UTC)  # noqa: ARG005
    client = _FixtureClient(connector, {1: page1_html, 2: page2_html})

    raw_notices = connector.fetch_raw(client)

    assert client.requested == [connector.page_url(1), connector.page_url(2)], "no third page should be requested"
    assert len(raw_notices) == IN_WINDOW_COUNT
    assert all(isinstance(raw, RawNotice) for raw in raw_notices)
    assert all(raw.source_id == "ghana" for raw in raw_notices)
    assert all(raw.mime == "application/json" for raw in raw_notices)

    seen_resource_ids = set()
    for raw in raw_notices:
        payload = json.loads(raw.payload)
        assert sorted(payload) == [
            "deadline",
            "description",
            "notice_pdf_url",
            "procedure",
            "procuring_entity",
            "publication_date",
            "resource_id",
            "status",
            "title",
        ]
        assert raw.url == payload["notice_pdf_url"]
        seen_resource_ids.add(payload["resource_id"])

    assert len(seen_resource_ids) == IN_WINDOW_COUNT, "each notice must have its own resourceId"


def test_nothing_here_parses_a_date_or_translates(connector, page1_html, page2_html):
    """Rule 5 and rule 9: what a connector returns is the response as fetched.
    The deadline and publication date both stay the source's own verbatim
    strings, not a canonical form - parsing them is the normaliser's job."""
    connector.cutoff = lambda today=None: datetime(2026, 9, 6, tzinfo=UTC)  # noqa: ARG005
    client = _FixtureClient(connector, {1: page1_html, 2: page2_html})

    raw_notices = connector.fetch_raw(client)
    first = json.loads(raw_notices[0].payload)

    assert first["deadline"] == "Fri Oct 02 10:00:00 GMT 2026"
    assert first["publication_date"] == "Fri Sep 11 15:58:07 GMT 2026"
    assert isinstance(first["deadline"], str)


# --- personal data (task requirement: checked, not assumed) -----------------


def test_no_personal_data_is_present_in_either_recorded_page(page1, page2):
    """Every title, procuring entity and description tooltip on both recorded
    pages (20 rows) is searched for an email address or a phone-number-shaped
    digit run. None exists: every description names an institution, never a
    person, so there is nothing here for a normaliser to strip before a model
    call (rule 19)."""
    import re

    email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    phone = re.compile(r"\+?\d[\d \-]{7,}\d")

    checked = 0
    for row in page1["rows"] + page2["rows"]:
        for field in ("title", "procuring_entity", "description"):
            value = row[field]
            assert not email.search(value), f"email-shaped text in {field}: {value!r}"
            assert not phone.search(value), f"phone-shaped text in {field}: {value!r}"
            checked += 1
    assert checked == 20 * 3
