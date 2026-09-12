"""EBRD's contract, against the pass recorded on 2026-09-12.

Most of this file is about two hazards, and they are opposites of the World Bank's.
There the API filtered and the danger was trusting a parameter it had ignored; here
there are no parameters at all, so every scope decision is the connector's own and
nothing else will catch it being wrong. The three tests that matter most are the one
asserting the rows are NOT sorted newest first - which is why this connector reads
the whole archive instead of walking down it - the one that makes a covered
country's name vanishing raise instead of reading as a quiet week, and the one that
shows the listing rendering the noon hour as midnight. The rest check that the
parsers read what is actually in the recording.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from monitor.connectors.ebrd import (
    DETAIL_CLOSING,
    DETAIL_DESCRIPTION,
    DETAIL_EXERCISE_NAME,
    DETAIL_NOTICE_TYPE,
    DETAIL_PROJECT_NAME,
    EBRD_COUNTRY_NAMES,
    LOOKBACK_DAYS,
    NOTICE_URL,
    EbrdConnector,
    cell_text,
    country_name,
    ebrd_names,
    in_scope,
    iso_timestamp,
    parse_detail,
    parse_listing,
    published_date,
)
from monitor.models import Source
from monitor.normalise.ebrd import ADMIN_LEVEL, LANGUAGE, country_alpha2, map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "ebrd.json"
# The listing is stored as the HTML it is rather than as a JSON string: 3.8 MB of
# escaped newlines inside ebrd.json took the pair over the 4 MB an added file may
# be, and the response is evidence that reads better as itself.
LISTING_FIXTURE = Path(__file__).parent / "fixtures" / "ebrd.html"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "ebrd.yaml"

# The fixture this file needs does not exist, and the reason is worth stating where a
# reader meets it rather than in a commit message.
#
# `ecepp.ebrd.com` served the recording pass on 2026-09-12 and then began resetting
# connections mid-exchange - four `ws_closed_mid_exchange` entries in the egress
# relay log, and a plain GET still answers `[Errno 104] Connection reset by peer`.
# Most likely rate limiting after that pass. It was tried twice and then left alone:
# rule 21 allows one polite pass per schedule and hammering a host that is refusing
# is the opposite of that.
#
# An earlier `ebrd.json` existed in the working tree and was deleted rather than
# committed. To be exact, because the first version of this note was not and a reader
# who went looking would have found nothing: it was never in git. It held real
# recorded data in the recorder's *previous* shape - the detail pages already parsed
# into dicts instead of stored as the HTML they arrived as - so 45 of the 56 cases
# below could not have run against it, and the detail parser could not have been
# tested at all. A fixture that cannot exercise the parser it exists for is worse than
# none, because it looks like coverage.
#
# This skips rather than fails, and it skips loudly: a skip is reported in every run
# summary, so the gap stays visible until `scripts/record_ebrd_fixture.py` can reach
# the host again. The source is `enabled: false` and absent from CONNECTORS
# (`monitor/fetch.py`), so nothing runs it in the meantime.
if not FIXTURE.exists() or not LISTING_FIXTURE.exists():
    pytest.skip(
        "EBRD fixture not recorded: ecepp.ebrd.com is resetting connections "
        "(Errno 104) after serving the first pass. Re-run "
        "scripts/record_ebrd_fixture.py when the host answers again.",
        allow_module_level=True,
    )

# The pass was recorded on 2026-09-12, so this is that run's own window.
RECORDED_ON = date(2026, 9, 12)
CUTOFF = RECORDED_ON - timedelta(days=LOOKBACK_DAYS)

# What the archive held that day. Asserted rather than computed so a re-recorded
# fixture that lost half the table fails here and not somewhere subtler.
ARCHIVE_ROWS = 4050
IN_SCOPE = 11

# The two rows that prove the title's country prefix is prose a client types and
# not a field. See monitor/connectors/ebrd.py.
MISTYPED_PREFIX = "BA: "
TEST_NOTICE_PREFIX = "United Kingdom: MDB PIA test"


@pytest.fixture(scope="module")
def document() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def listing_html(document) -> str:
    assert document["listing_file"] == LISTING_FIXTURE.name
    return LISTING_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source() -> Source:
    return Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def rows(listing_html, source) -> list[dict]:
    return parse_listing(
        listing_html,
        covers=source.covers,
        excluded_types=source.exclude_notice_types,
    )


@pytest.fixture(scope="module")
def wanted(rows, source) -> list[dict]:
    return in_scope(rows, cutoff=CUTOFF, excluded_types=source.exclude_notice_types)


@pytest.fixture(scope="module")
def details(document) -> dict:
    """Every notice page the run fetched, parsed from the recorded HTML."""
    return {notice_id: parse_detail(html, notice_id=notice_id) for notice_id, html in document["details_html"].items()}


@pytest.fixture(scope="module")
def gpn(document) -> dict:
    """The one page from outside the window: a General Procurement Notice."""
    recorded = document["general_procurement_notice"]
    return parse_detail(recorded["html"], notice_id=recorded["notice_id"])


# --- what the connector asks for, which is nothing ----------------------------


def test_the_listing_url_carries_no_query_parameters(source):
    """Measured: noticeType, country, keyword, currentState and pageSize are all
    ignored and return the same 4,050 rows, and so does a POST to the form's own
    action. A parameter in the registry entry would imply one of them works."""
    assert "?" not in source.list_url
    assert source.list_url.endswith("/delta/noticeSearchResults.html")


def test_the_window_is_the_thirty_days_before_the_run(source):
    connector = EbrdConnector(source, ["48"])

    assert connector.cutoff(today=RECORDED_ON) == CUTOFF
    assert LOOKBACK_DAYS == 30


def test_one_request_returns_the_whole_archive(rows):
    """Eleven years in one response, because the portal offers no other shape."""
    dates = [published_date(row) for row in rows]

    assert len(rows) == ARCHIVE_ROWS
    assert min(dates) == date(2015, 7, 27)
    assert max(dates) == date(2026, 9, 11)


# --- hazard one: the rows are not sorted ---------------------------------------


def test_the_rows_are_not_sorted_newest_first(rows):
    """The hazard this connector is shaped around, asserted so nobody 'optimises'
    the full scan into a walk that stops at the first out-of-window row.

    Ten adjacent pairs are out of order by whole days and one of them straddles the
    30-day cutoff: a walk would have stopped there and dropped the rest of the
    month while reporting a healthy run.
    """
    dates = [published_date(row) for row in rows]
    out_of_order = [i for i in range(1, len(dates)) if dates[i] > dates[i - 1]]

    assert dates != sorted(dates, reverse=True)
    assert len(out_of_order) == 10
    assert any(dates[i - 1] < CUTOFF <= dates[i] for i in out_of_order)


def test_the_scan_keeps_an_in_window_row_that_a_sorted_walk_would_have_missed(rows, wanted):
    """The concrete one: row 31 is 2026-08-07 and row 32 is 2026-08-17."""
    dates = [published_date(row) for row in rows]
    straddle = next(i for i in range(1, len(dates)) if dates[i - 1] < CUTOFF <= dates[i])

    assert dates[straddle - 1] < CUTOFF <= dates[straddle]
    assert max(published_date(row) for row in wanted) == date(2026, 9, 11)
    assert min(published_date(row) for row in wanted) >= CUTOFF


# --- hazard two: the scope is the connector's own ------------------------------


def test_every_covered_country_still_appears_in_the_archive(rows, source):
    """Eleven years of archive is what makes this checkable at all."""
    archive = "\n".join(row["metadata"] for row in rows)

    for code in source.covers:
        assert any(f", {name}," in archive for name in ebrd_names(code)), code


def test_a_covered_country_whose_name_changed_raises(listing_html, source):
    """A renamed country returns nothing, and nothing looks exactly like a quiet
    week. Over the whole archive it cannot be one, so it raises instead."""
    renamed = listing_html.replace(", Kosovo,", ", Kosova,")

    with pytest.raises(ValueError, match="Kosovo"):
        parse_listing(renamed, covers=source.covers, excluded_types=source.exclude_notice_types)


def test_an_iso_code_with_no_ebrd_spelling_raises(listing_html, source):
    """Scoping on six of seven countries would read as a quiet week in the seventh."""
    with pytest.raises(ValueError, match="'GH'"):
        parse_listing(
            listing_html,
            covers=[*source.covers, "GH"],
            excluded_types=source.exclude_notice_types,
        )


def test_an_excluded_notice_type_no_notice_carries_raises(listing_html, source):
    """A typo in exclude_notice_types would exclude nothing, in silence."""
    with pytest.raises(ValueError, match="Contract Awards Notice"):
        parse_listing(
            listing_html,
            covers=source.covers,
            excluded_types=["Contract Awards Notice"],
        )


def test_the_exclusion_drops_tenders_that_are_already_decided(rows, source):
    """1,109 Contract Award Notices and 42 Notices of Prequalified Participants,
    28% of the archive, and none is an opportunity anyone can still respond to."""
    excluded = {row["notice_type"] for row in rows} & set(source.exclude_notice_types)
    kept = in_scope(rows, cutoff=CUTOFF, excluded_types=source.exclude_notice_types)
    unfiltered = in_scope(rows, cutoff=CUTOFF, excluded_types=[])

    assert excluded == set(source.exclude_notice_types)
    assert sum(1 for row in rows if row["notice_type"] == "Contract Award Notice") == 1109
    assert {row["notice_type"] for row in kept}.isdisjoint(source.exclude_notice_types)
    assert len(unfiltered) > len(kept)


# --- the country comes from the metadata cell, not the title -------------------


def test_the_scope_reads_the_metadata_cell_and_not_the_title_prefix(rows):
    """One row is titled "BA: ..." where its metadata says Bosnia And Herzegovina.
    Scoping on the prefix would have dropped it as an unknown country."""
    mistyped = next(row for row in rows if row["title"].startswith(MISTYPED_PREFIX))

    assert mistyped["country_name"] == "Bosnia And Herzegovina"
    assert ", Bosnia And Herzegovina," in mistyped["metadata"]


def test_a_test_notice_left_in_the_live_archive_is_out_of_scope(rows):
    """ "United Kingdom: MDB PIA test", project id N/A, client PPAD."""
    left_behind = next(row for row in rows if row["title"] == TEST_NOTICE_PREFIX)

    assert left_behind["country_name"] is None
    assert ", United Kingdom," in left_behind["metadata"]


def test_a_historical_country_spelling_still_resolves(rows):
    """47 titles and 42 metadata cells still say Macedonia FYR or FYR Macedonia."""
    historical = [row for row in rows if row["country_name"] in ("Macedonia FYR", "FYR Macedonia")]

    assert len(historical) == 42
    assert {country_alpha2(row["country_name"]) for row in historical} == {"MK"}
    assert all(published_date(row) < CUTOFF for row in historical)


def test_a_metadata_cell_naming_two_covered_countries_raises():
    """Then the membership test is matching a project or client name, not a field."""
    with pytest.raises(ValueError, match="more than one covered country"):
        country_name("[A project, 1, Ukraine, Works, A client, Kosovo, Transport]", covers=["UA", "XK"])


def test_no_row_in_the_archive_names_two_covered_countries(rows):
    """The reason the membership test is sound rather than lucky."""
    for row in rows:
        matched = [name for code in EBRD_COUNTRY_NAMES for name in ebrd_names(code) if f", {name}," in row["metadata"]]
        assert len(matched) <= 1, row["metadata"]


def test_the_nineteen_empty_metadata_cells_are_all_older_than_the_window(rows):
    """They state no country. All nineteen are pre-2023, so the window never reads
    one - and one of them was Ukrainian, which is why an in-window one raises."""
    blank = [row for row in rows if row["metadata"] == "[]"]

    assert len(blank) == 19
    assert max(published_date(row) for row in blank) == date(2022, 12, 6)
    assert all(published_date(row) < CUTOFF for row in blank)


def test_an_empty_metadata_cell_inside_the_window_raises(rows, source):
    """Dropping it would be silent, and one of the historical nineteen was covered."""
    broken = [dict(row) for row in rows]
    inside = next(row for row in broken if published_date(row) >= CUTOFF)
    inside["metadata"] = "[]"

    with pytest.raises(ValueError, match="empty metadata cell"):
        in_scope(broken, cutoff=CUTOFF, excluded_types=source.exclude_notice_types)


# --- the listing's shape -------------------------------------------------------


def test_a_renamed_results_table_raises(listing_html, source):
    """Without the container check this would be an archive of zero rows, and the
    covered-country guard would report it as seven countries having vanished."""
    renamed = listing_html.replace('id="noticeResultsTable"', 'id="searchResults"')

    with pytest.raises(ValueError, match="noticeResultsTable"):
        parse_listing(renamed, covers=source.covers, excluded_types=source.exclude_notice_types)


def test_a_row_that_lost_a_column_raises(listing_html, source):
    """DataTables hides four of the ten columns; the server sends all ten, and the
    country is in the last of them."""
    lost = listing_html.replace(
        "<td>[Nigeria Sovereign Fibre (Bridge) Project", "<span>[Nigeria Sovereign Fibre (Bridge) Project", 1
    )

    with pytest.raises(ValueError, match="9 cells, not 10"):
        parse_listing(lost, covers=source.covers, excluded_types=source.exclude_notice_types)


def test_an_unparseable_publication_date_raises(rows):
    with pytest.raises(ValueError, match="not dd/mm/yyyy"):
        published_date(dict(rows[0], published_date="2026-09-11"))


def test_the_in_scope_set_is_what_the_run_fetched(wanted, document, source):
    assert len(wanted) == IN_SCOPE
    assert {row["notice_id"] for row in wanted} == set(document["details_html"])
    assert source.expected_min <= len(wanted) <= source.expected_max


def test_the_run_saw_the_geography_the_pilot_pays_for(wanted):
    """Six countries, five of them at config/thresholds.yaml's 0.8 weight."""
    countries = {country_alpha2(row["country_name"]) for row in wanted}

    assert countries == {"UA", "BA", "MK", "ME", "XK", "NG"}


