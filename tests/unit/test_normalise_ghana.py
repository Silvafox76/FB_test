"""The Ghana normaliser: one GHANEPS Current Tenders row to a Notice.

`monitor/normalise/ghana.py` has no fixture of its own; it is written and
measured directly against the 20 rows `monitor/connectors/ghana.py`'s own
`parse_listing_page` reads from the two recorded pages in
`tests/contract/fixtures/ghana.html` and `ghana_page2.html` (10 rows each), so
its tests run the fixture pages through the connector's own parser rather than
duplicating that HTML or guessing at row shape.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from monitor.connectors.ghana import parse_listing_page
from monitor.normalise.ghana import _from_java_date_string, map_notice

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures"

# What the recorded pages actually hold. Asserted rather than assumed, so a
# re-recorded fixture that quietly changed shape fails here and not somewhere
# subtler.
ROW_COUNT = 20
REPEATS_TITLE_COUNT = 4
REPEAT_RESOURCE_IDS = {"3557053", "3543412", "3543368", "3403640"}


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    page1 = parse_listing_page((FIXTURES / "ghana.html").read_text(encoding="utf-8"), expected_page=1, language="en")
    page2 = parse_listing_page(
        (FIXTURES / "ghana_page2.html").read_text(encoding="utf-8"), expected_page=2, language="en"
    )
    return page1["rows"] + page2["rows"]


@pytest.fixture()
def row(rows) -> dict:
    """The first row (resourceId 3403640), deep-copied so a test can mutate it."""
    return deepcopy(rows[0])


# --- both fixture pages map cleanly, end to end through the connector's own parser --


def test_all_20_rows_across_both_pages_map_without_raising_and_produce_distinct_hashes(rows):
    mapped = [map_notice(r) for r in rows]

    assert len(mapped) == ROW_COUNT
    assert len({m.notice.content_hash for m in mapped}) == ROW_COUNT


def test_every_row_has_a_publication_date_and_a_deadline(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.published_at is not None for m in mapped)
    assert all(m.notice.deadline_at is not None for m in mapped)


def test_no_row_carries_a_value_a_currency_or_a_cpv_code(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.estimated_value is None for m in mapped)
    assert all(m.notice.value_currency is None for m in mapped)
    assert all(m.notice.cpv_codes == [] for m in mapped)


def test_admin_level_language_and_country_are_uniform(rows):
    mapped = [map_notice(r) for r in rows]

    assert {m.notice.admin_level for m in mapped} == {"national"}
    assert {m.notice.language for m in mapped} == {"en"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}
    assert {m.notice.country for m in mapped} == {"GH"}


def test_no_english_is_carried_because_the_original_already_is_english(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


def test_every_notice_uses_its_own_notice_pdf_url_not_a_shared_listing_page(rows):
    mapped = [map_notice(r) for r in rows]

    urls = {m.notice.url for m in mapped}
    assert len(urls) == ROW_COUNT, "every row has its own per-notice link"
    for r, m in zip(rows, mapped, strict=True):
        assert m.notice.url == r["notice_pdf_url"]


def test_the_external_id_is_resource_id(row):
    mapped = map_notice(row)

    assert mapped.notice.external_id == row["resource_id"]


# --- the description-repeats-title judgement ---------------------------------


def test_4_of_20_rows_have_a_description_that_only_repeats_the_title(rows):
    """The measurement behind the module's body-dropping rule: 3 exact matches
    plus one differing from the title only by a trailing full stop."""
    repeats = {r["resource_id"] for r in rows if r["description"].rstrip(".") == r["title"].rstrip(".")}

    assert repeats == REPEAT_RESOURCE_IDS
    assert len(repeats) == REPEATS_TITLE_COUNT


def test_a_repeated_description_is_dropped_to_an_empty_body(rows):
    for r in rows:
        if r["resource_id"] in REPEAT_RESOURCE_IDS:
            mapped = map_notice(r)
            assert mapped.notice.body == "", r["resource_id"]


def test_the_trailing_period_only_variant_is_still_recognised_as_a_repeat(rows):
    """resourceId 3403640: description is the title plus one trailing '.' and
    nothing else - a bare `==` would miss this; see the module docstring."""
    row = next(r for r in rows if r["resource_id"] == "3403640")

    assert row["description"] == row["title"] + "."

    mapped = map_notice(row)

    assert mapped.notice.body == ""


def test_a_description_that_adds_real_information_is_kept(rows):
    row = next(r for r in rows if r["resource_id"] == "3517574")

    assert row["description"] != row["title"]

    mapped = map_notice(row)

    assert mapped.notice.body == row["description"]
    assert mapped.notice.body != ""


def test_16_of_20_rows_keep_a_non_empty_body(rows):
    mapped = [map_notice(r) for r in rows]

    non_empty = [m for m in mapped if m.notice.body != ""]
    assert len(non_empty) == ROW_COUNT - REPEATS_TITLE_COUNT


# --- the Java Date.toString() reshaping ---------------------------------------


def test_every_deadline_and_publication_date_in_both_pages_matches_the_measured_java_shape(rows):
    """Regex-reshaped against every one of the 40 date values across both
    fixture pages, not just a sample; see the module docstring."""
    for r in rows:
        for field in ("deadline", "publication_date"):
            iso = _from_java_date_string(r[field], resource_id=r["resource_id"], field=field)
            assert iso.count("-") == 2
            assert iso.endswith("+00:00")


def test_one_real_row_reshapes_to_the_expected_iso_form(row):
    assert row["deadline"] == "Fri Oct 02 10:00:00 GMT 2026"
    assert row["publication_date"] == "Fri Sep 11 15:58:07 GMT 2026"

    mapped = map_notice(row)

    deadline = mapped.notice.deadline_at
    published = mapped.notice.published_at
    assert (deadline.year, deadline.month, deadline.day) == (2026, 10, 2)
    assert (deadline.hour, deadline.minute, deadline.second) == (10, 0, 0)
    assert (published.year, published.month, published.day) == (2026, 9, 11)
    assert (published.hour, published.minute, published.second) == (15, 58, 7)


def test_from_java_date_string_reorders_weekday_month_day_time_zone_year():
    iso = _from_java_date_string("Sun Sep 06 17:12:56 GMT 2026", resource_id="x", field="publication_date")

    assert iso == "2026-09-06T17:12:56+00:00"


# --- raise cases ---------------------------------------------------------------


def test_a_missing_resource_id_raises(row):
    row["resource_id"] = ""

    with pytest.raises(ValueError, match="resource_id"):
        map_notice(row)


def test_a_missing_title_raises(row):
    row["title"] = ""

    with pytest.raises(ValueError, match="title"):
        map_notice(row)


def test_a_missing_procuring_entity_raises(row):
    row["procuring_entity"] = ""

    with pytest.raises(ValueError, match="procuring_entity"):
        map_notice(row)


def test_a_missing_notice_pdf_url_raises(row):
    row["notice_pdf_url"] = ""

    with pytest.raises(ValueError, match="notice_pdf_url"):
        map_notice(row)


def test_a_missing_deadline_raises(row):
    row["deadline"] = ""

    with pytest.raises(ValueError, match="deadline"):
        map_notice(row)


def test_a_missing_publication_date_raises(row):
    row["publication_date"] = ""

    with pytest.raises(ValueError, match="publication_date"):
        map_notice(row)


@pytest.mark.parametrize(
    "bad_value",
    [
        "2026-10-02T10:00:00+00:00",  # already ISO, not Java's Date.toString() form
        "October 2, 2026",
        "not a date",
        "Fri Oct 02 10:00:00 UTC 2026",  # a zone name other than the always-literal GMT
    ],
)
def test_a_deadline_not_shaped_like_javas_date_tostring_raises(row, bad_value):
    row["deadline"] = bad_value

    with pytest.raises(ValueError, match="deadline"):
        map_notice(row)


def test_a_publication_date_not_shaped_like_javas_date_tostring_raises(row):
    row["publication_date"] = "2026-09-11"

    with pytest.raises(ValueError, match="publication_date"):
        map_notice(row)


def test_an_unknown_month_name_raises():
    with pytest.raises(ValueError, match="unknown month"):
        _from_java_date_string("Fri Xxx 02 10:00:00 GMT 2026", resource_id="x", field="deadline")
