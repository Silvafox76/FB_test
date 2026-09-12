"""The World Bank's contract, against the page recorded on 2026-09-11.

Most of this file is about one hazard: the API ignores query parameters it does
not recognise and answers with the whole 418,561-row corpus, so "the filter was
honoured" has to be asserted rather than assumed. The three tests that corrupt a
row's type, country or ordering are the point of the file; the rest check that the
parser reads what is actually in the fixture.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml

from monitor.connectors.worldbank import (
    BANK_COUNTRY_NAMES,
    NOTICE_TYPES,
    PAGE_SIZE,
    WorldBankConnector,
    country_names,
    country_query,
    notice_date,
    parse_notices,
    type_query,
    within_window,
)
from monitor.models import Source
from monitor.normalise.worldbank import ADMIN_LEVEL, clock, deadline, map_notice, strip_html

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "worldbank.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "worldbank.yaml"

# The fixture was recorded on 2026-09-11, so this is that run's own window.
RECORDED_ON = date(2026, 9, 11)


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def wanted(source) -> frozenset[str]:
    return frozenset(country_names(source.covers))


@pytest.fixture(scope="module")
def rows(document, wanted) -> list[dict]:
    return parse_notices(document, countries=wanted)


# --- the query the connector sends -------------------------------------------


def test_the_query_asks_for_the_banks_own_country_spellings(source):
    """Exact match on names that are neither ISO codes nor codes.py's names."""
    query = country_query(source.covers)

    assert query.count("^") == len(source.covers) - 1
    assert "Gambia, The" in query
    assert "Cote d'Ivoire" in query
    assert "Côte d'Ivoire" not in query


def test_an_iso_code_with_no_bank_spelling_raises(source):
    """Sending 18 of 19 countries would read as a quiet day in the nineteenth."""
    with pytest.raises(ValueError, match="RS"):
        country_query([*source.covers, "RS"])


def test_the_query_excludes_contract_awards():
    """308,619 of the 418,561 rows are tenders already decided."""
    assert "Contract Award" not in type_query()
    assert set(type_query().split("^")) == set(NOTICE_TYPES)


def test_the_window_is_the_two_days_before_the_run(source):
    connector = WorldBankConnector(source, ["48"])

    assert connector.cutoff(today=RECORDED_ON) == date(2026, 9, 9)


def test_the_request_carries_the_sort_the_window_cut_depends_on(source):
    params = WorldBankConnector(source, ["48"]).params(offset=0)

    assert params["srt"] == "noticedate"
    assert params["order"] == "desc"
    assert params["rows"] == PAGE_SIZE
    assert params["os"] == 0


# --- the response the connector got back --------------------------------------


def test_the_recorded_page_is_a_full_page(rows):
    assert len(rows) == PAGE_SIZE


def test_a_renamed_container_raises(document, wanted):
    with pytest.raises(ValueError, match="procnotices"):
        parse_notices({"results": document["procnotices"]}, countries=wanted)


def test_a_renamed_field_raises(document, wanted):
    broken = json.loads(json.dumps(document))
    for row in broken["procnotices"]:
        row["buyer_organization"] = row.pop("contact_organization")

    with pytest.raises(ValueError, match="contact_organization"):
        parse_notices(broken, countries=wanted)


def test_a_notice_type_that_carries_fewer_fields_does_not_raise(rows):
    """Nine General Procurement Notices carry no bid, reference or deadline.

    Requiring those would fail the whole run on a notice that is simply a
    different kind of notice, which is how a source becomes unhealthy for a reason
    nobody can act on.
    """
    sparse = [raw for raw in rows if "bid_description" not in raw]

    assert len(sparse) == 10


def test_an_unasked_for_notice_type_raises(document, wanted):
    """The measured hazard: an ignored notice_type means the corpus came back."""
    broken = json.loads(json.dumps(document))
    broken["procnotices"][7]["notice_type"] = "Contract Award"

    with pytest.raises(ValueError, match="not asked for"):
        parse_notices(broken, countries=wanted)


def test_an_uncovered_country_raises(document, wanted):
    """Same hazard on the other parameter: 418,561 rows, most of them elsewhere."""
    broken = json.loads(json.dumps(document))
    broken["procnotices"][3]["project_ctry_name"] = "Indonesia"

    with pytest.raises(ValueError, match="not covered"):
        parse_notices(broken, countries=wanted)