# --- what the connector actually requests --------------------------------------


class _FixtureClient:
    """A stand-in httpx client serving the recorded responses, recording each URL."""

    def __init__(self, document: dict, listing_html: str):
        self._pages = {document["listing_url"]: listing_html} | {
            NOTICE_URL.format(notice_id=notice_id): html for notice_id, html in document["details_html"].items()
        }
        self.requested: list[str] = []

    def get(self, url, **kwargs):
        assert not kwargs, f"the connector sent {kwargs} with a request; this source has no parameters"
        self.requested.append(url)
        if url not in self._pages:
            raise AssertionError(f"the connector requested {url}, which the fixture does not hold")
        return _StubResponse(self._pages[url])


class _StubResponse:
    def __init__(self, text: str):
        self.text = text

    def raise_for_status(self):
        return None


def test_a_run_requests_the_listing_once_and_one_page_per_notice(document, listing_html, source):
    connector = EbrdConnector(source, ["48"])
    connector.cutoff = lambda today=None: CUTOFF
    client = _FixtureClient(document, listing_html)

    raw_notices = connector.fetch_raw(client)

    assert client.requested[0] == source.list_url
    assert len(client.requested) == 1 + IN_SCOPE
    assert len(raw_notices) == IN_SCOPE
    assert all(raw.mime == "application/json" for raw in raw_notices)
    assert all(raw.source_id == "ebrd" for raw in raw_notices)


