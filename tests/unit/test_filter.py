"""The free filter, against the hand-written cases and against its own edges.

The cases in `fixtures/filter_cases.yaml` are the contract: a person decided what
should pass and what should drop, and a lexicon change at step 19 has to keep
agreeing with them. A case is added there before the lexicon changes, never after,
so a miss is provably fixed rather than patched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from monitor.filter.cpv import check as cpv_check
from monitor.filter.lexicon import check as lexicon_check
from monitor.filter.run import NEEDS_TRANSLATION, decide, lexicons, pass_prefixes

CASES = yaml.safe_load((Path(__file__).parent / "fixtures" / "filter_cases.yaml").read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def prefixes() -> list[str]:
    return pass_prefixes()


@pytest.fixture(scope="module")
def available() -> dict:
    return lexicons()


def outcome_for(case: dict, prefixes: list[str], available: dict):
    return decide(
        title=case["title"],
        body=case["body"],
        language=case["language"],
        cpv_codes=case["cpv_codes"],
        prefixes=prefixes,
        available_lexicons=available,
    )


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_every_hand_written_case(case, prefixes, available):
    outcome = outcome_for(case, prefixes, available)

    if case["expect"] == "needs_translation":
        assert outcome.status == "detected"
        assert outcome.filter_result == NEEDS_TRANSLATION
    else:
        assert outcome.status == case["expect"], (
            f"{case['name']}: expected {case['expect']}, got {outcome.status} ({outcome.filter_result})"
        )


def test_the_case_file_covers_what_the_step_asks_for():
    """Four CPV drops, four lexicon drops, four passes, at least one French pass."""
    expectations = [case["expect"] for case in CASES]

    assert len(CASES) >= 12
    assert expectations.count("filtered_in") >= 4
    assert expectations.count("filtered_out") >= 8
    assert any(case["language"] == "fr" and case["expect"] == "filtered_in" for case in CASES)


def test_every_case_says_why_it_is_there():
    """A case without a reason is a case nobody can argue with later."""
    for case in CASES:
        assert case["because"].strip(), f"{case['name']} has no reason"


def test_a_dropped_notice_says_why(prefixes, available):
    """A reviewer asking why they never saw a notice gets a sentence, not an absence."""
    for case in CASES:
        outcome = outcome_for(case, prefixes, available)
        assert outcome.filter_result.strip(), f"{case['name']} was decided silently"


# --- the CPV stage on its own ------------------------------------------------


def test_no_cpv_code_is_not_a_failed_match():
    """Absence means the source did not classify, not that it classified against us."""
    verdict = cpv_check([], ["48", "72", "79"])

    assert verdict.passed
    assert verdict.reason == "no cpv code"


def test_one_passing_code_among_many_is_enough():
    verdict = cpv_check(["45000000", "72000000"], ["48", "72", "79"])

    assert verdict.passed
    assert verdict.matched_prefix == "72"


def test_the_drop_reason_names_the_code_and_the_allowed_set():
    verdict = cpv_check(["45233120"], ["48", "72", "79"])

    assert not verdict.passed
    assert "45233120" in verdict.reason
    assert "48/72/79" in verdict.reason


def test_the_prefixes_are_an_argument_not_a_constant():
    """Rule 6: this module does not know that the prefixes are 48, 72 and 79."""
    assert cpv_check(["33000000"], ["33"]).passed
    assert not cpv_check(["48000000"], ["33"]).passed


# --- the lexicon stage on its own --------------------------------------------


def test_matching_is_case_insensitive():
    verdict = lexicon_check("An ifmis rollout", "", {"treasury_accounting": ["IFMIS"]})

    assert verdict.matched
    assert verdict.functions == ["treasury_accounting"]


def test_matching_respects_word_boundaries():
    """Without boundaries, TSA matches inside Motsa and every acronym over-fires."""
    inside_a_word = lexicon_check("Motsa district council", "", {"bank_cash_management": ["TSA"]})
    on_its_own = lexicon_check("Establishing a TSA", "", {"bank_cash_management": ["TSA"]})

    assert not inside_a_word.matched
    assert on_its_own.matched


def test_the_body_is_matched_as_well_as_the_title():
    verdict = lexicon_check("Procurement notice", "Implementation of a GIFMIS", {"f": ["GIFMIS"]})

    assert verdict.matched


def test_matched_phrases_come_back_in_lexicon_order():
    """The reviewer sees the first three, so the order cannot be set-ordered."""
    verdict = lexicon_check(
        "budget execution and commitment control",
        "",
        {"f": ["budget execution", "commitment control"]},
    )

    assert verdict.phrases == ["budget execution", "commitment control"]


def test_an_accented_french_phrase_matches():
    verdict = lexicon_check(
        "Système d'information pour l'exécution budgétaire",
        "",
        {"budget_execution": ["exécution budgétaire"]},
    )

    assert verdict.matched


# --- the whole filter --------------------------------------------------------


def test_cpv_runs_before_the_lexicon(prefixes, available):
    """A notice that would match the lexicon still drops on a failing CPV code."""
    outcome = decide(
        title="Catering for the IFMIS project team",
        body="Lunches for the treasury single account programme office.",
        language="en",
        cpv_codes=["55520000"],
        prefixes=prefixes,
        available_lexicons=available,
    )

    assert outcome.status == "filtered_out"
    assert outcome.filter_result.startswith("cpv 55520000")


def test_a_pass_records_the_prefix_the_language_and_the_phrases(prefixes, available):
    outcome = decide(
        title="Supply of an Integrated Financial Management Information System",
        body="IFMIS covering budget execution.",
        language="en",
        cpv_codes=["48440000"],
        prefixes=prefixes,
        available_lexicons=available,
    )

    assert outcome.status == "filtered_in"
    assert outcome.filter_result.startswith("cpv 48, lexicon en: ")
    assert len(outcome.filter_result.split(": ")[1].split(", ")) <= 3


def test_a_language_with_no_lexicon_is_held_not_dropped(prefixes, available):
    """Rule 4: these are counted, not silently lost."""
    outcome = decide(
        title="Закупівля програмного забезпечення",
        body="",
        language="uk",
        cpv_codes=["48000000"],
        prefixes=prefixes,
        available_lexicons=available,
    )

    assert outcome.status == "detected"
    assert outcome.filter_result == NEEDS_TRANSLATION


def test_the_filter_never_calls_a_model():
    """The free filter is free. If this module ever imports anthropic, that is the point."""
    import monitor.filter.run as run_module

    source = Path(run_module.__file__).read_text(encoding="utf-8")
    assert "anthropic" not in source
    assert "import httpx" not in source