def test_an_unsorted_page_raises(document, wanted):
    """Without the sort the window cut under-reads, and it looks like a quiet day."""
    broken = json.loads(json.dumps(document))
    broken["procnotices"][0], broken["procnotices"][40] = (
        broken["procnotices"][40],
        broken["procnotices"][0],
    )

    with pytest.raises(ValueError, match="not sorted"):
        parse_notices(broken, countries=wanted)


def test_the_recorded_page_is_sorted_newest_first(rows):
    dates = [notice_date(row) for row in rows]

    assert dates == sorted(dates, reverse=True)


def test_the_window_cut_keeps_the_two_days_the_run_asked_for(rows, source):
    """13 of the 100 recorded rows; the rest are older than the lookback."""
    cutoff = WorldBankConnector(source, ["48"]).cutoff(today=RECORDED_ON)
    kept = within_window(rows, cutoff)

    assert len(kept) == 13
    assert all(notice_date(row) >= cutoff for row in kept)
    assert source.expected_min <= len(kept) <= source.expected_max


# --- the mapping ---------------------------------------------------------------


def test_every_recorded_notice_maps(rows):
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.title.strip()
        assert mapped.body.strip()
        assert mapped.external_id == raw["id"]
        assert mapped.url.endswith(raw["id"])
        assert mapped.buyer == raw["contact_organization"].strip()


def test_the_country_is_the_projects_and_the_admin_level_is_donor(rows, source):
    """A donor notice and the national notice for the same tender share a cluster."""
    assert ADMIN_LEVEL == source.admin_level

    covered = set(source.covers)
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.country in covered
        assert mapped.country == {v: k for k, v in BANK_COUNTRY_NAMES.items()}[raw["project_ctry_name"]]
        assert mapped.admin_level == "donor"


def test_the_language_name_becomes_a_two_letter_code(rows):
    """67 English, 31 French, 2 "Spanish; Castilian" in the recorded page."""
    languages = {map_notice(raw).notice.language for raw in rows}

    assert languages == {"en", "fr", "es"}


def test_an_unknown_language_name_raises(rows):
    broken = dict(rows[0], notice_lang_name="Klingon")

    with pytest.raises(ValueError, match="Klingon"):
        map_notice(broken)


def test_an_unknown_country_name_raises(rows):
    broken = dict(rows[0], project_ctry_name="Gambia")

    with pytest.raises(ValueError, match="Gambia"):
        map_notice(broken)


def test_no_notice_carries_an_english_rendering(rows):
    """Nothing here translates (rule 9); empty is not "the original was English"."""
    for raw in rows:
        mapped = map_notice(raw)

        assert mapped.title_en == ""
        assert mapped.body_en == ""


def test_the_body_is_the_html_stripped_to_text(rows):
    for raw in rows:
        body = map_notice(raw).notice.body

        assert "<p>" not in body
        assert "&nbsp;" not in body
        assert "\xa0" not in body


def test_a_non_breaking_space_does_not_survive_into_the_body():
    """A phrase written with one would not match the lexicon's ordinary spaces."""
    assert strip_html("<p>gestion&nbsp;des finances publiques</p>") == "gestion des finances publiques"


def test_the_published_date_is_the_notice_date(rows):
    for raw in rows:
        published = map_notice(raw).notice.published_at

        assert published == datetime.combine(notice_date(raw), datetime.min.time(), tzinfo=UTC)


# --- the deadline, which is the field documentation memory gets wrong ----------


def test_submission_date_is_the_arrival_date_and_not_the_deadline(rows):
    """It equals `noticedate` on all 100 recorded rows.

    This is the measurement behind the connector's central mapping decision: a
    deadline built from `submission_date` would be the publication date under
    another name, already past on the day the reviewer sees it, and it would look
    entirely plausible on the candidate page.
    """
    for raw in rows:
        assert datetime.fromisoformat(raw["submission_date"]).date() == notice_date(raw)


def test_the_deadline_is_the_deadline_pair(rows):
    with_deadline = [raw for raw in rows if raw.get("submission_deadline_date")]

    assert len(with_deadline) == 91
    for raw in with_deadline:
        at = map_notice(raw).notice.deadline_at

        assert at is not None
        assert at.date() == datetime.fromisoformat(raw["submission_deadline_date"]).date()