def test_every_notice_a_run_yields_maps(document, listing_html, source):
    connector = EbrdConnector(source, ["48"])
    connector.cutoff = lambda today=None: CUTOFF

    for raw in connector.fetch_raw(_FixtureClient(document, listing_html)):
        mapped = map_notice(json.loads(raw.payload)).notice

        assert mapped.title.strip()
        assert mapped.url == raw.url
        assert mapped.external_id in document["details_html"]


def test_a_notice_page_disagreeing_with_the_listing_on_country_raises(document, listing_html, source):
    """Nothing else verifies this connector's own scope, so the notice page does."""
    tampered = json.loads(json.dumps(document))
    notice_id = next(iter(tampered["details_html"]))
    tampered["details_html"][notice_id] = (
        tampered["details_html"][notice_id]
        .replace("<td>Ukraine</td>", "<td>Kosovo</td>")
        .replace("<td>Nigeria</td>", "<td>Kosovo</td>")
    )

    connector = EbrdConnector(source, ["48"])
    connector.cutoff = lambda today=None: CUTOFF

    with pytest.raises(ValueError, match="on its own page but"):
        connector.fetch_raw(_FixtureClient(tampered, listing_html))


def test_a_notice_page_disagreeing_with_the_listing_on_notice_type_raises(document, listing_html, source):
    """exclude_notice_types was applied to the listing's string, not this one.

    The tamper is a regex rather than a literal replace, and that is the whole lesson
    of recording the fixture. The page does not contain the string "Shortlist Notice":
    it contains `Shortlist` followed by eighteen spaces and `Notice`, because the
    template indents inside its text nodes. That is exactly what `INLINE_SPACE` in the
    connector exists to collapse, and a literal replace silently matched nothing — so
    this test passed no notice through, tampered with nothing, and asserted a raise
    that could never happen. It never ran until the fixture existed to run it against.
    """
    tampered = json.loads(json.dumps(document))
    shortlist = re.compile(r"Shortlist\s+Notice")
    tampered_count = 0
    for notice_id, html in tampered["details_html"].items():
        swapped, hits = shortlist.subn("Contract Award Notice", html)
        tampered["details_html"][notice_id] = swapped
        tampered_count += hits
    assert tampered_count, "the fixture no longer carries a Shortlist Notice to tamper with"

    connector = EbrdConnector(source, ["48"])
    connector.cutoff = lambda today=None: CUTOFF

    with pytest.raises(ValueError, match="on its own page but a"):
        connector.fetch_raw(_FixtureClient(tampered, listing_html))


