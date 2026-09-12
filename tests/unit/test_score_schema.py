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
        "eligibility_flags": ["local_registration"],
        "deadline_at": "2026-11-30",
        "summary_en": "Ghana's Ministry of Finance seeks a supplier for a national IFMIS replacement.",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


# --- the tool definition -----------------------------------------------------


def test_the_tool_input_schema_is_derived_from_the_model_and_not_hand_written():
    """One contract, not two. If they drift, the model is held to the wrong one.

    It is no longer byte-identical to `Score.model_json_schema()`, because strict
    mode accepts only a subset of JSON Schema and rejects some of what pydantic
    generates. What still has to hold is that it is DERIVED: the field set is the
    model's field set, so adding a field to `Score` reaches the wire without anyone
    editing the schema module, and hand-writing a schema here fails this test.
    """
    definition = tool_definition()

    assert definition["name"] == TOOL_NAME
    assert definition["input_schema"]["properties"].keys() == Score.model_json_schema()["properties"].keys()
    assert definition["input_schema"]["additionalProperties"] is False


def test_every_property_is_required_because_strict_mode_drops_the_rest():
    """The defect this pins cost a whole scoring run.

    Five of appendix C's ten fields had defaults in `Score`, so pydantic left them
    out of `required`, and a strict tool will not carry a property that is not
    required. The first 29 notices scored came back with matched_functions,
    system_names, eligibility_flags, estimated_value_usd and deadline_at empty on
    every single one, which read exactly like a model matching nothing.

    `estimated_value_usd` is no longer one of them: it was removed from `Score` on
    2026-09-12 because the model invented every value it ever reported. The rule
    this test pins is unchanged and still applies to the four that remain.
    """
    schema = tool_definition()["input_schema"]

    assert set(schema["required"]) == set(schema["properties"]), "a property not required cannot be returned"
    for name, definition in schema.get("$defs", {}).items():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"]), name

    for field in ("matched_functions", "system_names", "eligibility_flags", "deadline_at"):
        assert field in schema["required"], f"{field} has a default in Score and must still be required here"


def test_no_keyword_the_strict_api_refuses_survives_anywhere():
    """`minimum` on an integer is a 400 on the tool definition, not a warning.

    Checked over the serialised document rather than the top level, because
    MatchedFunction's constraints live in $defs and a top-level-only filter passed
    locally and failed on the wire.
    """
    serialised = json.dumps(tool_definition()["input_schema"])

    for keyword in ("minimum", "maximum", "minLength", "maxLength", "exclusiveMinimum", "multipleOf"):
        assert f'"{keyword}"' not in serialised, f"{keyword} is refused by the strict tool API"

    # It really was in what pydantic generated, so the filter is doing work.
    assert '"minimum"' in json.dumps(Score.model_json_schema())


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
    """A notice stating no deadline is normal, not invalid."""
    payload = valid_input()
    del payload["deadline_at"]

    score = Score.model_validate(payload)

    assert score.deadline_at is None


def test_the_model_can_no_longer_report_a_value_at_all():
    """The field is gone from the schema, so a model that supplies one is refused.

    It was removed because it was never right: all eight values the scorer ever
    produced were absent from the notice text it was given, and on the four where
    the payload carried a real EUR figure the model could not see, three of the
    inventions sat at 1.09 to 1.19 times it. The figure now comes from the source's
    own structured field. `extra="forbid"` on Score is what makes this a refusal
    rather than a silently ignored key.
    """
    assert "estimated_value_usd" not in tool_definition()["input_schema"]["properties"]

    with pytest.raises(ValidationError):
        Score.model_validate(valid_input() | {"estimated_value_usd": 4_200_000})


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