def test_a_deadline_on_the_notices_own_day_is_the_sources_data_not_a_parse(rows):
    """12 of the 91, each with a real clock time, on notices the Bank posted late.

    A reviewer will see candidates whose deadline has already passed. That is what
    the source published, and silently moving such a date is exactly the invention
    rule 10 forbids; the check here is only that they are a minority and that they
    still come from the deadline pair.
    """
    same_day = [
        raw
        for raw in rows
        if raw.get("submission_deadline_date")
        and datetime.fromisoformat(raw["submission_deadline_date"]).date() == notice_date(raw)
    ]

    assert len(same_day) == 12
    for raw in same_day:
        assert map_notice(raw).notice.deadline_at.date() == notice_date(raw)


def test_the_deadline_takes_the_stated_time_of_day(rows):
    stated = [raw for raw in rows if (raw.get("submission_deadline_time") or "") not in ("", "00:00")]

    assert stated
    for raw in stated:
        hour, minute = (int(part) for part in raw["submission_deadline_time"].split(":"))
        at = map_notice(raw).notice.deadline_at

        assert (at.hour, at.minute) == (hour, minute)


def test_midnight_is_read_as_no_time_stated_and_closes_the_day(rows):
    """4 of the 91 recorded pairs say 00:00, which is a data-entry default."""
    midnights = [raw for raw in rows if raw.get("submission_deadline_time") == "00:00"]

    assert midnights
    for raw in midnights:
        at = map_notice(raw).notice.deadline_at

        assert (at.hour, at.minute) == (23, 59)


def test_an_unreadable_clock_keeps_the_closing_date(rows):
    """The date parsed by rule; losing the whole deadline would cost a day of runway."""
    assert clock("half past ten") is None

    raw = dict(next(row for row in rows if row.get("submission_deadline_date")), submission_deadline_time="noon")
    at = deadline(raw)

    assert at is not None and (at.hour, at.minute) == (23, 59)


def test_a_general_procurement_notice_has_no_deadline_and_is_titled_by_its_project(rows):
    """A GPN announces a programme: no single bid, no single closing date."""
    notices = [raw for raw in rows if raw["notice_type"] == "General Procurement Notice"]

    assert notices
    for raw in notices:
        mapped = map_notice(raw).notice

        assert "bid_description" not in raw
        assert mapped.deadline_at is None
        assert mapped.title == raw["project_name"].strip()


def test_a_notice_that_names_a_bid_is_titled_by_it(rows):
    named = [raw for raw in rows if (raw.get("bid_description") or "").strip()]

    assert len(named) == 90
    for raw in named:
        assert map_notice(raw).notice.title == raw["bid_description"].strip()


def test_no_notice_carries_a_cpv_code_or_a_value(rows):
    """There are none in this source, so every notice goes to the lexicon stage."""
    for raw in rows:
        mapped = map_notice(raw).notice

        assert mapped.cpv_codes == []
        assert mapped.estimated_value_usd is None


def test_a_bad_notice_date_raises(rows):
    with pytest.raises(ValueError, match="noticedate"):
        notice_date(dict(rows[0], noticedate="2026-09-10"))


# --- the country the OR join was silently dropping -----------------------------


def test_a_name_with_an_apostrophe_is_sent_first_because_the_api_drops_it_otherwise(source):
    """Measured on the live API, 2026-09-12, and silent in both directions.

        Cote d'Ivoire alone      5,996
        Ghana alone              4,452
        Cote d'Ivoire^Ghana     10,448   both
        Ghana^Cote d'Ivoire      4,452   Ghana only

    It cost the pilot one of its thirteen West African countries on every run, and
    reported a healthy fetch while doing it. The connector's filter guard cannot
    catch this: it checks nothing UNWANTED comes back, and a country returning
    nothing looks exactly like a country having a quiet day.
    """
    terms = country_query(source.covers).split("^")

    assert "'" in terms[0], f"an apostrophe name must lead the join, got {terms[0]!r}"
    assert set(terms) == set(country_names(source.covers)), "reordering must not drop or add a country"
    assert len(terms) == len(source.covers)


def test_a_second_apostrophe_name_raises_rather_than_being_dropped_quietly(source):
    """Only one can be first. The second would repeat the original defect."""
    from monitor.connectors import worldbank

    names = dict(worldbank.BANK_COUNTRY_NAMES)
    names["ZZ"] = "Someone's Republic"

    with pytest.raises(ValueError, match="more than one covered country name contains an apostrophe"):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(worldbank, "BANK_COUNTRY_NAMES", names)
            country_query(list(source.covers) + ["ZZ"])