# --- the notice page ----------------------------------------------------------


def test_every_recorded_notice_page_parses(details):
    assert len(details) == IN_SCOPE
    for notice_id, detail in details.items():
        assert detail["Country"]
        assert detail["Client Name"]
        assert detail[DETAIL_NOTICE_TYPE]
        assert detail["Publication Date"]
        assert detail[DETAIL_PROJECT_NAME], notice_id


def test_a_page_with_no_field_table_raises(document):
    notice_id, html = next(iter(document["details_html"].items()))

    with pytest.raises(ValueError, match="oppoverviewtable"):
        parse_detail(html.replace('id="oppoverviewtable"', 'id="overview"'), notice_id=notice_id)


def test_a_page_missing_a_required_label_raises(document):
    notice_id, html = next(iter(document["details_html"].items()))

    with pytest.raises(ValueError, match="Client Name"):
        parse_detail(html.replace("<strong>Client Name:</strong>", "<strong>Buyer:</strong>"), notice_id=notice_id)


def test_a_page_stating_one_label_twice_raises(document):
    """Which value is the notice's would be undecidable."""
    notice_id, html = next(iter(document["details_html"].items()))
    doubled = html.replace(
        "<td><strong>Country:</strong></td>",
        "<td><strong>Country:</strong></td><td>Elsewhere</td></tr><tr><td><strong>Country:</strong></td>",
        1,
    )

    with pytest.raises(ValueError, match="twice"):
        parse_detail(doubled, notice_id=notice_id)


