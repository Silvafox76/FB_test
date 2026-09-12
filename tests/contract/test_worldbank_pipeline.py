"""The World Bank projects API's contract, against the set recorded on 2026-09-12.

Most of this file is about one hazard, and it is a worse relative of the one
`test_worldbank.py` guards. That API ignores parameters it does not recognise; this
one ignores `countrycode` and `status` - both of which are real fields on every
returned row - while honouring `countrycode_exact` and `status_exact`. Dropping
one suffix returns a page that is correctly scoped on the other axis, so it does
not look wrong. The tests that corrupt a row's status, country or container type
are the point of the file; the rest check that the parser reads what is actually
in the fixture, including the seven projects with no named agency and the five
whose planned Board date has already gone past.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from monitor.connectors.worldbank_pipeline import (
    PIPELINE_STATUS,
    PROJECT_URL,
    ROWS,
    WorldBankPipelineConnector,
    country_query,
    covered_codes,
    parse_projects,
)
from monitor.models import Source
from monitor.normalise.codes import COUNTRY_NAMES
from monitor.normalise.worldbank_pipeline import (
    ADMIN_LEVEL,
    LANGUAGE,
    buyer,
    compose_body,
    map_notice,
    single_country,
)

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "worldbank_pipeline.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "worldbank_pipeline.yaml"

# The whole covered-country pipeline set on the day it was recorded.
RECORDED_PROJECTS = 42


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def wanted(source) -> frozenset[str]:
    return frozenset(source.covers)


@pytest.fixture(scope="module")
def rows(document, wanted) -> list[dict]:
    return parse_projects(document, countries=wanted)


def _broken(document: dict) -> dict:
    return json.loads(json.dumps(document))


def _first(document: dict) -> str:
    return next(iter(document["projects"]))


# --- the query the connector sends -------------------------------------------


def test_the_query_sends_iso_codes_not_the_banks_country_spellings(source):
    """The opposite of the notices API, which matches on "Gambia, The"."""
    query = country_query(source.covers)

    assert query.count("^") == len(source.covers) - 1
    assert set(query.split("^")) == set(source.covers)
    assert "Gambia, The" not in query


def test_the_request_uses_the_exact_suffixes_that_actually_filter(source):
    """countrycode and status are accepted, ignored, and return all 28,113 rows."""
    params = WorldBankPipelineConnector(source, ["48"]).params()

    assert params["countrycode_exact"] == country_query(source.covers)
    assert params["status_exact"] == PIPELINE_STATUS
    assert "countrycode" not in params
    assert "status" not in params


def test_the_request_asks_for_every_field(source):
    """Without fl=* a row carries 15 fields and none of status, countrycode or pdo."""
    params = WorldBankPipelineConnector(source, ["48"]).params()

    assert params["fl"] == "*"
    assert params["rows"] == ROWS


def test_the_request_asks_for_no_ordering(source):
    """The whole set is read, so nothing depends on srt, which is ignored silently."""
    params = WorldBankPipelineConnector(source, ["48"]).params()

    assert "srt" not in params
    assert "os" not in params


# --- the response the connector got back --------------------------------------


def test_the_recorded_set_is_the_whole_pipeline(document, rows, source):
    assert len(rows) == RECORDED_PROJECTS
    assert int(document["total"]) == len(rows)
    assert source.expected_min <= len(rows) <= source.expected_max


def test_a_renamed_container_raises(document, wanted):
    with pytest.raises(ValueError, match="projects"):
        parse_projects({"results": document["projects"]}, countries=wanted)


def test_an_array_container_raises(document, wanted):
    """The notices API returns an array; iterating a dict yields its keys instead."""
    with pytest.raises(ValueError, match="not the id-keyed object"):
        parse_projects({"total": "42", "projects": list(document["projects"].values())}, countries=wanted)


def test_a_renamed_field_raises(document, wanted):
    broken = _broken(document)
    for row in broken["projects"].values():
        row["development_objective"] = row.pop("pdo")

    with pytest.raises(ValueError, match="pdo"):
        parse_projects(broken, countries=wanted)


def test_a_result_set_larger_than_one_request_raises(document, wanted):
    """Growth past 1,000 must stop the run, not read a silent fraction of it."""
    broken = dict(document, total="1200")

    with pytest.raises(ValueError, match="outgrown"):
        parse_projects(broken, countries=wanted)


def test_a_status_that_was_not_asked_for_raises(document, wanted):
    """The measured hazard: `status` without `_exact` returns every status there is.

    `countrycode_exact=GH&status=Pipeline` returned 340 Ghanaian projects on
    2026-09-12, of which the first three were Pipeline, Active and Active. A page
    scoped correctly on one axis and silently unscoped on the other reads as a
    country, not as a bug.
    """
    broken = _broken(document)
    broken["projects"][_first(broken)]["status"] = "Closed"

    with pytest.raises(ValueError, match="not asked for"):
        parse_projects(broken, countries=wanted)


def test_an_uncovered_country_raises(document, wanted):
    """Same hazard on the other parameter: 28,113 rows, most of them elsewhere."""
    broken = _broken(document)
    broken["projects"][_first(broken)]["countrycode"] = ["ID"]

    with pytest.raises(ValueError, match="none of which is covered"):
        parse_projects(broken, countries=wanted)


def test_every_recorded_project_is_pipeline_and_covered(rows, wanted):
    for raw in rows:
        assert raw["status"] == PIPELINE_STATUS
        assert covered_codes(raw, wanted)


def test_a_regional_project_naming_a_covered_country_passes_the_filter_check(rows, wanted):
    """`countrycode` is a list; the filter check asks only that one of them is ours."""
    assert covered_codes(dict(rows[0], countrycode=["GH", "BI"]), wanted) == ["GH"]


# --- the mapping ---------------------------------------------------------------


def test_every_recorded_project_maps(rows):
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.body.strip()
        assert mapped.external_id == raw["id"]
        assert mapped.url == PROJECT_URL.format(id=raw["id"])
        assert mapped.source_id == "worldbank_pipeline"


def test_the_country_is_a_single_iso_code_the_export_can_name(rows, source):
    assert ADMIN_LEVEL == source.admin_level

    covered = set(source.covers)
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.country == raw["countrycode"][0]
        assert mapped.country in covered
        # The export's Shipping Country column is a picklist of names, so a code
        # with no name in codes.py would fail after a reviewer had approved it.
        assert mapped.country in COUNTRY_NAMES
        assert mapped.admin_level == "donor"


def test_a_project_naming_two_countries_raises(rows):
    """A Notice carries one country; which one a regional operation is needs a person."""
    with pytest.raises(ValueError, match="countrycode"):
        single_country(dict(rows[0], countrycode=["GH", "NG"]))

    with pytest.raises(ValueError, match="countrycode"):
        single_country(dict(rows[0], countrycode=[]))


def test_the_language_is_english_and_nothing_is_translated(rows):
    """The endpoint serves one edition, ignores apilang and states no language."""
    for raw in rows:
        mapped = map_notice(raw)

        assert mapped.notice.language == LANGUAGE == "en"
        assert mapped.notice.language_confidence == 1.0
        assert mapped.title_en == ""
        assert mapped.body_en == ""


def test_the_buyer_is_the_implementing_agency(rows):
    """35 of the 42 name one. `borrower` is the sovereign and is a different thing."""
    named = [raw for raw in rows if (raw.get("impagency") or "").strip()]

    assert len(named) == 35
    for raw in named:
        assert map_notice(raw).notice.buyer == raw["impagency"].strip(" ,")


def test_an_unnamed_agency_is_left_empty_rather_than_filled_from_the_borrower(rows):
    """The 7 with no impagency are the same 7 with no borrower, so there is no choice."""
    unnamed = [raw for raw in rows if not (raw.get("impagency") or "").strip()]

    assert len(unnamed) == 7
    for raw in unnamed:
        assert not (raw.get("borrower") or "").strip()
        assert map_notice(raw).notice.buyer == ""


def test_a_trailing_comma_from_the_banks_data_entry_is_not_part_of_the_name(rows):
    """1 of the 35: "Public Company Republic of Srpska Motorways (PC RS Motorways),"."""
    assert buyer({"impagency": "Public Company Republic of Srpska Motorways (PC RS Motorways), "}) == (
        "Public Company Republic of Srpska Motorways (PC RS Motorways)"
    )
    assert [raw["id"] for raw in rows if (raw.get("impagency") or "").strip().endswith(",")]


def test_no_project_carries_a_cpv_code_or_a_procurement_value(rows):
    """The API states the whole operation's cost, which is not what this field means."""
    stated_financing = [raw for raw in rows if raw.get("totalamt") or raw.get("curr_total_commitment")]

    assert len(stated_financing) == 41
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.cpv_codes == []
        assert mapped.estimated_value_usd is None


