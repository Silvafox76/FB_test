"""The deduper: one tender, one candidate, however many sources carry it.

The three cases BUILD_ORDER step 8 names are the first three tests. The rest are
the ways this goes wrong in practice: joining two genuinely different tenders is
worse than failing to join two copies, because a missed join shows a reviewer the
same thing twice and a wrong join hides an opportunity entirely.

The last three sections are step 20: which English rendering the fuzzy rule
compares, what happens when a notice has none, and the system-name rule's place
after the fuzzy pass rather than beside it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from rapidfuzz.fuzz import token_set_ratio

from monitor.dedupe.cluster import (
    MATCH_CONTENT_HASH,
    MATCH_SYSTEM_NAME,
    MATCH_TITLE_FUZZY,
    TITLE_RATIO,
    Candidate,
    Incoming,
    both_rendered,
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


# --- the donor notice joining the national one, step 11's first acceptance test -


def test_a_world_bank_notice_joins_the_national_notice_for_the_same_tender():
    """Constructed, because no real pair existed on 2026-09-11. Said plainly:

    The live World Bank run that day fetched 13 notices, in BJ, ME, MK, NE (five),
    NG (two), SL, TG and UA. The only one of those countries any other enabled
    source carries is Ukraine, and against all 390 Prozorro notices the best
    `token_set_ratio` was 29 with no content-hash collision anywhere, so nothing
    joined and nothing should have. The national half of a West African pair comes
    from the portals at step 17; until then this case can only be constructed.

    What is real here is the donor side: notice OP00468043, Togo, Electric Power
    Company of Togo, closing 2026-10-28 at 10:00, fetched today. Its title is
    published in French and the `title_en` below is the English rendering step 14
    produces, because that is what the deduper compares (the originals would never
    match). `admin_level` is `donor` on the World Bank side and national on the
    other, which is the whole point: one candidate, two sources behind it.
    """
    world_bank = incoming(
        notice_id="wb-OP00468043",
        country="TG",
        title_en="Supply and commissioning of equipment for the continuation of the Revenue Protection Programme",
        system_names=(),
        deadline_at=datetime(2026, 10, 28, 10, 0, tzinfo=UTC),
        content_hash="hash-worldbank-OP00468043",
    )
    national_portal = candidate(
        id="C000042",
        country="TG",
        title_en="Supply and commissioning of equipment for the Revenue Protection Programme, lot 1",
        system_names=(),
        deadline_at=datetime(2026, 10, 28, 12, 0, tzinfo=UTC),
        content_hashes=frozenset({"hash-togo-portal"}),
    )

    match = find_match(world_bank, [national_portal])

    assert match is not None
    assert (match.candidate_id, match.method) == ("C000042", MATCH_TITLE_FUZZY)
    assert match.score >= 85


# --- step 20: which text the fuzzy rule compares ------------------------------

# One real TED notice, 619646-2026, fetched 2026-09-12, in three renderings as the
# database actually holds them. The French is `notices.title`, the second line is
# TED's own `ted-eforms`/`source-native` row in `translations`, and the third is
# `scores.title_en`, the scorer's English title. The numbers in the test below were
# measured from these exact strings.
FR_ORIGINAL = (
    "France – Logiciels et systèmes d'information – ACQUISITION ET MISE EN PLACE D'UNE SOLUTION DE "
    "GESTION DU PATRIMOINE DE LA REGION NOUVELLE AQUITAINE"
)
FR_TED_RENDERING = (
    "France – Software package and information systems – ACQUISITION ET MISE EN PLACE D'UNE SOLUTION DE "
    "GESTION DU PATRIMOINE DE LA REGION NOUVELLE AQUITAINE"
)
FR_SCORER_RENDERING = "Acquisition and implementation of an asset management solution for the Nouvelle-Aquitaine region"

# Constructed, and said plainly: no English mirror of 619646-2026 exists in the
# database, because the source that would carry one is a West African portal from
# step 17 or a donor feed that did not pick this tender up. The French half is real.
EN_MIRROR = "Acquisition and implementation of an asset management solution for the Nouvelle-Aquitaine region, lot 1"

FR_DEADLINE = datetime(2026, 10, 15, 21, 59, 59, tzinfo=UTC)


def test_a_real_french_notice_joins_its_english_mirror_through_the_scorer_rendering():
    """The whole of step 20's dedupe half, on one real notice's three renderings.

    `token_set_ratio` against the mirror, measured 2026-09-12:

        French original                     23.7   never joins
        TED's own ted-eforms rendering      27.2   never joins
        scores.title_en                     96.5   joins

    The middle line is the one that decides where the deduper reads from. TED does
    publish an English rendering, and it is 84 percent a translation of the CPV
    heading with the buyer's own words left in French (618 of 738 non-English TED
    notices on 2026-09-12), so a deduper reading `translations.title_en` would be
    comparing French against English and would join nothing.
    """
    french = incoming(
        notice_id="ted-619646-2026",
        country="FR",
        title_en=FR_SCORER_RENDERING,
        system_names=(),
        deadline_at=FR_DEADLINE,
        content_hash="hash-ted-619646",
    )
    mirror = candidate(
        id="C000101",
        country="FR",
        title_en=EN_MIRROR,
        system_names=(),
        deadline_at=FR_DEADLINE + timedelta(days=1),
        content_hashes=frozenset({"hash-mirror"}),
    )

    match = find_match(french, [mirror])

    assert match is not None
    assert (match.candidate_id, match.method) == ("C000101", MATCH_TITLE_FUZZY)
    assert match.score >= TITLE_RATIO

    # And the two renderings the deduper is not given would both have failed.
    assert find_match(replace(french, title_en=FR_ORIGINAL), [mirror]) is None
    assert find_match(replace(french, title_en=FR_TED_RENDERING), [mirror]) is None


# --- step 20: a notice with no English rendering is not comparable ------------


def test_a_notice_with_no_english_rendering_joins_nothing_on_title():
    """Not "compared and found different": not comparable. Rule 1, no fallback to the original."""
    unrendered = incoming(content_hash="hash-other", title_en="")

    assert find_match(unrendered, [candidate()]) is None


def test_a_candidate_with_no_english_rendering_joins_nothing_on_title():
    assert find_match(incoming(content_hash="hash-other"), [candidate(title_en="")]) is None


def test_two_unrendered_notices_do_not_join_each_other_on_a_shared_system_name():
    """The failure this guard exists for.

    Country, deadline week and system name all agree, and the only thing left to
    check is a title neither side has. rapidfuzz scores two empty strings 0.0 today,
    so the floor would catch it; `both_rendered` is what makes that this module's
    rule rather than a property of the installed library.
    """
    unrendered = incoming(content_hash="hash-other", title_en="   ", system_names=("GIFMIS",))

    assert find_match(unrendered, [candidate(title_en="")]) is None


def test_an_exact_hash_still_joins_a_notice_with_no_rendering():
    """Rule 1 needs no text at all: the same bytes from two sources are the same notice."""
    match = find_match(incoming(content_hash="hash-ted", title_en=""), [candidate()])

    assert match is not None
    assert match.method == MATCH_CONTENT_HASH


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("Supply of an IFMIS", "Supply of an IFMIS", True),
        ("", "Supply of an IFMIS", False),
        ("Supply of an IFMIS", "", False),
        ("   ", "Supply of an IFMIS", False),
        ("", "", False),
    ],
)
def test_both_rendered(left, right, expected):
    assert both_rendered(left, right) == expected


def test_rapidfuzz_still_scores_an_empty_title_at_zero():
    """The measured fact the guard's comment cites. If this ever fails, read that comment."""
    assert token_set_ratio("", "") == 0.0
    assert token_set_ratio("", IFMIS_TITLE) == 0.0


# --- step 20: the system-name rule is a fallback, not an alternative ----------


def test_a_fuzzy_title_on_a_later_candidate_beats_a_system_name_on_an_earlier_one():
    """Rule 2 is exhausted against every candidate before rule 3 is tried at all.

    Without that ordering the answer would depend on which candidate the stager
    loaded first, and a reviewer would see the same tender attached to a different
    programme depending on the order of a `group by`.
    """
    same_system_different_tender = candidate(
        id="C000001",
        title_en="Modernisation of public financial management, phase 2: GIFMIS rollout",
        system_names=("GIFMIS",),
        content_hashes=frozenset({"hash-a"}),
    )
    same_tender = candidate(id="C000002", system_names=(), content_hashes=frozenset({"hash-b"}))

    match = find_match(
        incoming(content_hash="hash-new", system_names=("GIFMIS",)),
        [
            same_system_different_tender,
            same_tender,
        ],
    )

    assert match is not None
    assert (match.candidate_id, match.method) == ("C000002", MATCH_TITLE_FUZZY)