def test_a_notice_type_is_read_as_a_reader_sees_it(details):
    """The page's templates indent inside text nodes, so the raw cell reads
    "Invitation<20 spaces>For<20 spaces>Tenders". A browser collapses it."""
    types = {detail[DETAIL_NOTICE_TYPE] for detail in details.values()}

    assert types == {
        "Invitation For Tenders Single",
        "Invitation For Tenders Two Stage",
        "Invitation For Prequalification",
        "Shortlist Notice",
    }
    assert all("  " not in value for value in types)


def test_a_description_keeps_the_line_breaks_its_client_typed(details):
    """Collapsing them would run a notice's lots into one line."""
    multiline = [
        detail[DETAIL_DESCRIPTION] for detail in details.values() if "\n" in detail.get(DETAIL_DESCRIPTION, "")
    ]

    # Five, measured against the recorded fixture on 2026-09-12. This asserted four
    # before the fixture existed, which was a guess rather than a measurement — the
    # module-level skip meant nothing in this file had ever executed. The property
    # that matters is the second assertion; the count is here to notice a re-record
    # changing the corpus underneath it.
    assert len(multiline) == 5
    assert all("  " not in line for body in multiline for line in body.split("\n"))


def test_a_non_breaking_space_does_not_survive_into_a_cell():
    """A phrase written with one would not match the lexicon's ordinary spaces."""
    from selectolax.parser import HTMLParser

    # Wrapped in a table, because selectolax follows the HTML parsing rules and
    # DISCARDS a bare <td> parsed outside table context — `css_first("td")` returned
    # None and the failure read as a connector bug rather than a malformed fixture.
    cell = HTMLParser("<table><tr><td>public&nbsp;financial   management</td></tr></table>").css_first("td")

    assert cell_text(cell) == "public financial management"


