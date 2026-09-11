"""Score one notice. One model, one prompt, one forced tool, one retry.

The same shape as the translation call at step 14, and deliberately so: the cap is
checked before the request (rule 22), the answer is validated at the boundary
(rule 4), a schema failure gets exactly one retry with the validation message
appended and then the notice is parked (rule 2), every attempt is logged including
the failures because a failed call costs what a successful one costs, and the
request body is never logged (rule 20).

What the model is sent is fixed by Architecture v0.4 section 8 and BUILD_ORDER step
6, and it is public notice text and metadata only (rule 19): language, title,
buyer, country, admin level, published and deadline dates, CPV codes, the stated
value, the source URL, the first 3,000 tokens of the body in its original language,
and the machine translation where one exists. No reviewer name, no staff name, no
contact detail, no business record, no prior candidate. The pipeline holds none of
those to send, which is a property of the architecture rather than a rule anyone
has to remember.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import psycopg
import structlog
from pydantic import ValidationError

from monitor import caps
from monitor.models import Notice, Score
from monitor.score.prompt import body_budget, system_prompt
from monitor.score.schema import TOOL_NAME, tool_choice, tool_definition

log = structlog.get_logger(__name__)

# Architecture v0.4 section 8: "Haiku 4.5 translates and scores everything that
# passes the filter." Sonnet 5 appears only at step 20's rescore, for the 40 to 70
# band, and is a scoring decision rather than a second path.
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 2048
PURPOSE = "score"


class SchemaError(Exception):
    """The model returned something invalid twice. The caller parks the notice."""


@dataclass(frozen=True)
class ScoreResult:
    score: Score
    raw_input: dict
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: float
    attempts: int


def user_message(notice: Notice, *, title_en: str = "", body_en: str = "") -> str:
    """Everything the model is told about one notice, and nothing else (rule 19)."""
    lines = [
        f"Language: {notice.language}",
        f"Title: {notice.title}",
        f"Buyer: {notice.buyer or 'not stated'}",
        f"Country: {notice.country}",
        f"Level of government: {notice.admin_level}",
        f"Published: {notice.published_at.date().isoformat() if notice.published_at else 'not stated'}",
        f"Deadline: {notice.deadline_at.date().isoformat() if notice.deadline_at else 'not stated'}",
        f"CPV codes: {', '.join(notice.cpv_codes) if notice.cpv_codes else 'none'}",
        f"Stated value (USD): {notice.estimated_value_usd if notice.estimated_value_usd else 'not stated'}",
        f"Source URL: {notice.url}",
    ]

    if title_en and title_en != notice.title:
        # Marked as machine output so the model weighs it as a rendering rather
        # than as the notice's own words. The original is above it either way
        # (rule 9): this is an aid, not a replacement.
        lines.append(f"\nEnglish title (machine translation): {title_en}")

    lines.append(f"\nBody (original language, first {body_budget()} characters):\n{notice.body[: body_budget()]}")

    if body_en:
        lines.append(f"\nEnglish body (machine translation):\n{body_en[: body_budget()]}")

    return "\n".join(lines)


def score_notice(
    conn: psycopg.Connection,
    client: anthropic.Anthropic,
    notice: Notice,
    *,
    title_en: str = "",
    body_en: str = "",
    prompt_version: str,
) -> ScoreResult:
    """One notice to a Score. Raises CapExceeded before calling, SchemaError after two tries."""
    caps.check(conn, purpose=PURPOSE)

    messages = [{"role": "user", "content": user_message(notice, title_en=title_en, body_en=body_en)}]
    total_cost = 0.0
    tokens_in = tokens_out = latency_ms = 0
    last_error = ""

    for attempt in (1, 2):
        started = time.monotonic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            # Marked for caching: the block is ~7,500 tokens and identical for every
            # notice in a run, so it is paid once per window and then at a tenth.
            system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=[tool_definition()],
            tool_choice=tool_choice(),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        tokens_in, tokens_out = response.usage.input_tokens, response.usage.output_tokens

        total_cost += caps.record(
            conn,
            purpose=PURPOSE,
            model=MODEL,
            prompt_version=prompt_version,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )

        raw_input = _tool_input(response)
        try:
            score = Score.model_validate(raw_input)
        except ValidationError as error:
            last_error = str(error)
            log.warning("score_schema_invalid", attempt=attempt, prompt_version=prompt_version)
            if attempt == 2:
                break
            # The one documented retry (rule 2). Same model, same prompt, same tool.
            messages = [
                *messages,
                {"role": "assistant", "content": [{"type": "text", "text": str(raw_input)}]},
                {
                    "role": "user",
                    "content": f"That did not validate: {last_error}. Call record_score again, valid this time.",
                },
            ]
            continue

        return ScoreResult(
            score=score,
            raw_input=raw_input,
            model=MODEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=total_cost,
            attempts=attempt,
        )

    raise SchemaError(f"score failed validation twice: {last_error}")


def _tool_input(response) -> dict:
    """The forced tool's arguments. No tool call is itself a schema failure."""
    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            return dict(block.input)
    return {}
