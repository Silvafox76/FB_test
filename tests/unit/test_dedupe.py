"""The deduper: one tender, one candidate, however many sources carry it.

The three cases BUILD_ORDER step 8 names are the first three tests. The rest are
the ways this goes wrong in practice: joining two genuinely different tenders is
worse than failing to join two copies, because a missed join shows a reviewer the
same thing twice and a wrong join hides an opportunity entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monitor.dedupe.cluster import (
    MATCH_CONTENT_HASH,
    MATCH_SYSTEM_NAME,
    MATCH_TITLE_FUZZY,
    Candidate,
    Incoming,
    find_match,
    within_window,
)

DEADLINE = datetime(2026, 11, 30, tzinfo=UTC)
IFMIS_TITLE = "Supply and implementation of an integrated financial management information system"


def candidate(**overrides) -> Candidate:
    payload = {
        "id": "C000001",
        "country": "GH",
        "title_en": IFMIS_TITLE,
        "system_names": ("IFMIS", "GIFMIS"),
        "deadline_at": DEADLINE,
        "content_hashes": frozenset({"hash-ted"}),
    }
    payload.update(overrides)
    return Candidate(**payload)


def incoming(**overrides) -> Incoming:
    payload = {
        "notice_id": "n-2",
        "country": "GH",
        "title_en": IFMIS_TITLE,
        "system_names": ("IFMIS",),
        "deadline_at": DEADLINE,
        "content_hash": "hash-worldbank",
    }
    payload.update(overrides)
    return Incoming(**payload)


# --- the three cases the step names ------------------------------------------


def test_the_same_notice_text_from_two_sources_joins():
    """Since migration 007 each source keeps its own notice, so this can happen."""
    match = find_match(incoming(content_hash="hash-ted"), [candidate()])

    assert match is not None
    assert match.method == MATCH_CONTENT_HASH
    assert match.score == 100


def test_two_different_tenders_from_the_same_buyer_with_the_same_deadline_do_not_join():
    """Same country, same closing date, different thing being bought."""
    payroll = incoming(
        title_en="Supply of a payroll and human resources management system",
        system_names=("IPPIS",),
    )

    assert find_match(payroll, [candidate()]) is None


def test_a_french_title_and_its_english_rendering_join():
    """They join through title_en. Matching the originals would never do it."""
    french_original = incoming(
        notice_id="n-fr",
        country="SN",
        title_en="Supply and implementation of an integrated financial management information system",
        content_hash="hash-sigmap",
    )
    english_mirror = candidate(
        id="C000002",
        country="SN",
        title_en="Supply and implementation of an integrated financial management information system for the Treasury",
        content_hashes=frozenset({"hash-ted"}),
    )

    match = find_match(french_original, [english_mirror])

    assert match is not None
    assert match.method == MATCH_TITLE_FUZZY
    assert match.score >= 85


# --- country is a hard gate --------------------------------------------------


def test_an_identical_tender_in_another_country_does_not_join():
    """Two ministries buying an IFMIS in the same month are two opportunities."""
    senegal = incoming(country="SN")

    assert find_match(senegal, [candidate(country="GH")]) is None


# --- the deadline window -----------------------------------------------------


def test_a_notice_with_no_deadline_does_not_join_on_title():
    """No deadline means no window, and title alone would merge last year's rerun."""
    undated = incoming(content_hash="hash-other", deadline_at=None)

    assert find_match(undated, [candidate()]) is None


def test_a_candidate_with_no_deadline_does_not_join_on_title():
    assert find_match(incoming(content_hash="hash-other"), [candidate(deadline_at=None)]) is None


def test_a_deadline_eight_days_apart_does_not_join():
    far = incoming(content_hash="hash-other", deadline_at=DEADLINE + timedelta(days=8))

    assert find_match(far, [candidate()]) is None


def test_a_deadline_seven_days_apart_still_joins():
    near = incoming(content_hash="hash-other", deadline_at=DEADLINE + timedelta(days=7))

    assert find_match(near, [candidate()]) is not None


def test_an_exact_hash_joins_even_with_no_deadline():
    """Certainty needs no corroboration; the other two rules do."""
    match = find_match(incoming(content_hash="hash-ted", deadline_at=None), [candidate()])

    assert match is not None
    assert match.method == MATCH_CONTENT_HASH


@pytest.mark.parametrize("days", [0, 1, 7])
def test_within_window_accepts_up_to_seven_days(days):
    assert within_window(DEADLINE, DEADLINE + timedelta(days=days))


def test_within_window_needs_both_dates():
    assert not within_window(DEADLINE, None)
    assert not within_window(None, DEADLINE)


# --- the system-name rule ----------------------------------------------------


def test_a_shared_system_name_joins_titles_that_would_not_match_alone():
    """GIFMIS in the same country inside a week is almost certainly one programme."""
    differently_worded = incoming(
        content_hash="hash-other",
        title_en="Modernisation of public financial management, phase 2: GIFMIS rollout",
        system_names=("GIFMIS",),
    )

    match = find_match(differently_worded, [candidate()])

    assert match is not None
    assert match.method == MATCH_SYSTEM_NAME


def test_a_shared_system_name_is_not_enough_without_any_title_overlap():
    """The 40 ratio is low but it is not zero."""
    unrelated = incoming(
        content_hash="hash-other",
        title_en="Catering",
        system_names=("GIFMIS",),
    )

    assert find_match(unrelated, [candidate()]) is None


def test_system_names_match_case_insensitively():
    lowercase = incoming(
        content_hash="hash-other",
        title_en="Modernisation of public financial management, phase 2: gifmis rollout",
        system_names=("gifmis",),
    )

    assert find_match(lowercase, [candidate()]) is not None


def test_no_shared_system_name_means_no_system_name_match():
    other_system = incoming(
        content_hash="hash-other",
        title_en="Modernisation of public financial management, phase 2",
        system_names=("IPPIS",),
    )

    assert find_match(other_system, [candidate()]) is None


# --- ordering ----------------------------------------------------------------


def test_an_exact_hash_beats_a_fuzzy_title_on_an_earlier_candidate():
    """The rules are tried in order, not the candidates, so the result is stable."""
    fuzzy_first = candidate(id="C000001", content_hashes=frozenset({"hash-something-else"}))
    exact_second = candidate(
        id="C000002", title_en="A completely different title", content_hashes=frozenset({"hash-x"})
    )

    match = find_match(incoming(content_hash="hash-x"), [fuzzy_first, exact_second])

    assert match is not None
    assert (match.candidate_id, match.method) == ("C000002", MATCH_CONTENT_HASH)


def test_no_candidates_means_no_match():
    assert find_match(incoming(), []) is None
