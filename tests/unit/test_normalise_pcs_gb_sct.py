"""The Public Contracts Scotland normaliser, measured against the 62 releases
recorded in `tests/contract/fixtures/pcs_gb_sct.json` (September 2026,
noticeType 102), run through the connector's own `parse_releases` rather than
duplicated or guessed at, the same shape as `test_normalise_ghana.py`.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from monitor.connectors.pcs_gb_sct import parse_releases
from monitor.normalise.pcs_gb_sct import map_notice

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "contract" / "fixtures" / "pcs_gb_sct.json"

# What the recorded fixture actually holds, confirmed against the fixture
# rather than assumed, so a re-recorded fixture that quietly changed shape
# fails here and not somewhere subtler.
RELEASE_COUNT = 62
CPV_PRESENT_COUNT = 42
CPV_ABSENT_COUNT = 20
VALUE_STATED_COUNT = 28
API_HOST = "api.publiccontractsscotland.gov.uk"


@pytest.fixture(scope="module")
def releases() -> list[dict]:
    document = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return parse_releases(document)


@pytest.fixture()
def release(releases) -> dict:
    """The first release, deep-copied so a test can mutate it."""
    return deepcopy(releases[0])


# --- the whole fixture maps cleanly ------------------------------------------


def test_all_62_releases_map_without_raising_and_produce_distinct_hashes(releases):
    mapped = [map_notice(r) for r in releases]

    assert len(mapped) == RELEASE_COUNT
    assert len({m.notice.content_hash for m in mapped}) == RELEASE_COUNT


def test_source_id_country_and_admin_level_are_uniform(releases):
    mapped = [map_notice(r) for r in releases]

    assert {m.notice.source_id for m in mapped} == {"pcs_gb_sct"}
    assert {m.notice.country for m in mapped} == {"GB"}
    # Every buyer on this source is a Scottish public body by construction -
    # sub-national is known, not guessed per release.
    assert {m.notice.admin_level for m in mapped} == {"regional"}


def test_language_is_lower_cased_from_the_publishers_upper_case_en(releases):
    mapped = [map_notice(r) for r in releases]

    assert {r["language"] for r in releases} == {"EN"}, "the fixture states upper-case EN throughout"
    assert {m.notice.language for m in mapped} == {"en"}
    assert {m.notice.language_confidence for m in mapped} == {1.0}


def test_every_release_has_a_deadline(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.notice.deadline_at is not None for m in mapped)


def test_no_english_is_carried_because_the_original_already_is_english(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.title_en == "" for m in mapped)
    assert all(m.body_en == "" for m in mapped)


# --- CPV: present on 42, an explicit empty list (not missing) on 20 ----------


def test_cpv_is_present_on_42_releases_and_an_empty_list_on_20(releases):
    mapped = [map_notice(r) for r in releases]

    with_cpv = [m for m in mapped if m.notice.cpv_codes]
    without_cpv = [m for m in mapped if not m.notice.cpv_codes]

    assert len(with_cpv) == CPV_PRESENT_COUNT
    assert len(without_cpv) == CPV_ABSENT_COUNT
    assert all(m.notice.cpv_codes == [] for m in without_cpv), "absent means [], never missing"


# --- value: stated on 28 of 62, always GBP, never converted ------------------


def test_value_is_stated_on_28_releases_and_always_gbp(releases):
    mapped = [map_notice(r) for r in releases]

    stated = [m for m in mapped if m.notice.estimated_value is not None]
    unstated = [m for m in mapped if m.notice.estimated_value is None]

    assert len(stated) == VALUE_STATED_COUNT
    assert len(stated) + len(unstated) == RELEASE_COUNT
    assert all(m.notice.value_currency == "GBP" for m in stated)
    assert all(m.notice.value_currency is None for m in unstated)


# --- body is tender.description, never the release-level boilerplate --------


def test_body_is_the_tender_description_not_the_release_level_boilerplate(release):
    release["tender"]["description"] = "Real procurement detail about this specific tender."
    release["description"] = "NOTE: To register your interest in this notice ... (SC Ref:999999)"

    mapped = map_notice(release)

    assert mapped.notice.body == "Real procurement detail about this specific tender."
    assert "SC Ref" not in mapped.notice.body


# --- every url is the api host's canonical link, never the www host ---------


def test_every_url_is_on_the_api_host(releases):
    mapped = [map_notice(r) for r in releases]

    assert all(m.notice.url.startswith(f"https://{API_HOST}/") for m in mapped)
    assert all("www.publiccontractsscotland.gov.uk" not in m.notice.url for m in mapped)


def test_external_id_is_the_releases_own_id(release):
    mapped = map_notice(release)

    assert mapped.notice.external_id == release["id"]


# --- raise cases --------------------------------------------------------------


def test_a_release_without_a_tender_title_raises_naming_the_external_id(release):
    release["tender"]["title"] = ""

    with pytest.raises(ValueError, match=re.escape(release["id"])):
        map_notice(release)


def test_a_release_with_a_whitespace_only_title_also_raises(release):
    release["tender"]["title"] = "   "

    with pytest.raises(ValueError, match=re.escape(release["id"])):
        map_notice(release)