# --- the dates, which is where documentation memory would have gone wrong ------


def test_the_published_date_is_the_disclosure_date(rows):
    """On all 42, and always in the past: 2020-03-04 to 2026-09-06."""
    for raw in rows:
        published = map_notice(raw).notice.published_at

        assert published is not None
        assert published == datetime.fromisoformat(raw["public_disclosure_date"]).replace(tzinfo=UTC)


def test_the_deadline_is_the_planned_board_approval_date(rows):
    """There is no RFP or EOI date in this API and none is invented.

    `closingdate` is the loan's closing date years after implementation starts, and
    it is on 32 of 42; the appraisal date inside `milestones` is on 20. The Board
    date is the only forward-looking date on every row.
    """
    for raw in rows:
        at = map_notice(raw).notice.deadline_at

        assert at is not None
        assert at.date() == datetime.fromisoformat(raw["boardapprovaldate"].replace("Z", "+00:00")).date()
        assert raw.get("closingdate") is None or at.date().isoformat() != raw["closingdate"]


def test_the_board_date_matches_the_milestone_it_is_flattened_from(rows):
    for raw in rows:
        assert raw["milestones"][0]["apprvl_date"] == raw["boardapprovaldate"]


def test_a_board_date_already_past_is_stored_as_published(rows):
    """5 of the 42 are stale entries whose planned date slipped without a status change.

    P173108, Nigeria's Beneficial Ownership Transparency project, was disclosed in
    March 2020 with a Board date of February 2020 and last updated in December 2022,
    and it is still marked Pipeline. Moving such a date to something more plausible
    is the invention rule 10 forbids; the reviewer sees the real one.
    """
    recorded_on = datetime(2026, 9, 12, tzinfo=UTC)
    past = [raw for raw in rows if map_notice(raw).notice.deadline_at < recorded_on]

    assert len(past) == 5
    stale = next(raw for raw in rows if raw["id"] == "P173108")
    assert map_notice(stale).notice.deadline_at.date().isoformat() == "2020-02-28"
    assert map_notice(stale).notice.published_at.date().isoformat() == "2020-03-04"