# --- the mapping --------------------------------------------------------------


def test_the_admin_level_is_the_registrys_and_it_is_donor(source):
    """A donor notice and the national notice for the same tender share a cluster."""
    assert ADMIN_LEVEL == source.admin_level == "donor"


def test_every_notice_maps_with_a_covered_iso2_country(details, rows, source):
    listing = {row["notice_id"]: row for row in rows}

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail}).notice

        assert mapped.country in set(source.covers)
        assert mapped.country == country_alpha2(detail["Country"])
        assert mapped.buyer == detail["Client Name"]
        assert mapped.external_id == notice_id
        assert mapped.url == NOTICE_URL.format(notice_id=notice_id)


def test_the_language_is_english_and_the_portal_states_it(details, rows):
    """<html lang="en_GB"> on every page, and ECEPP permits no other language."""
    listing = {row["notice_id"]: row for row in rows}

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail}).notice

        assert mapped.language == LANGUAGE == "en"
        assert mapped.language_confidence == 1.0


def test_no_notice_carries_a_separate_english_rendering(details, rows):
    """Nothing here translates (rule 9), and the original already is the English."""
    listing = {row["notice_id"]: row for row in rows}

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail})

        assert mapped.title_en == ""
        assert mapped.body_en == ""


def test_no_notice_carries_a_cpv_code_or_a_stated_value(details, rows):
    """So every notice reaches config/lexicon_en.yaml, which is free."""
    listing = {row["notice_id"]: row for row in rows}

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail}).notice

        assert mapped.cpv_codes == []
        assert mapped.estimated_value is None
        assert mapped.value_currency is None


def test_the_title_is_the_exercise_where_the_notice_names_one(details, rows):
    listing = {row["notice_id"]: row for row in rows}

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail}).notice

        assert mapped.title == detail[DETAIL_EXERCISE_NAME]


def test_a_general_procurement_notice_titles_from_its_project_and_states_no_deadline(gpn, document):
    """715 of the 4,050 archived notices are GPNs and none fell in the window.

    A GPN announces a project's procurement programme before any contract is
    tendered, so it carries no exercise, no description, no method, no issue date
    and no closing date. Requiring any of those would have failed this source on the
    first GPN to arrive - 18% of what it publishes.
    """
    recorded = document["general_procurement_notice"]
    mapped = map_notice({"listing": {"notice_id": recorded["notice_id"]}, "detail": gpn}).notice

    assert gpn[DETAIL_NOTICE_TYPE] == "General Procurement Notice"
    assert DETAIL_EXERCISE_NAME not in gpn
    assert DETAIL_DESCRIPTION not in gpn
    assert DETAIL_CLOSING not in gpn
    assert mapped.title == gpn[DETAIL_PROJECT_NAME]
    assert mapped.deadline_at is None
    assert mapped.body == ""


def test_the_two_notices_with_no_description_map_with_an_empty_body(details, rows):
    """Both Shortlist Notices. The title is then what the lexicon and scorer see."""
    listing = {row["notice_id"]: row for row in rows}
    bodiless = [
        map_notice({"listing": listing[notice_id], "detail": detail}).notice
        for notice_id, detail in details.items()
        if DETAIL_DESCRIPTION not in detail
    ]

    assert len(bodiless) == 2
    assert {notice.body for notice in bodiless} == {""}
    assert all(notice.title.strip() for notice in bodiless)


# --- the deadline, which this source publishes in the past on purpose ----------


