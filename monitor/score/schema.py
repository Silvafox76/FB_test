"""The `record_score` tool: the only way the scorer accepts an answer.

The input schema is derived from `Score.model_json_schema()` and nothing else, so
the contract the model is held to and the contract the pipeline validates against
cannot drift: they are generated from the same object. Appendix C of Architecture
v0.4 is what `Score` is written to, field for field.

`tool_choice` forces this tool, so the model cannot answer in prose.

**Why the schema is rewritten before it is sent.** `strict` makes the API validate
the arguments against the schema before they reach us, and in exchange it accepts
only a subset of JSON Schema. Two things about that subset were learned the hard
way on 2026-09-11, from the first scoring calls ever made on this deployment.
Until a credential existed nothing sent this schema anywhere, so neither could
have been caught by a test.

*Unsupported keywords are refused outright.* A pydantic model with a bounded
integer generates `minimum` and `maximum`:

    400 invalid_request_error
    tools.0.custom: For 'integer' type, properties maximum, minimum are not supported

The module used to carry a comment saying these were "stripped and enforced by
Score on arrival" - an accurate description of an intention that nothing
implemented. `strict_schema` is that claim, made true. Nothing is lost: `Score`
validates every tool result, so `relevance: 101` is caught by the validator a
moment later, and a schema failure parks the notice after one retry (rule 2).

*A property the schema does not mark required cannot be returned at all.* This one
was expensive. Five of appendix C's fields had a default in `Score` (four still do;
`estimated_value_usd` was removed on 2026-09-12, see migration 012) -
`matched_functions`, `system_names` and `eligibility_flags` default to empty lists,
`estimated_value_usd` and `deadline_at` to null - so pydantic leaves them out of
`required`, and under `strict` the API accepted tool calls containing only the
other five. The first 29 notices scored came back with a relevance, a title, a
summary, a confidence and a procurement type, and with every one of those five
fields empty, on every single notice. It read exactly like a model declining to
match anything.

What that silently disabled is the whole point of the scorer: `matched_functions`
is what appendix E's FreeBalance Products Required and Product Gaps are computed
from, `system_names` is the deduper's third match rule, `eligibility_flags` drives
the eligibility comment and the register-interest suggestion, and the value and
deadline are columns of their own. An empty list is indistinguishable from a
considered "none", which is why this was invisible until someone asked why a
treasury system matched no treasury function.

So `require_every_property` marks every property required at every level, which is
what strict mode asks for, and optionality is expressed the way strict mode expects
it: as a nullable type the model must answer explicitly. A field with a list
default stays a plain array and the model returns `[]` when it means none - which
is now a statement rather than an absence. `Score`'s defaults still apply on
arrival, so nothing about the Python contract changes.
"""

from __future__ import annotations

from typing import Any

from monitor.models import Score

TOOL_NAME = "record_score"

DESCRIPTION = (
    "Record your assessment of this procurement notice. Call this exactly once, with every "
    "required field. Do not answer in prose."
)

# JSON Schema keywords the strict tool API rejects. Every one of them is a
# constraint `Score` re-checks on arrival, which is why dropping them costs
# nothing. Listed explicitly rather than filtered by a rule, so that a keyword the
# API starts or stops accepting is a one-line diff with a date against it.
UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "format",
    }
)


def strict_schema(schema: Any) -> Any:
    """`Score`'s JSON Schema with the keywords the strict tool API refuses removed.

    Walks the whole document rather than the top level: `matched_functions` is a
    list of `MatchedFunction`, whose `min_length=1` fields sit two levels down in
    `$defs`, and a filter that only looked at the top level would pass locally and
    fail on the wire.
    """
    if isinstance(schema, dict):
        return {key: strict_schema(value) for key, value in schema.items() if key not in UNSUPPORTED_KEYWORDS}
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    return schema


def require_every_property(schema: Any) -> Any:
    """Mark every property of every object required, as strict mode demands.

    Walks into `$defs` as well, because `MatchedFunction` is where `function_id`
    and `evidence` live and a nested object with optional properties has the same
    problem as the top-level one.

    This does not make a value mandatory in any meaningful sense: a nullable field
    is still answerable with null and an array field with `[]`. It makes the model
    say so out loud instead of omitting the key, which is the difference between
    "no function matched" and "the field was not available to me".
    """
    if isinstance(schema, dict):
        rewritten = {key: require_every_property(value) for key, value in schema.items()}
        if rewritten.get("type") == "object" and isinstance(rewritten.get("properties"), dict):
            rewritten["required"] = list(rewritten["properties"])
        return rewritten
    if isinstance(schema, list):
        return [require_every_property(item) for item in schema]
    return schema


def tool_definition() -> dict:
    """The tool as the API takes it."""
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "input_schema": require_every_property(strict_schema(Score.model_json_schema())),
        "strict": True,
    }


def tool_choice() -> dict:
    """Force this tool. An answer in prose is not an answer."""
    return {"type": "tool", "name": TOOL_NAME}
