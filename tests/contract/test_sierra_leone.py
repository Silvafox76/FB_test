"""Sierra Leone's NPPA Bid Opportunities contract, against the page recorded on
2026-09-12.

The whole source is one WordPress page and one TablePress table, so most of what
can go wrong here is the table changing shape under an unattended run: a renamed
container, a shifted column, a re-platformed page under another locale, or a
table found empty. Rule 4 says a selector matching nothing must raise rather than
report a healthy zero-item run, and that is what most of this file checks -
against a deliberately altered copy of the real fixture, never against the
fixture itself edited to make a test pass.

The other half is the two real data gaps this page publishes: two of the 32 rows
carry no bid document link, and several date cells carry a stray internal space or
a non-standard format. Both are read as the source published them (rule 5, rule
9): a missing link becomes `None` rather than a dropped row, and a date is stored
as a string with no attempt made here to parse or straighten it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import yaml
from selectolax.parser import HTMLParser

from monitor.connectors.sierra_leone import (
    CELLS_PER_ROW,
    RESULTS_TABLE,
    SierraLeoneConnector,
    cell_text,
    document_link,
    parse_bid_table,
)
from monitor.models import RawNotice, Source
from monitor.normalise.sierra_leone import map_notice

pytestmark = pytest.mark.contract

FIXTURE = Path(__file__).parent / "fixtures" / "sierra_leone.json"
SOURCE_YAML = Path(__file__).resolve().parents[2] / "sources" / "sierra_leone.yaml"

# What the page held on the day it was recorded. Asserted rather than computed so
# a re-recorded fixture that lost rows fails here and not somewhere subtler.
ROW_COUNT = 32
NO_DOCUMENT_LINK = 2


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
    return parse_bid_table(listing_html, language=source.language)


# --- the registry and the fixture agree -----------------------------------------


def test_the_registry_entry_is_enabled_and_wired(source):
    """Enabled on 2026-09-12, and this test changed with it rather than being deleted.

    It read `assert source.enabled is False`: a live `fetch_raw` at 17:53 UTC
    returned 32 notices inside this entry's `[15, 60]` band, a full pipeline pass at
    17:55 stored all 32 through `monitor/normalise/sierra_leone.py`, and the
    registry was flipped to `enabled: true` and wired into `monitor/fetch.py`'s
    CONNECTORS on the strength of that. An assertion written to describe the
    not-yet-enabled state then started failing as though something had broken,
    which is the same shape of stale test EBRD's contract test names and fixed the
    same way (see `test_the_registry_entry_is_enabled_and_wired` there).

    What is worth asserting instead is the pair that has to stay true together: a
    source is `enabled` in the registry AND present in `monitor.fetch.CONNECTORS`,
    or it is neither - enabled without a connector raises at fetch time, and wired
    without being enabled means nothing ever runs it.
    `tests/unit/test_fetch.py::test_every_wired_source_is_enabled_and_every_enabled_source_is_wired`
    asserts that property across the whole registry; this asserts it for the one
    source this file is about.
    """
    from monitor.fetch import CONNECTORS

    assert source.enabled is True
    assert source.id in CONNECTORS
    assert source.id == "sierra_leone"
    assert source.country == "SL"
    assert source.admin_level == "national"
    assert source.language == "en"
    assert source.connector_class == "FeedConnector"
    assert source.access_type == "api"
    assert source.tos_status == "reviewed_ok"
    assert source.list_url == "https://nppa.gov.sl/bid/"


def test_no_cpv_or_row_selector_is_declared(source):
    """No shared classification scheme exists on this listing (see the module
    docstring); a row_selector belongs to BrowserConnector sources only."""
    assert source.row_selector == ""
    assert source.exclude_notice_types == []


def test_the_recording_matches_the_registrys_own_url(document, source):
    assert document["list_url"] == source.list_url
    assert document["recorded_on"] == "2026-09-12"


# --- item count within the health range -----------------------------------------


def test_the_table_size_is_within_the_registrys_expected_range(rows, source):
    """expected_items_per_run models the table's size, not an arrival rate (see
    sources/sierra_leone.yaml's VOLUME note): re-reading the whole page every run
    costs nothing extra, so 32 measured rows sit inside [15, 60]."""
    assert len(rows) == ROW_COUNT
    assert source.expected_min <= len(rows) <= source.expected_max


# --- every required field is present --------------------------------------------


def test_every_row_carries_its_four_text_fields(rows):
    for row in rows:
        assert row["organisation"].strip()
        assert row["date_posted"].strip()
        assert row["expiration_date"].strip()
        assert row["description"].strip()


def test_every_row_states_a_document_link_or_none(rows):
    """The fifth field may be absent; it may never be missing as a dict key."""
    for row in rows:
        assert "document_url" in row
        assert row["document_url"] is None or row["document_url"].startswith("https://")


def test_the_two_rows_with_no_document_link_are_read_not_dropped(rows):
    """A gap in the source, not a fetch failure (rule 4 is about a selector
    matching nothing, not about one column of a matched row being blank)."""
    missing = [row for row in rows if row["document_url"] is None]

    assert len(missing) == NO_DOCUMENT_LINK
    assert {row["organisation"] for row in missing} == {
        "WEST AFRICA HOLDING (SL) LIMITED",
        "UNIVERSITY OF SIERRA LEONE",
    }
    assert {row["date_posted"] for row in missing} == {"16-06-2026", "20-04-2026"}


def test_a_row_missing_a_required_field_raises(listing_html, source):
    """An empty organisation cell would otherwise map a notice with no buyer name
    and no way to tell a real gap from a changed page."""
    broken = listing_html.replace(">MINISTRY OF SPORTS<", "><", 1)

    with pytest.raises(ValueError, match="organisation"):
        parse_bid_table(broken, language=source.language)


# --- dates are stored exactly as published, glitches included ------------------


def test_a_stray_internal_space_in_a_date_is_kept_as_published(rows):
    """Two rows carry this; straightening it here would be the normaliser's job
    done early and rule 10 forbids reading a deadline from anything but the
    original string."""
    posted_with_space = [row["date_posted"] for row in rows if row["date_posted"] == "30- 04-2026"]
    assert posted_with_space == ["30- 04-2026"]

    both_columns = next(row for row in rows if row["date_posted"] == "26 -01-2026")
    assert both_columns["expiration_date"] == "23 -02- 2026"


def test_non_standard_date_shapes_are_not_normalised(rows):
    """A two-digit year and a single-digit month, neither corrected here."""
    values = {row["expiration_date"] for row in rows}

    assert "23-04-26" in values
    assert "13-4-2026" in values


def test_a_multiline_cell_keeps_its_line_breaks(rows):
    """The buyer typed several lines into one cell; collapsing them would run a
    description or an organisation name into one unreadable line."""
    multiline_orgs = [row for row in rows if "\n" in row["organisation"]]

    assert len(multiline_orgs) > 0
    assert all("  " not in line for row in multiline_orgs for line in row["organisation"].split("\n"))
    assert all(line == line.strip() for row in multiline_orgs for line in row["organisation"].split("\n"))


def test_a_non_breaking_space_does_not_survive_into_a_cell():
    cell = _cell("<td>public&nbsp;financial   management</td>")

    assert cell_text(cell) == "public financial management"


def test_a_trailing_empty_line_from_a_double_line_break_is_dropped():
    cell = _cell("<td>NATIONAL REVENUE AUTHORITY<br /><br /></td>")

    assert cell_text(cell) == "NATIONAL REVENUE AUTHORITY"


# --- declared language matching --------------------------------------------------


def test_the_page_declares_the_registrys_language(listing_html, source):
    assert '<html lang="en-US"' in listing_html
    assert source.language == "en"
    # parse_bid_table raising nothing on the real fixture is itself the assertion
    # that the two agree; made explicit here rather than only implied by success.
    parse_bid_table(listing_html, language=source.language)


def test_a_page_declaring_another_language_raises(listing_html, source):
    """The cheapest signal this source's shape allows that NPPA re-platformed
    under a different locale, in place of EBRD's country-name check or DÖE's
    per-notice language field, neither of which this listing carries."""
    changed = listing_html.replace('<html lang="en-US" >', '<html lang="fr-FR" >', 1)

    with pytest.raises(ValueError, match="fr-fr"):
        parse_bid_table(changed, language=source.language)


def test_a_page_with_no_html_tag_at_all_raises(listing_html, source):
    changed = listing_html.replace('<html lang="en-US" >', "", 1)

    with pytest.raises(ValueError, match="''"):
        parse_bid_table(changed, language=source.language)


# --- a changed layout fails loudly, not silently ---------------------------------


def test_a_renamed_table_id_raises(listing_html, source):
    """Without this check a WordPress theme update that renamed the table would
    read as an archive of zero rows, the exact silent failure rule 4 forbids."""
    renamed = listing_html.replace('id="tablepress-1"', 'id="tablepress-2"')

    with pytest.raises(ValueError, match="tablepress-1"):
        parse_bid_table(renamed, language=source.language)


def test_a_row_that_lost_a_column_raises(listing_html, source):
    """A dropped `<td>` would otherwise shift every later column left by one and
    map, say, a description into the expiration-date field."""
    broken = listing_html.replace(
        '<td class="column-4">SELECTION OF A PRIVATE PARTNER',
        '<span class="column-4">SELECTION OF A PRIVATE PARTNER',
        1,
    )

    with pytest.raises(ValueError, match=f"not {CELLS_PER_ROW}"):
        parse_bid_table(broken, language=source.language)


def test_a_table_emptied_of_rows_raises_rather_than_yielding_zero(listing_html, source):
    """The property `monitor/connectors/browser_base.py` enforces for browser
    sources applies just as much to this server-rendered table: 32 rows on every
    run to date, so zero is a failure state and not a quiet day (rule 4). The
    table's own arrival rate is genuinely sparse (see VOLUME in
    sources/sierra_leone.yaml) but that governs new *postings*, not how many rows
    the table holds - which is exactly why this guard exists apart from that."""
    emptied = re.sub(
        r'(<tbody class="row-striping row-hover">).*?(</tbody>)',
        r"\1\2",
        listing_html,
        flags=re.DOTALL,
    )
    assert f'id="{RESULTS_TABLE.split("#")[1]}"' in emptied  # the container survives; only its rows are gone

    with pytest.raises(ValueError, match="zero rows"):
        parse_bid_table(emptied, language=source.language)


# --- document_link, in isolation -------------------------------------------------


def test_document_link_reads_an_absent_href_as_none():
    cell = _cell("<td><a> View and Download</a></td>")

    assert document_link(cell) is None


def test_document_link_reads_an_empty_href_as_none():
    cell = _cell('<td><a href="">View and Download</a></td>')

    assert document_link(cell) is None


def test_document_link_reads_a_real_href():
    cell = _cell('<td><a href="https://nppa.gov.sl/x.pdf">View and Download</a></td>')

    assert document_link(cell) == "https://nppa.gov.sl/x.pdf"


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


def test_a_run_requests_the_bid_page_once(listing_html, source):
    connector = SierraLeoneConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)

    assert client.requested == [source.list_url]
    assert len(raw_notices) == ROW_COUNT
    assert all(isinstance(raw, RawNotice) for raw in raw_notices)
    assert all(raw.source_id == "sierra_leone" for raw in raw_notices)
    assert all(raw.mime == "application/json" for raw in raw_notices)


def test_every_raw_notice_carries_a_url_document_link_or_the_bid_page(listing_html, source):
    """`RawNotice.url` is one PDF link short of the payload's own value on one row:
    `monitor/models.py` strips whitespace on every string field
    (`str_strip_whitespace=True`), and one recorded href carries a trailing space
    ("Request-for-Proposal-Letter.pdf "). The model's own normalisation, not this
    connector's - the payload below is compared with `.strip()` for that reason,
    and it is the payload, not `raw.url`, that is `notices_raw`'s record of what
    the page actually printed."""
    connector = SierraLeoneConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)
    pairs = [(raw, json.loads(raw.payload)) for raw in raw_notices]

    without_link = [raw for raw, payload in pairs if payload["document_url"] is None]

    assert len(without_link) == NO_DOCUMENT_LINK
    assert all(raw.url == payload["document_url"].strip() for raw, payload in pairs if payload["document_url"])
    assert all(raw.url == source.list_url for raw in without_link)


def test_the_payload_round_trips_every_field_the_row_parsed(listing_html, source):
    connector = SierraLeoneConnector(source, ["48"])
    client = _FixtureClient(listing_html)
    parsed_rows = parse_bid_table(listing_html, language=source.language)

    raw_notices = connector.fetch_raw(client)

    for raw, row in zip(raw_notices, parsed_rows, strict=True):
        assert json.loads(raw.payload) == row


def test_nothing_here_translates_or_parses_a_date(listing_html, source):
    """Rule 5 and rule 9: what a connector returns is the response as fetched.
    Spot check that the stored description is the original English prose and the
    stored dates are the original strings, not a canonical form."""
    connector = SierraLeoneConnector(source, ["48"])
    client = _FixtureClient(listing_html)

    raw_notices = connector.fetch_raw(client)
    first = json.loads(raw_notices[0].payload)

    assert first["organisation"] == "MINISTRY OF SPORTS"
    assert first["date_posted"] == "27-08-2026"
    assert isinstance(first["date_posted"], str)


# --- the normaliser: one bid-table row to a Notice -------------------------
#
# `monitor/normalise/sierra_leone.py` has no fixture of its own; it is written and
# measured directly against the 32 rows this file already parses out of the
# recorded fixture, so its contract test lives here rather than in a fourth file.


def test_all_32_rows_map_without_raising_and_produce_distinct_hashes(rows):
    mapped = [map_notice(row) for row in rows]

    assert len(mapped) == ROW_COUNT
    assert len({m.notice.content_hash for m in mapped}) == ROW_COUNT


def test_the_description_becomes_the_title_and_the_body_stays_empty(rows):
    """NPPA has no separate title column (see the normaliser's module docstring);
    the one prose cell is what a reviewer sees as the notice."""
    for row in rows:
        mapped = map_notice(row)

        assert mapped.notice.title == row["description"].strip()
        assert mapped.notice.body == ""


def test_31_of_32_deadlines_parse_and_all_32_publication_dates_parse(rows):
    """The one refusal is a two-digit year, left unparsed deliberately rather than
    guessed at (rule 10); see the normaliser's module docstring for why."""
    mapped = [map_notice(row) for row in rows]

    assert sum(1 for m in mapped if m.notice.deadline_at is not None) == 31
    assert sum(1 for m in mapped if m.notice.published_at is not None) == 32

    unparsed = [row for row, m in zip(rows, mapped, strict=True) if m.notice.deadline_at is None]
    assert [row["expiration_date"] for row in unparsed] == ["23-04-26"]


def test_a_stray_internal_space_recovers_but_a_two_digit_year_does_not(rows):
    """Four rows in the fixture are typed loosely and get two different, deliberate
    outcomes: trimming whitespace cannot change which number is the day, so those
    parse; choosing a century can, so that one does not (see `_iso`'s docstring)."""
    posted_with_space = next(row for row in rows if row["date_posted"] == "30- 04-2026")
    mapped = map_notice(posted_with_space)
    assert mapped.notice.published_at is not None
    assert (mapped.notice.published_at.month, mapped.notice.published_at.day) == (4, 30)

    both_columns = next(row for row in rows if row["date_posted"] == "26 -01-2026")
    mapped_both = map_notice(both_columns)
    assert mapped_both.notice.published_at is not None
    assert (mapped_both.notice.published_at.year, mapped_both.notice.published_at.month) == (2026, 1)
    assert mapped_both.notice.deadline_at is not None  # its expiration_date is "23 -02- 2026"


def test_the_evidence_for_the_day_month_order_decision_holds_in_this_fixture(rows):
    """`monitor/normalise/sierra_leone.py` decides DD-MM-YYYY on a count taken from
    this exact fixture: of the 64 date values across 32 rows, 41 have a first
    component above 12 (which only a day can be) and the second component never
    exceeds 10. Reproduced here so a re-recorded fixture that no longer supports
    that reasoning fails on this line, rather than the decision quietly resting on
    a count nothing checks any more."""
    values = [row["date_posted"] for row in rows] + [row["expiration_date"] for row in rows]
    triples = [[part.strip() for part in value.strip().split("-")] for value in values]
    digit_triples = [t for t in triples if len(t) == 3 and t[0].isdigit() and t[1].isdigit()]

    assert len(values) == 64
    assert sum(1 for day, _, _ in digit_triples if int(day) > 12) == 41
    assert max(int(month) for _, month, _ in digit_triples) == 10


def test_a_swapped_day_month_order_would_silently_change_a_real_deadline(rows):
    """The regression this decision most needs a guard against. Most rows in the
    fixture have a first component above 12, so misreading month-first on those
    would fail to parse at all - loudly, and already covered by the parse-count
    assertion above. This row's raw value, '06-10-2026', is a valid calendar date
    either way it is read: DD-MM means 6 October, MM-DD would silently mean 10 June
    instead. Nothing else in this suite would notice `_iso` "fixed" to read
    month-first, because both readings succeed; only pinning the actual date this
    row means catches it."""
    row = next(
        r
        for r in rows
        if r["organisation"] == "MINISTRY OF TECHNICAL AND HIGHER EDUCATION" and r["expiration_date"] == "06-10-2026"
    )

    mapped = map_notice(row)

    assert mapped.notice.deadline_at is not None
    assert (mapped.notice.deadline_at.month, mapped.notice.deadline_at.day) == (10, 6)


def test_no_cpv_codes_national_admin_level_and_asserted_english(rows):
    mapped = [map_notice(row) for row in rows]

    assert all(m.notice.cpv_codes == [] for m in mapped)
    assert {m.notice.admin_level for m in mapped} == {"national"}
    assert {m.notice.language for m in mapped} == {"en"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}


def test_a_row_with_no_description_raises():
    """The connector already refuses a row without five cells (see its own contract
    test above); this is the mapper's own guard for the one cell it depends on."""
    row = {
        "organisation": "MINISTRY OF EXAMPLE",
        "date_posted": "01-01-2026",
        "expiration_date": "01-02-2026",
        "description": "   ",
        "document_url": None,
    }

    with pytest.raises(ValueError, match="description"):
        map_notice(row)


# --- helpers ---------------------------------------------------------------


def _cell(markup: str):
    """One `<td>` parsed the way it actually arrives: inside a table and a row.

    A bare `<td>...</td>` handed to `HTMLParser` on its own is not valid HTML - a
    `td` outside `<table><tr>` - and this parser drops it rather than keeping it
    as a stray node, so every isolated-cell test wraps its markup the way the real
    page always does.
    """
    return HTMLParser(f"<table><tr>{markup}</tr></table>").css_first("td")
