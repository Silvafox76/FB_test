"""BOAD's contract, against the pass recorded on 2026-09-12.

The fixture is two files, the shape a two-request connector needs (the same
reason `tests/contract/test_liberia.py`'s fixture holds two document kinds and
`tests/contract/test_ebrd.py` reads a separate `.html` and `.json`):
`tests/contract/fixtures/boad.html` is the listing's real HTML shell (the
Inertia version is scraped from it), and `tests/contract/fixtures/boad.json`
holds the two paged JSON responses (`{"pages": {"1": ..., "2": ...}}`) that same
pass read. Two pages is what a real run needed that day: page 1 is entirely
inside the 30-day window, page 2 crosses out of it after its fourth row, and the
walk correctly never asks for a third page. Most of this file is about the two
measured hazards in `monitor/connectors/boad.py`'s own docstring - the Inertia
version 409 and the `project_status` parameter that answers with a differently-
shaped empty response - because everything else here is straightforward parsing
once those two are handled.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml

from monitor.connectors.boad import (
    BOAD_PROJECT_STATUS,
    PUBLIC_BASE_URL,
    BoadConnector,
    is_biddable,
    parse_inertia_version,
    parse_tenders_page,
    record_date,
    validate_rows,
    within_window,
)
from monitor.models import Source

pytestmark = pytest.mark.contract

HTML_FIXTURE = Path(__file__).parent / "fixtures" / "boad.html"
JSON_FIXTURE = Path(__file__).parent / "fixtures" / "boad.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "boad.yaml"

# The live pass this fixture was recorded from, 2026-09-12. cutoff() is relative
# to "today", so every window-cut test pins it to that day rather than the real
# one.
RECORDED_ON = date(2026, 9, 12)

# The version live on 2026-09-12, scraped from the recorded HTML shell and
# carried by both recorded JSON pages. Asserted rather than just used, so a
# re-recorded fixture whose two halves disagree fails here and not somewhere
# subtler.
RECORDED_VERSION = "535150b3678be449c0cdeec106cc10a6"

# What the recorded pass actually held, asserted rather than just described.
RECORDED_ROWS_PER_PAGE = 6
RECORDED_IN_WINDOW = 10  # all 6 of page 1, the first 4 of page 2
RECORDED_BIDDABLE = 4


@pytest.fixture(scope="module")
def html_shell() -> str:
    return HTML_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pages() -> dict:
    return json.loads(JSON_FIXTURE.read_text(encoding="utf-8"))["pages"]


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def connector(source) -> BoadConnector:
    return BoadConnector(source, [])


@pytest.fixture(scope="module")
def rows_page_1(pages) -> list[dict]:
    return validate_rows(parse_tenders_page(pages["1"])["data"])


@pytest.fixture(scope="module")
def rows_page_2(pages) -> list[dict]:
    return validate_rows(parse_tenders_page(pages["2"])["data"])


@pytest.fixture(scope="module")
def cutoff(connector) -> date:
    return connector.cutoff(today=RECORDED_ON)


@pytest.fixture(scope="module")
def in_window(rows_page_1, rows_page_2, cutoff) -> list[dict]:
    return within_window(rows_page_1, cutoff) + within_window(rows_page_2, cutoff)


# --- cpv_prefixes and covers, taken and unused ---------------------------------


def test_cpv_prefixes_is_accepted_but_unused(connector):
    """No CPV code and no per-tender country field exist in this source; see the
    module docstring. Taken so every connector is built the same shape."""
    assert connector.cpv_prefixes == []


def test_covers_lists_seven_of_boads_eight_member_states(source):
    """Guinea-Bissau (GW) is deliberately absent; see sources/boad.yaml."""
    assert source.covers == ["BJ", "BF", "CI", "ML", "NE", "SN", "TG"]
    assert "GW" not in source.covers


# --- the Inertia version, scraped from the HTML shell ---------------------------


def test_the_version_is_scraped_from_the_html_shell(html_shell):
    assert parse_inertia_version(html_shell) == RECORDED_VERSION


def test_both_recorded_json_pages_carry_the_same_version(pages):
    """The two-request sequence only works if the version scraped from the shell
    is the one the JSON calls actually needed that pass."""
    assert pages["1"]["version"] == RECORDED_VERSION
    assert pages["2"]["version"] == RECORDED_VERSION


def test_a_shell_with_no_data_page_attribute_raises():
    with pytest.raises(ValueError, match="no Inertia data-page"):
        parse_inertia_version("<html><body>no app div here</body></html>")


def test_a_data_page_with_no_version_raises():
    broken = '<div id="app" data-page="{&quot;component&quot;:&quot;Page&quot;,&quot;props&quot;:{}}">'
    with pytest.raises(ValueError, match="no version"):
        parse_inertia_version(broken)


# --- the paginator shape, and the project_status hazard that breaks it ----------


def test_the_recorded_pages_are_the_paginator_shape(pages):
    for page in ("1", "2"):
        tenders = parse_tenders_page(pages[page])
        assert tenders["per_page"] == RECORDED_ROWS_PER_PAGE
        assert len(tenders["data"]) == RECORDED_ROWS_PER_PAGE
    assert pages["1"]["props"]["tenders"]["total"] == 379
    assert pages["1"]["props"]["tenders"]["last_page"] == 64


def test_a_response_with_no_tenders_prop_raises(pages):
    broken = json.loads(json.dumps(pages["1"]))
    del broken["props"]["tenders"]

    with pytest.raises(ValueError, match="props.tenders"):
        parse_tenders_page(broken)


def test_a_bare_list_instead_of_a_paginator_raises(pages):
    """The measured hazard: a project_status query parameter answers this shape."""
    broken = json.loads(json.dumps(pages["1"]))
    broken["props"]["tenders"] = []

    with pytest.raises(ValueError, match="not the paginator shape"):
        parse_tenders_page(broken)


# --- the rows on a page ----------------------------------------------------------


def test_every_recorded_row_has_what_the_mapper_and_the_window_need(rows_page_1, rows_page_2):
    for row in rows_page_1 + rows_page_2:
        assert row["id"]
        assert row["link"].startswith("/fr/opportunites/appels-doffre/")
        assert row["title"].strip()
        assert row["status"] == "publish"
        assert row["type"] == "tender"
        assert "end_at" in row["acf"]
        assert "start_at" in row["acf"]


def test_the_recorded_pages_are_sorted_newest_first(rows_page_1, rows_page_2):
    dates = [record_date(row) for row in rows_page_1 + rows_page_2]
    assert dates == sorted(dates, reverse=True)


def test_a_row_missing_a_required_field_raises(pages):
    broken = json.loads(json.dumps(pages["1"]))
    del broken["props"]["tenders"]["data"][0]["date_gmt"]

    with pytest.raises(ValueError, match="date_gmt"):
        validate_rows(broken["props"]["tenders"]["data"])


def test_an_unpublished_row_raises(pages):
    broken = json.loads(json.dumps(pages["1"]))
    broken["props"]["tenders"]["data"][0]["status"] = "draft"

    with pytest.raises(ValueError, match="draft"):
        validate_rows(broken["props"]["tenders"]["data"])


def test_a_row_of_the_wrong_type_raises(pages):
    broken = json.loads(json.dumps(pages["1"]))
    broken["props"]["tenders"]["data"][0]["type"] = "page"

    with pytest.raises(ValueError, match="tender"):
        validate_rows(broken["props"]["tenders"]["data"])


def test_an_unknown_project_status_raises(pages):
    """BOAD's own taxonomy growing a seventh term is a person's decision, not a
    silent keep or drop; see the module docstring."""
    broken = json.loads(json.dumps(pages["1"]))
    broken["props"]["tenders"]["data"][0]["project_status"] = [{"name": "Avis de préqualification"}]

    with pytest.raises(ValueError, match="Avis de préqualification"):
        validate_rows(broken["props"]["tenders"]["data"])


def test_an_unsorted_page_raises(pages):
    broken = json.loads(json.dumps(pages["1"]))
    data = broken["props"]["tenders"]["data"]
    data[0], data[-1] = data[-1], data[0]

    with pytest.raises(ValueError, match="not sorted"):
        validate_rows(data)


def test_record_date_reads_the_gmt_timestamp():
    assert record_date({"id": "x", "date_gmt": "2026-09-11T18:15:56.000000Z"}) == date(2026, 9, 11)


def test_record_date_raises_when_absent():
    with pytest.raises(ValueError, match="no date_gmt"):
        record_date({"id": "x", "date_gmt": None})


# --- the window cut ---------------------------------------------------------------


def test_the_window_is_thirty_days_before_the_run(connector):
    assert connector.cutoff(today=RECORDED_ON) == date(2026, 8, 13)


def test_the_window_cut_keeps_the_measured_count(in_window, cutoff, source):
    assert len(in_window) == RECORDED_IN_WINDOW
    assert all(record_date(row) >= cutoff for row in in_window)


def test_page_one_is_entirely_inside_the_window_and_page_two_is_not(rows_page_1, rows_page_2, cutoff):
    assert len(within_window(rows_page_1, cutoff)) == len(rows_page_1) == RECORDED_ROWS_PER_PAGE
    assert len(within_window(rows_page_2, cutoff)) == 4 < len(rows_page_2)


# --- what counts as biddable ------------------------------------------------------


def test_boad_project_status_is_the_closed_set_measured_on_2026_09_12():
    assert BOAD_PROJECT_STATUS == {
        "Avis d'appel d'offre",
        "Avis de manifestation d'intérêt",
        "Résultats d'appel d'offre",
        "Résultats de manifestation d'intérêt",
        "Avis de passation de marché",
        "Plan de Passation des Marchés",
    }


def test_the_registry_excludes_every_decided_status(source):
    """The two biddable values are the ones NOT in this list."""
    assert set(source.exclude_notice_types) == BOAD_PROJECT_STATUS - {
        "Avis d'appel d'offre",
        "Avis de manifestation d'intérêt",
    }


def test_the_window_cut_yields_the_measured_biddable_count(in_window, source):
    biddable = [row for row in in_window if is_biddable(row, exclude_notice_types=source.exclude_notice_types)]

    assert len(biddable) == RECORDED_BIDDABLE
    assert source.expected_min <= len(biddable) <= source.expected_max
    for row in biddable:
        names = {entry["name"] for entry in row["project_status"]}
        assert names & {"Avis d'appel d'offre", "Avis de manifestation d'intérêt"}
        assert row["acf"]["end_at"]


def test_a_biddable_status_with_no_end_at_is_not_biddable():
    """19 of the 379 records measured 2026-09-12 carry this gap; see the module
    docstring. Both conditions are required, and neither alone is enough."""
    row = {"project_status": [{"name": "Avis d'appel d'offre"}], "acf": {"end_at": None}}

    assert is_biddable(row, exclude_notice_types=[]) is False


def test_an_end_at_under_a_decided_status_is_not_biddable():
    """24 of the 379 records measured 2026-09-12 carry this gap the other way."""
    row = {"project_status": [{"name": "Résultats d'appel d'offre"}], "acf": {"end_at": "10/10/2026"}}

    assert is_biddable(row, exclude_notice_types=["Résultats d'appel d'offre"]) is False


def test_no_project_status_at_all_is_not_biddable_even_with_a_deadline():
    """65 of the 379 records measured 2026-09-12 carry no status; 18 of those
    nonetheless carry a real end_at. A live deadline with no classification is
    not enough signal on its own."""
    row = {"project_status": [], "acf": {"end_at": "10/10/2026"}}

    assert is_biddable(row, exclude_notice_types=[]) is False


def test_a_decided_tag_disqualifies_even_alongside_a_biddable_one():
    """The one record in the corpus tagged both ways: its own result has since
    been published under the same taxonomy entry, so the decided tag wins."""
    row = {
        "project_status": [
            {"name": "Avis de manifestation d'intérêt"},
            {"name": "Résultats de manifestation d'intérêt"},
        ],
        "acf": {"end_at": "27/06/2024"},
    }

    assert is_biddable(row, exclude_notice_types=["Résultats de manifestation d'intérêt"]) is False


def test_end_at_and_start_at_are_stored_as_the_dd_mm_yyyy_strings_they_arrive_as(in_window, source):
    """Rule 9 and rule 10: nothing here parses a date; a future normaliser does."""
    biddable = [row for row in in_window if is_biddable(row, exclude_notice_types=source.exclude_notice_types)]
    for row in biddable:
        assert row["acf"]["end_at"].count("/") == 2
        assert len(row["acf"]["end_at"]) == 10


# --- one whole pass, replayed ------------------------------------------------------


def replaying_client(html_shell: str, pages: dict) -> httpx.Client:
    """A client that serves the recorded shell and pages and refuses anything else."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-inertia") != "true":
            return httpx.Response(200, text=html_shell)

        assert request.headers.get("x-inertia-version") == RECORDED_VERSION
        page = request.url.params.get("page")
        if page in pages:
            return httpx.Response(200, json=pages[page])
        raise AssertionError(f"the connector asked for page {page!r}, which is not part of the recorded pass")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_one_whole_pass_yields_the_measured_biddable_count(connector, html_shell, pages, monkeypatch):
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 8, 13))

    with replaying_client(html_shell, pages) as client:
        raw_notices = connector.fetch_raw(client)

    assert len(raw_notices) == RECORDED_BIDDABLE
    seen_urls = set()
    for raw in raw_notices:
        assert raw.source_id == "boad"
        assert raw.mime == "application/json"
        assert raw.url.startswith(PUBLIC_BASE_URL)
        seen_urls.add(raw.url)

        payload = json.loads(raw.payload)
        assert payload["status"] == "publish"
        assert payload["acf"]["end_at"]

    assert len(seen_urls) == RECORDED_BIDDABLE, "each notice must have its own URL"


def test_a_stale_inertia_version_raises_end_to_end(connector, html_shell, pages, monkeypatch):
    """A version mismatch is a real 409 on the live endpoint (see the module
    docstring); this is that failure, replayed, and it must stop the run rather
    than fall back to anything."""
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 8, 13))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-inertia") != "true":
            return httpx.Response(200, text=html_shell)
        return httpx.Response(409, headers={"x-inertia-location": str(request.url)})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        connector.fetch_raw(client)


def test_the_walk_never_asks_for_a_third_page(connector, html_shell, pages, monkeypatch):
    """Page 2 crosses out of the window at its fifth row, so a correct connector
    never requests page 3 - the recorded fixture only has two pages for exactly
    this reason."""
    monkeypatch.setattr(connector, "cutoff", lambda today=None: date(2026, 8, 13))
    requested_pages = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-inertia") != "true":
            return httpx.Response(200, text=html_shell)
        page = request.url.params.get("page")
        requested_pages.append(page)
        return httpx.Response(200, json=pages[page])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector.fetch_raw(client)

    assert requested_pages == ["1", "2"]
