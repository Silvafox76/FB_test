"""The `record_score` tool contract.

The tool's input schema and the thing the pipeline validates against are the same
object, `Score`, so this file tests one contract rather than two that might drift.
Architecture v0.4 appendix C is what `Score` is written to.

The property behind every case here: the API does not enforce numeric or
string-length constraints, so a model can return `relevance: 101` and the tool call
will succeed. `Score` is what catches it, and a message that does not name the
field is a message nobody can act on.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from monitor.models import SUMMARY_MAX_WORDS, Score
from monitor.score.schema import TOOL_NAME, tool_choice, tool_definition


def valid_input(**overrides) -> dict:
    payload = {
        "relevance": 82,
        "title_en": "Supply and implementation of an integrated financial management system",
        "matched_functions": [
            {"function_id": "treasury_accounting", "evidence": "supply of an IFMIS covering the general ledger"}
        ],
        "system_names": ["IFMIS"],
        "procurement_type": "system",
        "estimated_value_usd": 4_200_000,
        "eligibility_flags": ["local_registration"],
        "deadline_at": "2026-11-30",
        "summary_en": "Ghana's Ministry of Finance seeks a supplier for a national IFMIS replacement.",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


# --- the tool definition -----------------------------------------------------


def test_the_tool_input_schema_is_the_model_itself():
    """One contract, not two. If they drift, the model is held to the wrong one."""
    definition = tool_definition()

    assert definition["name"] == TOOL_NAME
    assert definition["input_schema"] == Score.model_json_schema()


def test_the_tool_is_forced():
    """An answer in prose is not an answer."""
    assert tool_choice() == {"type": "tool", "name": TOOL_NAME}


def test_the_schema_forbids_invented_fields():
    """strict: true requires additionalProperties false on every object."""
    schema = tool_definition()["input_schema"]

    assert tool_definition()["strict"] is True
    assert schema["additionalProperties"] is False
    for definition in schema.get("$defs", {}).values():
        assert definition.get("additionalProperties") is False, definition


def test_the_schema_is_json_serialisable():
    """It goes over the wire as JSON; a non-serialisable default would fail at call time."""
    assert json.loads(json.dumps(tool_definition()))["name"] == TOOL_NAME


# --- a valid answer ----------------------------------------------------------


def test_a_valid_tool_input_round_trips():
    score = Score.model_validate(valid_input())

    assert score.relevance == 82
    assert score.matched_functions[0].function_id == "treasury_accounting"
    assert score.confidence == "high"
    assert score.eligibility_flags == ["local_registration"]


def test_the_optional_fields_may_be_absent():
    """A notice stating no value and no deadline is normal, not invalid."""
    payload = valid_input()
    del payload["estimated_value_usd"]
    del payload["deadline_at"]

    score = Score.model_validate(payload)

    assert score.estimated_value_usd is None
    assert score.deadline_at is None


# --- every invalid variant names its field -----------------------------------


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("relevance", 101),
        ("relevance", -1),
        ("relevance", "high"),
        ("procurement_type", "framework agreement"),
        ("confidence", "very high"),
        ("confidence", 0.9),
        ("eligibility_flags", ["bribe_required"]),
        ("matched_functions", [{"function_id": "treasury_accounting"}]),
        ("matched_functions", [{"evidence": "no function id"}]),
        ("title_en", ""),
        ("summary_en", ""),
        ("deadline_at", "the end of November"),
        ("system_names", "IFMIS"),
    ],
)
def test_an_invalid_value_raises_naming_the_field(field, bad_value):
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_input(**{field: bad_value}))

    assert field in str(raised.value), f"the message does not name {field}"


@pytest.mark.parametrize("field", ["relevance", "title_en", "procurement_type", "summary_en", "confidence"])
def test_a_missing_required_field_raises_naming_it(field):
    payload = valid_input()
    del payload[field]

    with pytest.raises(ValidationError) as raised:
        Score.model_validate(payload)

    assert field in str(raised.value)


def test_a_summary_over_the_word_limit_raises_naming_the_limit():
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_input(summary_en=" ".join(["word"] * 141)))

    message = str(raised.value)
    assert "summary_en" in message
    assert str(SUMMARY_MAX_WORDS) in message


def test_a_field_the_model_invented_raises_naming_it():
    with pytest.raises(ValidationError) as raised:
        Score.model_validate(valid_input(incumbent_vendor="someone else"))

    assert "incumbent_vendor" in str(raised.value)


def test_no_tool_call_at_all_is_a_schema_failure():
    """An empty answer has to fail the same way an invalid one does."""
    with pytest.raises(ValidationError):
        Score.model_validate({})
