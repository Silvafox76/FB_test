"""The typed contracts, tested at the boundary they exist to defend.

`Score` is the model-call tool schema (Architecture v0.4 appendix C), so a change
here changes what the model is asked to produce at step 6.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from monitor.models import SUMMARY_MAX_WORDS, Score, Source


def valid_score(**overrides) -> dict:
    payload = {
        "relevance": 78,
        "title_en": "Supply and implementation of an integrated financial management system",
        "matched_functions": [{"function_id": "treasury_accounting", "evidence": "IFMIS rollout"}],
        "system_names": ["IFMIS", "GIFMIS"],
        "procurement_type": "system",
        "estimated_value_usd": 4_200_000,
        "eligibility_flags": ["local_registration"],
        "deadline_at": "2026-11-30",
        "summary_en": "Ghana's Ministry of Finance seeks a supplier for a national IFMIS replacement.",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


def test_a_valid_score_round_trips():
    score = Score.model_validate(valid_score())

    assert score.relevance == 78
    assert score.matched_functions[0].function_id == "treasury_accounting"
    assert score.procurement_type == "system"


def test_score_rejects_relevance_over_one_hundred():
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_score(relevance=101))

    assert "relevance" in str(raised.value)


def test_score_rejects_an_unknown_procurement_type():
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_score(procurement_type="framework agreement"))

    assert "procurement_type" in str(raised.value)


def test_score_rejects_a_summary_over_the_word_limit():
    too_long = " ".join(["word"] * 141)

    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_score(summary_en=too_long))

    assert "summary_en" in str(raised.value)
    assert str(SUMMARY_MAX_WORDS) in str(raised.value)


def test_score_rejects_a_missing_title():
    payload = valid_score()
    del payload["title_en"]

    with pytest.raises(ValidationError) as raised:
        Score.model_validate(payload)

    assert "title_en" in str(raised.value)


def test_score_rejects_an_unknown_eligibility_flag():
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_score(eligibility_flags=["bribe_required"]))

    assert "eligibility_flags" in str(raised.value)


def test_score_rejects_a_field_the_model_invented():
    """extra='forbid' everywhere: an unknown key raises rather than being dropped."""
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_score(competitor="an incumbent vendor"))

    assert "competitor" in str(raised.value)


def test_score_json_schema_is_usable_as_a_tool_definition():
    """Step 6 passes this straight to the API as the record_score input schema."""
    schema = Score.model_json_schema()

    assert schema["type"] == "object"
    assert set(schema["required"]) >= {"relevance", "title_en", "procurement_type", "summary_en", "confidence"}


def valid_source(**overrides) -> dict:
    payload = {
        "id": "example",
        "name": "An example portal",
        "country": "SN",
        "language": "fr",
        "admin_level": "national",
        "stream": "portal",
        "wave": 1,
        "access": "public_listing",
        "connector": "PageConnector",
        "schedule": "0 5 * * 1-5",
        "list_url": "https://example.invalid/avis",
        "tos_status": "pending",
        "health": {"expected_items_per_run": [1, 25], "max_consecutive_failures": 3},
        "owner": "ryan.dear",
    }
    payload.update(overrides)
    return payload


def test_source_flattens_the_health_block_the_way_the_table_stores_it():
    source = Source.model_validate(valid_source())

    assert (source.expected_min, source.expected_max) == (1, 25)
    assert source.max_consecutive_failures == 3
    assert source.enabled is False  # a new source is off until its contract test passes


def test_source_needs_a_url():
    payload = valid_source()
    del payload["list_url"]

    with pytest.raises(ValidationError) as raised:
        Source.model_validate(payload)

    assert "list_url or api_url" in str(raised.value)


def test_a_multi_country_source_must_say_what_it_covers():
    with pytest.raises(ValidationError) as raised:
        Source.model_validate(valid_source(country="multi"))

    assert "covers" in str(raised.value)


def test_source_rejects_an_expected_range_the_wrong_way_round():
    with pytest.raises(ValidationError) as raised:
        Source.model_validate(valid_source(health={"expected_items_per_run": [40, 2], "max_consecutive_failures": 3}))

    assert "expected_items_per_run" in str(raised.value)
