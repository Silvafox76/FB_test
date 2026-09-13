"""The Mali normaliser: one SIGMAP dossier row to a Notice.

`monitor/normalise/mali.py` has no fixture of its own; it is written and measured
directly against the 98 rows recorded in `tests/contract/fixtures/mali.json`, so
its tests live here rather than duplicating that fixture.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from monitor.connectors.mali import NOTICE_URL
from monitor.normalise.mali import map_notice

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures" / "mali.json"

# What the recorded corpus actually holds. Asserted rather than assumed, so a
# re-recorded fixture that quietly changed shape fails here and not somewhere
# subtler.
ROW_COUNT = 98


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def row(rows) -> dict:
    """One row (id 175527), deep-copied so a test can mutate it freely."""
    return deepcopy(rows[0])


# --- the whole corpus maps cleanly ------------------------------------------


def test_all_98_rows_map_without_raising_and_produce_distinct_hashes(rows):
    mapped = [map_notice(r) for r in rows]

    assert len(mapped) == ROW_COUNT
    assert len({m.notice.content_hash for m in mapped}) == ROW_COUNT


def test_every_row_has_a_publication_date_and_no_deadline(rows):
    """No deadline field exists anywhere on this endpoint; see the module docstring."""
    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.published_at is not None for m in mapped)
    assert all(m.notice.deadline_at is None for m in mapped)


def test_no_row_carries_a_value_a_currency_or_a_cpv_code(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.estimated_value is None for m in mapped)
    assert all(m.notice.value_currency is None for m in mapped)
    assert all(m.notice.cpv_codes == [] for m in mapped)


def test_admin_level_language_and_country_are_uniform(rows):
    mapped = [map_notice(r) for r in rows]

    assert {m.notice.admin_level for m in mapped} == {"national"}
    assert {m.notice.language for m in mapped} == {"fr"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}
    assert {m.notice.country for m in mapped} == {"ML"}


def test_body_is_always_empty_because_the_endpoint_publishes_no_body_text(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.notice.body == "" for m in mapped)


def test_no_english_is_carried_because_none_is_published(rows):
    mapped = [map_notice(r) for r in rows]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


def test_every_notice_uses_the_shared_listing_page_as_its_url(rows):
    """There is no per-dossier deep link on this source; see the module docstring."""
    mapped = [map_notice(r) for r in rows]

    assert {m.notice.url for m in mapped} == {NOTICE_URL}


def test_69_of_98_rows_have_a_ctrname_that_differs_from_dptname(rows):
    """The measurement behind reading ctrName and not dptName as the buyer."""
    differing = [r for r in rows if r["ctrName"] != r["dptName"]]

    assert len(differing) == 69


def test_the_buyer_is_ctrname_not_dptname_on_a_row_where_they_differ(rows):
    differing = next(r for r in rows if r["ctrName"] != r["dptName"])

    mapped = map_notice(differing)

    assert mapped.notice.buyer == differing["ctrName"]
    assert mapped.notice.buyer != differing["dptName"]


def test_the_external_id_is_dosnr_not_the_connectors_own_id(row):
    mapped = map_notice(row)

    assert mapped.notice.external_id == row["dosNr"]
    assert mapped.notice.external_id != str(row["id"])


def test_ami_notices_are_mapped_the_same_as_aao_ones(rows):
    """Rule 5: the mapper does not filter, so a manifestation-interet notice is
    carried exactly like a call for tenders."""
    ami_rows = [r for r in rows if r["dtpCode"] == "AMI"]
    aao_rows = [r for r in rows if r["dtpCode"] == "AAO"]

    assert len(ami_rows) == 46
    assert len(aao_rows) == 52

    ami_mapped = map_notice(ami_rows[0])
    aao_mapped = map_notice(aao_rows[0])

    assert ami_mapped.notice.title == ami_rows[0]["dosName"].strip()
    assert aao_mapped.notice.title == aao_rows[0]["dosName"].strip()


def test_the_bare_yyyymmdd_date_is_read_without_reshaping(row):
    """20260911 -> 2026-09-11, midnight UTC: dates.py's own %Y%m%d format, not a
    transformation performed in this module. See the module docstring."""
    assert row["dosDate"] == "20260911"

    mapped = map_notice(row)

    published = mapped.notice.published_at
    assert (published.year, published.month, published.day) == (2026, 9, 11)
    assert (published.hour, published.minute, published.second) == (0, 0, 0)


# --- raise cases -------------------------------------------------------------


def test_a_missing_dosname_raises(row):
    row["dosName"] = ""

    with pytest.raises(ValueError, match="dosName"):
        map_notice(row)


def test_a_missing_ctrname_raises(row):
    row["ctrName"] = ""

    with pytest.raises(ValueError, match="ctrName"):
        map_notice(row)


def test_a_missing_dosnr_raises(row):
    row["dosNr"] = ""

    with pytest.raises(ValueError, match="dosNr"):
        map_notice(row)


def test_a_missing_dosdate_raises(row):
    del row["dosDate"]

    with pytest.raises(ValueError, match="dosDate"):
        map_notice(row)


@pytest.mark.parametrize(
    "bad_date",
    [
        "2026-09-11",  # not the bare digit form this endpoint actually publishes
        "202609",  # too short
        "2026091100",  # too long
        "2026091x",  # not all digits
        "",
    ],
)
def test_a_dosdate_not_shaped_like_yyyymmdd_raises(row, bad_date):
    row["dosDate"] = bad_date

    with pytest.raises(ValueError, match="dosDate"):
        map_notice(row)


def test_a_dosdate_with_an_impossible_calendar_date_raises(row):
    """Digit-and-length shaped, but not a real day: dates.py logs and returns
    None, and this module raises rather than carrying a silently unset date for a
    source that always states one."""
    row["dosDate"] = "20261332"  # month 13

    with pytest.raises(ValueError, match="unparseable dosDate"):
        map_notice(row)
