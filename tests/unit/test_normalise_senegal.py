"""The Senegal normaliser: one achatspublics.sn tender row to a Notice.

`monitor/normalise/senegal.py` has no fixture of its own; it is written and
measured directly against the 35 rows recorded in
`tests/contract/fixtures/senegal.json`, so its tests live here rather than
duplicating that fixture.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from monitor.connectors.senegal import NOTICE_URL
from monitor.normalise.senegal import map_notice

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures" / "senegal.json"

# What the recorded archive actually holds. Asserted rather than assumed, so a
# re-recorded fixture that quietly changed shape fails here and not somewhere
# subtler.
ROW_COUNT = 35
TEST_TITLED_ROWS = 9


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document["data"]["content"]


@pytest.fixture()
def row(rows) -> dict:
    """The first row (test-organisation uid), deep-copied so a test can mutate it."""
    return deepcopy(rows[0])


# --- the whole archive maps cleanly ------------------------------------------


def test_all_35_rows_map_without_raising_and_produce_distinct_hashes(rows):
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
    """cible (COMMUNITY/NATIONAL) does not change admin_level; see the module
    docstring."""
    mapped = [map_notice(r) for r in rows]

    assert {m.notice.admin_level for m in mapped} == {"national"}
    assert {m.notice.language for m in mapped} == {"fr"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}
    assert {m.notice.country for m in mapped} == {"SN"}


def test_body_is_always_empty_because_description_is_always_null(rows):
    assert all(r.get("description") is None for r in rows)

    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.body == "" for m in mapped)


def test_a_populated_description_would_be_carried_as_the_body(row):
    """Read generically rather than hardcoded to the always-null case observed in
    the fixture; see the module docstring."""
    row["description"] = "Details of the works to be carried out."

    mapped = map_notice(row)

    assert mapped.notice.body == "Details of the works to be carried out."


def test_no_english_is_carried_because_none_is_published(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


def test_every_notice_uses_the_per_record_url_built_from_uid(rows):
    mapped = [map_notice(r) for r in rows]

    for r, m in zip(rows, mapped, strict=True):
        assert m.notice.url == NOTICE_URL.format(uid=r["uid"])
        assert r["uid"] in m.notice.url


def test_the_buyer_is_organization_libelle_not_direction_libelle(row):
    assert row["organization"]["libelle"] != row["direction"]["libelle"]

    mapped = map_notice(row)

    assert mapped.notice.buyer == row["organization"]["libelle"]
    assert mapped.notice.buyer != row["direction"]["libelle"]


def test_the_external_id_is_uid_not_reference(row):
    mapped = map_notice(row)

    assert mapped.notice.external_id == row["uid"]
    assert mapped.notice.external_id != row["reference"]


def test_9_of_35_titles_carry_a_test_marker_and_are_mapped_not_dropped(rows):
    """Rule 5: the mapper does not filter, so the achatspublics.sn test rows reach
    Notice like any other row. See the module docstring."""
    test_titled = [r for r in rows if "test" in r["libelle"].lower()]

    assert len(test_titled) == TEST_TITLED_ROWS

    mapped = [map_notice(r) for r in test_titled]
    assert all(m.notice.title for m in mapped)
    assert {m.notice.status for m in mapped} == {"detected"}


def test_29_of_35_rows_are_cible_community_and_6_are_national(rows):
    """The measurement behind not reading cible as admin_level; see the module
    docstring."""
    counts = {value: sum(1 for r in rows if r["cible"] == value) for value in ("COMMUNITY", "NATIONAL")}

    assert counts == {"COMMUNITY": 29, "NATIONAL": 6}


def test_cible_does_not_change_admin_level_either_way(rows):
    national = next(r for r in rows if r["cible"] == "NATIONAL")
    community = next(r for r in rows if r["cible"] == "COMMUNITY")

    assert map_notice(national).notice.admin_level == "national"
    assert map_notice(community).notice.admin_level == "national"


def test_the_millisecond_timestamp_is_reshaped_not_reparsed_by_this_module(row):
    """2025-11-18T16:37:40.481+00:00 -> 16:37:40 UTC, dropping the milliseconds
    dates.py's own DATE_FORMATS has no form for. See the module docstring."""
    assert row["publicationDate"] == "2025-11-18T16:37:40.481+00:00"
    assert row["submissionDate"] == "2025-11-18T17:00:00.000+00:00"

    mapped = map_notice(row)

    published = mapped.notice.published_at
    deadline = mapped.notice.deadline_at
    assert (published.hour, published.minute, published.second) == (16, 37, 40)
    assert (deadline.hour, deadline.minute, deadline.second) == (17, 0, 0)


# --- raise cases -------------------------------------------------------------


def test_a_missing_uid_raises(row):
    row["uid"] = ""

    with pytest.raises(ValueError, match="uid"):
        map_notice(row)


def test_a_missing_libelle_raises(row):
    row["libelle"] = ""

    with pytest.raises(ValueError, match="libelle"):
        map_notice(row)


def test_a_missing_organization_raises(row):
    del row["organization"]

    with pytest.raises(ValueError, match="organization"):
        map_notice(row)


def test_an_organization_with_no_libelle_raises(row):
    row["organization"]["libelle"] = ""

    with pytest.raises(ValueError, match="organization.libelle"):
        map_notice(row)


def test_a_missing_publicationdate_raises(row):
    del row["publicationDate"]

    with pytest.raises(ValueError, match="publicationDate"):
        map_notice(row)


def test_a_missing_submissiondate_raises(row):
    del row["submissionDate"]

    with pytest.raises(ValueError, match="submissionDate"):
        map_notice(row)


@pytest.mark.parametrize(
    "bad_value",
    [
        "30/11/2026",  # ambiguous day-month form dates.py refuses on principle
        "next Friday",
        "not a date",
        "",
    ],
)
def test_a_malformed_publicationdate_raises(row, bad_value):
    row["publicationDate"] = bad_value

    with pytest.raises(ValueError, match="publicationDate"):
        map_notice(row)


def test_a_bare_date_with_no_time_still_parses_because_dates_py_accepts_it(row):
    """Not a raise case: `2025-11-18` is one of dates.py's own unambiguous
    DATE_FORMATS (a bare ISO date), so this module's reshaping - which only
    touches the measured millisecond-and-offset shape - leaves it untouched and
    dates.py reads it as that day's midnight."""
    row["publicationDate"] = "2025-11-18"

    mapped = map_notice(row)

    published = mapped.notice.published_at
    assert (published.year, published.month, published.day) == (2025, 11, 18)
    assert (published.hour, published.minute, published.second) == (0, 0, 0)


def test_a_malformed_submissiondate_raises(row):
    row["submissionDate"] = "not a date"

    with pytest.raises(ValueError, match="submissionDate"):
        map_notice(row)