def test_a_deadline_already_past_is_stored_as_published(details, rows):
    """Both Shortlist Notices close before the day they were published on: one
    published 21/08/2026 closed 26/02/2026, the other published 11/09/2026 closed
    09/06/2026. That is what the page says and the competition really is over
    (rule 10). Moving such a date to something plausible is the invention rule 10
    exists to forbid."""
    listing = {row["notice_id"]: row for row in rows}
    stale = [
        map_notice({"listing": listing[notice_id], "detail": detail}).notice
        for notice_id, detail in details.items()
        if detail[DETAIL_NOTICE_TYPE] == "Shortlist Notice"
    ]

    assert len(stale) == 2
    for notice in stale:
        assert notice.deadline_at is not None
        assert notice.deadline_at < notice.published_at


def test_the_listing_hides_those_deadlines_behind_n_a(rows, details):
    """The listing shows "N/A" for any information-only notice while the page states
    the real closing date. One more reason nothing is mapped from the listing."""
    for row in rows:
        if row["notice_id"] in details and row["state"] == "Information Only":
            assert row["closing_at"] == "N/A"
            assert details[row["notice_id"]][DETAIL_CLOSING]


def test_a_midnight_closing_time_becomes_the_end_of_that_day(details, rows):
    """00:00 is this portal's data-entry default for "no time stated". Read
    literally it would shut a tender before its closing day began, and
    monitor/normalise/dates.py already pushes a bare date to the end of its day."""
    listing = {row["notice_id"]: row for row in rows}
    midnight = [
        map_notice({"listing": listing[notice_id], "detail": detail}).notice
        for notice_id, detail in details.items()
        if detail.get(DETAIL_CLOSING, "").endswith("00:00")
    ]

    assert len(midnight) == 2
    for notice in midnight:
        assert (notice.deadline_at.hour, notice.deadline_at.minute) == (23, 59)


def test_a_stated_time_of_day_is_kept(details, rows):
    listing = {row["notice_id"]: row for row in rows}
    noon = next(notice_id for notice_id, detail in details.items() if detail.get(DETAIL_CLOSING, "").endswith("12:00"))

    mapped = map_notice({"listing": listing[noon], "detail": details[noon]}).notice

    assert (mapped.deadline_at.hour, mapped.deadline_at.minute) == (12, 0)
    assert mapped.deadline_at.tzinfo == UTC


def test_a_date_is_read_day_first():
    """dd/mm/yyyy, which monitor/normalise/dates.py refuses on purpose. Over the
    4,050 archived rows the first number runs 1 to 31 and the second never exceeds
    12, and each row's own sort key spells the instant out as yyyymmddHHMM."""
    assert iso_timestamp("11/09/2026 10:22", notice_id="x", label="d") == "2026-09-11T10:22:00"
    assert iso_timestamp("30/12/2026", notice_id="x", label="d") == "2026-12-30"
    assert iso_timestamp("", notice_id="x", label="d") is None


def test_an_impossible_date_raises():
    with pytest.raises(ValueError, match="day is out of range"):
        iso_timestamp("31/02/2026 10:00", notice_id="x", label="d")


def test_a_date_in_another_format_raises():
    with pytest.raises(ValueError, match="not dd/mm/yyyy"):
        iso_timestamp("2026-09-11T10:22", notice_id="x", label="d")


# --- the publication date, and the listing's broken clock ----------------------


def test_the_published_date_is_the_publication_date_and_not_the_issue_date(details, rows):
    """They differ on 8 of the 11, by one day on three and by 235 days on a
    Shortlist Notice whose exercise was issued the previous December. Three of the
    eight are issued after the notice announcing them."""
    listing = {row["notice_id"]: row for row in rows}
    differing = 0

    for notice_id, detail in details.items():
        mapped = map_notice({"listing": listing[notice_id], "detail": detail}).notice
        day, month, year = detail["Publication Date"].split()[0].split("/")

        assert mapped.published_at.date() == date(int(year), int(month), int(day))
        differing += detail["Issue Date"].split()[0] != detail["Publication Date"].split()[0]

    assert differing == 8


