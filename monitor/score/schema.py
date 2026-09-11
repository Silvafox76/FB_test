"""The `record_score` tool: the only way the scorer accepts an answer.

The input schema is `Score.model_json_schema()` and nothing else, so the contract
the model is held to and the contract the pipeline validates against cannot drift:
they are the same object. Appendix C of Architecture v0.4 is what `Score` is
written to, field for field.

`tool_choice` forces this tool, so the model cannot answer in prose. `strict` is
set, which makes the API validate the arguments against the schema before they
reach us. It does not remove the need to validate with `Score` on arrival: the API
does not enforce numeric or string-length constraints (open decision 3), so
`relevance: 101` is a thing the model can return and the validator is what catches
it.
"""

from __future__ import annotations

from monitor.models import Score

TOOL_NAME = "record_score"

DESCRIPTION = (
    "Record your assessment of this procurement notice. Call this exactly once, with every "
    "required field. Do not answer in prose."
)


def tool_definition() -> dict:
    """The tool as the API takes it."""
    return {
        "name": TOOL_NAME,
        "description": DESCRIPTION,
        "input_schema": Score.model_json_schema(),
        # The API validates arguments against the schema before returning them.
        # Constraints it does not support (minimum, maximum, minLength) are stripped
        # and enforced by Score on arrival instead.
        "strict": True,
    }


def tool_choice() -> dict:
    """Force this tool. An answer in prose is not an answer."""
    return {"type": "tool", "name": TOOL_NAME}