# --- the body -------------------------------------------------------------------


def test_the_body_says_this_is_a_pipeline_entry_and_not_a_tender(rows):
    """The scorer's rubric has a category for it and can only apply it if told."""
    for raw in rows:
        body = map_notice(raw).notice.body

        assert body.startswith(f"Project status: {PIPELINE_STATUS}\n")
        assert f"Stage reached: {raw['last_stage_reached_name'].strip()}" in body


def test_the_body_dates_and_the_deadline_cannot_disagree(rows):
    for raw in rows:
        notice = map_notice(raw).notice

        assert f"Planned Board approval: {notice.deadline_at.date().isoformat()}" in notice.body


def test_the_body_carries_the_banks_own_prose(rows):
    """pdo on all 42; project_abstract on 30, and the label is omitted on the other 12."""
    with_abstract = [raw for raw in rows if (raw.get("project_abstract") or "").strip()]

    assert len(with_abstract) == 30
    for raw in rows:
        body = map_notice(raw).notice.body
        abstract = (raw.get("project_abstract") or "").strip()

        assert raw["pdo"].strip() in body
        assert ("Project abstract:" in body) == bool(abstract)


def test_the_body_states_no_financing_figure(rows):
    """The same reason estimated_value_usd is None: it is the loan, not the contract."""
    for raw in rows:
        body = map_notice(raw).notice.body
        for field in ("totalamt", "curr_total_commitment", "lendprojectcost"):
            amount = raw.get(field)
            if amount and int(float(amount)) > 0:
                assert str(amount) not in body


def test_a_stage_advance_changes_the_content_hash(rows):
    """Intended: a project moving towards procurement is stored and scored again."""
    raw = rows[0]
    advanced = dict(raw, last_stage_reached_name="Begin Negotiation")

    assert map_notice(advanced).notice.content_hash != map_notice(raw).notice.content_hash


def test_an_unparseable_board_date_leaves_the_line_out_rather_than_stating_nothing(rows):
    """The body never states a date the deadline rule could not read."""
    body = compose_body(rows[0], None)

    assert "Planned Board approval" not in body
    assert body.startswith("Project status: Pipeline\n")