def test_the_listing_renders_the_noon_hour_as_midnight(rows, details):
    """The measured reason `published_at` comes from the notice page.

    The hour 12 appears in none of the 4,050 timestamps the listing renders, while
    00 appears 444 times against 8 at 01:00 and 9 at 23:00, with the neighbouring
    hours 11 and 13 busy at 438 and 377. One recorded notice shows it directly: the
    listing says 00:18 and its own page says 12:18. Only the time is affected, which
    is why the window cut may read the listing's date and does.
    """
    hours = [row["published_at"].split()[1][:2] for row in rows]

    assert "12" not in set(hours)
    assert hours.count("00") == 444
    assert hours.count("11") == 438 and hours.count("13") == 377

    disagreeing = [
        notice_id
        for notice_id, detail in details.items()
        if detail["Publication Date"]
        != next(row["published_at"].split("\n")[0] for row in rows if row["notice_id"] == notice_id)
    ]
    assert len(disagreeing) == 1
    assert details[disagreeing[0]]["Publication Date"].endswith("12:18")


# --- rule 9: the notice is stored as published ---------------------------------


def test_a_double_encoded_title_is_stored_as_published(details, rows):
    """Two of the 4,050 titles carry a Cyrillic tender number the portal
    double-encoded, and one is in this window. Repairing it would be guessing at
    bytes the publisher got wrong, and the page shows the same thing (rule 9)."""
    listing = {row["notice_id"]: row for row in rows}
    mojibake = [
        map_notice({"listing": listing[notice_id], "detail": detail}).notice
        for notice_id, detail in details.items()
        if any(0x80 <= ord(character) <= 0x9F for character in detail[DETAIL_EXERCISE_NAME])
    ]

    assert len(mojibake) == 1
    assert mojibake[0].title.startswith("Tender No. Ð")


def test_an_unknown_country_name_raises():
    with pytest.raises(ValueError, match="Serbia"):
        country_alpha2("Serbia")


def test_a_notice_with_neither_an_exercise_nor_a_project_raises(details, rows):
    listing = {row["notice_id"]: row for row in rows}
    notice_id, detail = next(iter(details.items()))
    stripped = {
        label: value for label, value in detail.items() if label not in (DETAIL_EXERCISE_NAME, DETAIL_PROJECT_NAME)
    }

    with pytest.raises(ValueError, match="states neither"):
        map_notice({"listing": listing[notice_id], "detail": stripped})


# --- the registry and the mapper agree ----------------------------------------


def test_the_registry_entry_is_enabled_and_wired(source):
    """Enabled on 2026-09-12, and this test changed with it rather than being deleted.

    It read `assert source.enabled is False`, with a docstring saying the orchestrator
    would flip it after the first live fetch. That is how a test quietly becomes a
    record of the day it was written: the live fetch happened, the source was enabled,
    and an assertion that had been describing a temporary state started failing as
    though something had broken.

    What is worth asserting instead is the pair that has to stay true together. A
    source is enabled in the registry AND wired into `monitor/fetch.py`'s CONNECTORS,
    or it is neither: enabled without a connector raises at fetch time, and wired
    without being enabled means nothing ever runs it.
    `tests/unit/test_fetch.py` asserts the whole registry keeps that property; this
    asserts it for the one source this file is about.
    """
    from monitor.fetch import CONNECTORS

    assert source.enabled is True
    assert source.id in CONNECTORS
    assert source.tos_status == "reviewed_ok"
    assert source.id == "ebrd"
    assert source.country == "multi"


def test_the_schedule_is_daily_because_the_host_refuses_a_second_pass(source):
    """The condition this source was enabled on, asserted so it cannot quietly change.

    EBRD was enabled once earlier on 2026-09-12 and reverted within the hour. The
    connector was never the problem: `fetch()` ignored `Source.schedule` while the
    scheduler woke hourly, so wiring a source whose host serves one pass and then
    resets meant asking it twenty-four times a day. `monitor/schedule.py` is what
    made this safe, and a schedule of anything sub-daily here would undo it without
    any other test noticing.
    """
    from monitor.schedule import parse

    cron = parse(source.schedule)

    assert source.schedule == "30 10 * * *"
    assert cron.days_of_week == frozenset(range(7)), "daily, and once a day"


def test_the_covered_codes_all_have_an_ebrd_spelling(source):
    assert set(source.covers) == set(EBRD_COUNTRY_NAMES)
    assert all(ebrd_names(code) for code in source.covers)


def test_the_recording_is_the_day_the_window_is_measured_from(document):
    assert document["recorded_on"] == RECORDED_ON.isoformat()
    assert datetime.fromisoformat(document["recorded_on"]).date() == RECORDED_ON
