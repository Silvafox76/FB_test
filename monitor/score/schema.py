"""The `record_score` tool: the only way the scorer accepts an answer.

The input schema is derived from `Score.model_json_schema()` and nothing else, so
the contract the model is held to and the contract the pipeline validates against
cannot drift: they are generated from the same object. Appendix C of Architecture
v0.4 is what `Score` is written to, field for field.

`tool_choice` forces this tool, so the model cannot answer in prose.

**Why the schema is filtered before it is sent.** `strict` makes the API validate
the arguments against the schema before they reach us, and in exchange it accepts
only a subset of JSON Schema. A pydantic model with a bounded integer generates
`minimum` and `maximum`, and the API refuses the tool outright:

    400 invalid_request_error
    tools.0.custom: For 'integer' type, properties maximum, minimum are not supported

That was a real failure of the first scoring call ever made on this deployment, on
2026-09-11. Until a credential existed nothing sent this schema anywhere, so the
module carried a comment claiming these keywords were "stripped and enforced by
Score on arrival" while nothing stripped them. `strict_schema` is that claim, made
true.

Nothing is lost by removing them. The constraints still hold, one layer later and
on the path that matters: `Score` validates every tool result on arrival, so
`relevance: 101` is caught by the validator rather than by the API, and a schema
failure parks the notice after one retry (rule 2). The API was never the thing
enforcing the range; it was only ever going to be a faster way to learn the same
thing.
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


def tool_definition() -> dict:
    """The tool as the API takes it."""
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "input_schema": strict_schema(Score.model_json_schema()),
        "strict": True,
    }


def tool_choice() -> dict:
    """Force this tool. An answer in prose is not an answer."""
    return {"type": "tool", "name": TOOL_NAME}
