"""Translate one notice into English. One model, one prompt, one retry.

The shape every model call in this system follows:

  - the cap is checked before the request (rule 22), never after;
  - the response is validated against a pydantic model at the boundary (rule 4);
  - a schema failure gets exactly one retry with the validation message appended,
    and then the notice is parked (rule 2). There is no third attempt, no second
    model and no second prompt;
  - the call is logged to `model_calls` whether it succeeded or not, because a
    failed call costs the same as a successful one;
  - the request body is never logged (rule 20).

The one thing specific to translation is the acronym check. The system names in
`config/system_names.yaml` are the strongest signal the filter and the scorer have, and a model
asked to translate a Ukrainian notice will happily render `СІФМІС` as "financial
system". If an acronym present in the original is missing from the translation the
result is flagged, not silently accepted: a dropped acronym is a lost opportunity
that nothing downstream can detect.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

import anthropic
import psycopg
import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from monitor import caps
from monitor.registry import load_system_names
from monitor.registry.load import CONFIG_DIR

log = structlog.get_logger(__name__)

# Checked against the SDK's model list at session start on 2026-09-11. Haiku is the
# translation model for the whole pilot; step 20's rescore is the only place a
# larger model appears, and it is a scoring decision, not a translation one.
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4096
PURPOSE = "translate"


def system_names() -> list[str]:
    """The system names, from config. One list, three readers (rule 6).

    Read through the registry rather than kept as a tuple here: the same names are
    what the free filter matches on in both lexicons and what step 6's prompt
    builder needs, and three Python copies of a keyword list is exactly what rule 6
    forbids. Cached, because it is read once per notice in a run of a thousand.
    """
    return _cached_system_names()


@lru_cache(maxsize=1)
def _cached_system_names() -> tuple[str, ...]:
    return tuple(load_system_names())


def system_prompt() -> str:
    """The translation instructions, with the system names interpolated from config.

    The prompt names them because a model that is not told will translate SIGFiP
    into "public financial management system" and mean well. Built from the config
    list so the prompt and the acronym check cannot disagree: if a name is added to
    config, the model is told about it and the check looks for it in the same pass,
    and `prompt_version` changes, so a translation made before the change is
    distinguishable from one made after.
    """
    names = ", ".join(system_names())
    return f"""You translate public procurement notices into English.

Rules:
- Translate the title and the body. Keep the meaning exact; this is a legal notice, not marketing copy.
- Keep every acronym, system name and product name exactly as written in the original. {names} and any
  similar name is a proper noun, not a phrase to translate.
- Keep numbers, currency amounts, dates and reference codes exactly as written.
- Do not summarise, do not shorten, do not add anything the original does not say.
- If the original is already English, return it unchanged."""


class TranslationOutput(BaseModel):
    """What the model is asked to return, and nothing else."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title_en: str = Field(min_length=1)
    body_en: str = ""


class SchemaError(Exception):
    """The model returned something invalid twice. The caller parks the notice."""


@dataclass(frozen=True)
class TranslationResult:
    output: TranslationOutput
    model: str
    latency_ms: int
    cost_usd: float
    attempts: int
    # Acronyms present in the original and missing from the translation. Non-empty
    # means flagged for a reviewer, not rejected: a flagged translation is still
    # better than no translation, and the flag is what makes it visible.
    dropped_acronyms: list[str] = field(default_factory=list)


def acronyms_in(text: str) -> list[str]:
    """Which of the known system names appear in this text, in list order."""
    return [name for name in system_names() if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE)]


def dropped_acronyms(original: str, translated: str) -> list[str]:
    """System names the original had and the translation lost."""
    present = set(acronyms_in(translated))
    return [name for name in acronyms_in(original) if name not in present]


@lru_cache(maxsize=1)
def _thresholds() -> dict:
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


def body_chars() -> int:
    """How much of a body is sent, from config/thresholds.yaml (rule 6)."""
    return int(_thresholds()["translate_body_chars"])


def user_message(*, language: str, title: str, body: str) -> str:
    """Public notice text and its language. Nothing else ever goes to a model (rule 19).

    The body is cut to `translate_body_chars`. The scorer never reads past its own
    equal cap, so translating further buys English nobody looks at - and a body long
    enough to overflow the 4,096-token response comes back as JSON cut off mid-string,
    which fails to parse rather than arriving short.
    """
    return f"Language: {language}\n\nTitle:\n{title}\n\nBody:\n{body[: body_chars()]}"


def translate(
    conn: psycopg.Connection,
    client: anthropic.Anthropic,
    *,
    language: str,
    title: str,
    body: str,
    prompt_version: str,
) -> TranslationResult:
    """One notice to English. Raises CapExceeded before calling, SchemaError after two tries."""
    caps.check(conn, purpose=PURPOSE)

    messages = [{"role": "user", "content": user_message(language=language, title=title, body=body)}]
    total_cost = 0.0
    last_error = ""

    for attempt in (1, 2):
        started = time.monotonic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt(),
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": TranslationOutput.model_json_schema()}},
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        total_cost += caps.record(
            conn,
            purpose=PURPOSE,
            model=MODEL,
            prompt_version=prompt_version,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            latency_ms=latency_ms,
        )

        # Rule 4: a response that ran out of room is a different failure from a
        # response that came back malformed, and the message has to say which. Left
        # to the validator it surfaces as "Invalid JSON: EOF while parsing a string
        # at column 19371", which sent one reader looking for a parser bug.
        if response.stop_reason == "max_tokens":
            raise SchemaError(
                f"translation response hit the {MAX_TOKENS}-token limit and was cut off mid-JSON; "
                f"the body sent was {body_chars()} characters. Lower translate_body_chars in "
                "config/thresholds.yaml or raise MAX_TOKENS, but do not retry: the same input "
                "will truncate again."
            )

        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            output = TranslationOutput.model_validate_json(text)
        except ValidationError as error:
            last_error = str(error)
            log.warning("translate_schema_invalid", attempt=attempt, prompt_version=prompt_version)
            if attempt == 2:
                break
            # The one documented retry (rule 2): the same model and the same prompt,
            # with what was wrong appended. Not a second model and not a second path.
            messages = [
                *messages,
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That did not validate: {last_error}. Return valid JSON only."},
            ]
            continue

        return TranslationResult(
            output=output,
            model=MODEL,
            latency_ms=latency_ms,
            cost_usd=total_cost,
            attempts=attempt,
            dropped_acronyms=dropped_acronyms(f"{title}\n{body}", f"{output.title_en}\n{output.body_en}"),
        )

    raise SchemaError(f"translation failed validation twice: {last_error}")
