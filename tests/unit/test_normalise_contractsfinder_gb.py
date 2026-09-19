"""The Contracts Finder normaliser: 14 recorded OCDS releases to Notices.

`monitor/normalise/contractsfinder_gb.py` has no fixture of its own; it is
written and measured directly against the 14 releases
`monitor/connectors/contractsfinder_gb.py`'s own `parse_releases` reads from
`tests/contract/fixtures/contractsfinder_gb.json`, so its tests run the fixture
through the connector's own parser rather than duplicating that JSON or
guessing at release shape.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import pytest

from monitor.connectors.contractsfinder_gb import parse_releases
from monitor.normalise.contractsfinder_gb import map_notice

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures"

# What the recorded fixture actually holds. Asserted rather than assumed, so a
# re-recorded fixture that quietly changed shape fails here and not somewhere
# subtler.
RELEASE_COUNT = 14
VALUE_STATED_COUNT = 9
VALUE_ABSENT_COUNT = 5
AMENDMENT_TAGGED_IDS = {
    "c1d4496d-d743-4cce-a4e3-c331667d1d49-914681",
    "cd451c25-8c25-46cb-9107-65eeb575e6f7-914654",
    "d8dce101-537d-4995-bccb-c311835c5cfc-914649",
}
# Releases 914719 and 914718 ("Face to Face Translation Services Contract",
# both filed jointly by the same five NHS bodies within four minutes of each
# other) carry byte-identical tender.title and tender.description under two
# different tender ids. content_hash is deliberately title+body only
# (monitor/normalise/hashing.py), so this pair collides: 14 releases map to
# 13 distinct hashes, not 14. That is a measured fact about this fixture, not
# a defect in the hash or the mapper - a true duplicate is exactly what the
# dedupe stage (rule 5) exists to catch downstream, not something a normaliser
# should paper over by hashing more fields into uniqueness.
DUPLICATE_HASH_IDS = {
    "cba3aabd-e8a3-4334-acea-cc8f7fae6a1e-914719",
    "b118e7b7-1061-4dc8-aab8-8f7e83420bce-914718",
}
DISTINCT_HASH_COUNT = RELEASE_COUNT - 1


@pytest.fixture(scope="module")
def releases() -> list[dict]:
    document = json.loads((FIXTURES / "contractsfinder_gb.json").read_text(encoding="utf-8"))
    return parse_releases(document)


@pytest.fixture()
def release(releases) -> dict:
    """The first recorded release, deep-copied so a test can mutate it."""
    return deepcopy(releases[0])


# --- every fixture release maps end to end, through the connector's own parser --


def test_all_14_releases_map_without_raising(releases):
    mapped = [map_notice(r) for r in releases]

    assert len(mapped) == RELEASE_COUNT


def test_13_of_14_hashes_are_distinct_because_two_releases_share_identical_text(releases):
    """See DISTINCT_HASH_COUNT above: 914719 and 914718 are a genuine
    title+body duplicate in the recorded fixture, not a hashing defect."""
    mapped_by_id = {m.notice.external_id: m for m in (map_notice(r) for r in releases)}

    assert len({m.notice.content_hash for m in mapped_by_id.values()}) == DISTINCT_HASH_COUNT

    a, b = (mapped_by_id[external_id] for external_id in sorted(DUPLICATE_HASH_IDS))
    assert a.notice.content_hash == b.notice.content_hash
    assert a.notice.title == b.notice.title
    assert a.notice.body == b.notice.body
    # Distinct records in every other respect - this is two releases, not one
    # read twice.
    assert a.notice.external_id != b.notice.external_id
    assert a.notice.url != b.notice.url


def test_country_source_id_and_admin_level_are_uniform(releases):
    mapped = [map_notice(r) for r in releases]

    assert {m.notice.country for m in mapped} == {"GB"}
    assert {m.notice.source_id for m in mapped} == {"contractsfinder_gb"}
    assert {m.notice.admin_level for m in mapped} == {"national"}


def test_language_is_read_release_level_and_lower_cased(releases):
    mapped = [map_notice(r) for r in releases]

    assert {m.notice.language for m in mapped} == {"en"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}


def test_no_english_is_carried_because_the_original_already_is_english(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


def test_external_id_is_the_release_id(release):
    mapped = map_notice(release)

    assert mapped.notice.external_id == release["id"]


def test_every_notice_uses_the_connectors_own_release_url_not_a_string_built_from_the_id(releases):
    from monitor.connectors.contractsfinder_gb import release_url

    mapped = [map_notice(r) for r in releases]

    urls = {m.notice.url for m in mapped}
    assert len(urls) == RELEASE_COUNT, "every release has its own notice URL"
    for r, m in zip(releases, mapped, strict=True):
        assert m.notice.url == release_url(r)
        # Never a string built by splicing the release id into a template.
        assert r["id"] not in m.notice.url


# --- CPV, extracted through tender.classification on every release -----------


def test_cpv_is_extracted_on_all_14_releases_through_the_classification_path(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.notice.cpv_codes for m in mapped)
    assert all(len(m.notice.cpv_codes) == 1 for m in mapped)

    for r, m in zip(releases, mapped, strict=True):
        assert m.notice.cpv_codes == [r["tender"]["classification"]["id"]]


def test_cpv_is_not_read_from_items_additional_classifications(releases):
    """Two releases carry a top-level `tender.additionalClassifications` list with
    CPV-scheme codes distinct from `tender.classification`; only the
    `classification` path is read (see the module docstring)."""
    release = next(r for r in releases if r["tender"].get("additionalClassifications"))
    extra_codes = {c["id"] for c in release["tender"]["additionalClassifications"] if c.get("scheme") == "CPV"}
    assert extra_codes, "fixture assumption: at least one release has additional CPV codes"

    mapped = map_notice(release)

    assert mapped.notice.cpv_codes == [release["tender"]["classification"]["id"]]
    assert not extra_codes & set(mapped.notice.cpv_codes)


# --- value: stated on 9, absent (not zero) on 5 -------------------------------


def test_value_is_stated_on_9_releases_and_none_on_5(releases):
    mapped = [map_notice(r) for r in releases]

    stated = [m for m in mapped if m.notice.estimated_value is not None]
    absent = [m for m in mapped if m.notice.estimated_value is None]

    assert len(stated) == VALUE_STATED_COUNT
    assert len(absent) == VALUE_ABSENT_COUNT
    assert all(m.notice.value_currency == "GBP" for m in stated)
    assert all(m.notice.value_currency is None for m in absent)


def test_a_stated_value_is_carried_exactly_as_published_including_a_bare_1(releases):
    with_value = {r["id"]: r for r in releases if "value" in r["tender"]}
    mapped_by_id = {m.notice.external_id: m for m in (map_notice(r) for r in releases)}

    for external_id, r in with_value.items():
        expected = Decimal(str(r["tender"]["value"]["amount"])).quantize(Decimal("0.01"))
        assert mapped_by_id[external_id].notice.estimated_value == expected

    # Two releases (PFRU2-2025-738 and PFRU2-2025-764) publish a bare 1, a real
    # stated figure and not the publisher's zero-means-unstated convention.
    ones = [r for r in with_value.values() if r["tender"]["value"]["amount"] == 1]
    assert len(ones) == 2
    for r in ones:
        assert mapped_by_id[r["id"]].notice.estimated_value == Decimal("1.00")


def test_a_release_without_a_value_key_carries_no_currency_either(releases):
    without_value = [r for r in releases if "value" not in r["tender"]]
    assert len(without_value) == VALUE_ABSENT_COUNT

    for r in without_value:
        mapped = map_notice(r)
        assert mapped.notice.estimated_value is None
        assert mapped.notice.value_currency is None


# --- the deadline parses with its published offset ----------------------------


def test_the_deadline_parses_with_its_published_offset(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.notice.deadline_at is not None for m in mapped)
    assert all(m.notice.published_at is not None for m in mapped)


def test_one_real_release_deadline_matches_its_stated_tender_period_end_date(release):
    assert release["tender"]["tenderPeriod"]["endDate"] == "2026-10-03T10:00:00+01:00"

    mapped = map_notice(release)
    deadline = mapped.notice.deadline_at

    # parse_deadline keeps the published offset rather than normalising to UTC
    # (monitor/normalise/dates.py); the wall-clock fields are exactly as stated.
    assert (deadline.year, deadline.month, deadline.day) == (2026, 10, 3)
    assert (deadline.hour, deadline.minute, deadline.second) == (10, 0, 0)
    assert deadline.utcoffset().total_seconds() == 3600
    # Same instant as 09:00 UTC.
    assert deadline.astimezone(UTC).hour == 9


# --- an amendment maps like any other tender-stage release --------------------


def test_an_amendment_tagged_release_maps_and_is_not_treated_differently(releases):
    amendments = [r for r in releases if set(r.get("tag") or []) & {"tenderAmendment"}]
    assert {r["id"] for r in amendments} == AMENDMENT_TAGGED_IDS
    assert len(amendments) == 3

    for r in amendments:
        mapped = map_notice(r)
        assert mapped.notice.title
        assert mapped.notice.external_id == r["id"]
        assert mapped.notice.country == "GB"


# --- raise cases ---------------------------------------------------------------


def test_a_release_without_a_tender_title_raises_naming_the_external_id(release):
    release["tender"]["title"] = ""

    with pytest.raises(ValueError, match=release["id"]):
        map_notice(release)


def test_a_release_with_only_whitespace_for_a_title_raises(release):
    release["tender"]["title"] = "   "

    with pytest.raises(ValueError, match=release["id"]):
        map_notice(release)
