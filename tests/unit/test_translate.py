"""The translation stage, driven through a mocked HTTP transport.

The client is exercised end to end: a real `anthropic.Anthropic` instance, a real
request built and serialised, and a fake transport returning recorded response
bodies. Patching `translate()` itself would test nothing, which is why step 6's
cap test asks for the same thing in the same words.

What needs a credential and is therefore not covered here: the recorded fixtures
in `tests/contract/fixtures/translate_*.json` are responses from real calls, and
`scripts/record_translation_fixtures.py` produces them. Until those exist the
schema-validity number step 14's acceptance asks for cannot be measured.
"""

from __future__ import annotations

import json

import anthropic

# The SDK is built on httpx2, not httpx, so the transport that mocks it has to be
# httpx2's. The connectors use plain httpx and are unaffected: they do not go
# through the SDK.
import httpx2
import pytest
from pydantic import ValidationError

from monitor.translate.client import (
    MODEL,
    SYSTEM_NAMES,
    SYSTEM_PROMPT,
    SchemaError,
    TranslationOutput,
    acronyms_in,
    dropped_acronyms,
    translate,
    user_message,
)


def message_response(text: str, *, tokens_in: int = 800, tokens_out: int = 200) -> dict:
    """The shape the Messages API returns, as the SDK expects to parse it."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


def client_returning(*bodies: str) -> tuple[anthropic.Anthropic, list[dict]]:
    """A real SDK client whose transport replays the given bodies in order."""
    sent: list[dict] = []
    remaining = list(bodies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=message_response(remaining.pop(0)))

    transport = httpx2.MockTransport(handler)
    client = anthropic.Anthropic(api_key="test-key-not-real", http_client=httpx2.Client(transport=transport))
    return client, sent


VALID = json.dumps({"title_en": "Supply of an IFMIS", "body_en": "The Ministry of Finance seeks an IFMIS."})


# --- the acronym check, which is the point of this stage ---------------------


def test_acronyms_are_found_on_word_boundaries():
    assert acronyms_in("Supply of an IFMIS for the TSA") == ["IFMIS", "TSA"]
    assert acronyms_in("Motsa district") == []


def test_a_dropped_acronym_is_reported():
    original = "Mise en place d'un SIGFiP et d'un compte unique du Trésor (TSA)"
    translated = "Implementation of a public financial management system and a treasury single account"

    assert dropped_acronyms(original, translated) == ["TSA", "SIGFiP"]


def test_a_preserved_acronym_is_not_reported():
    original = "Mise en place d'un SIGFiP"
    translated = "Implementation of a SIGFiP"

    assert dropped_acronyms(original, translated) == []


def test_an_acronym_the_original_never_had_is_not_reported():
    """Only losses matter. A model adding IFMIS is a different problem."""
    assert dropped_acronyms("A procurement notice", "Supply of an IFMIS") == []


def test_every_system_name_in_claude_md_is_checked():
    expected = {
        "IFMIS",
        "GIFMIS",
        "IPPIS",
        "TSA",
        "HRMIS",
        "ITAS",
        "e-procurement",
        "SIGIF",
        "SIGFiP",
        "AGFIS",
        "ISFU",
        "SIGMAP",
        "RACHAD",
        "KFMIS",
    }

    assert set(SYSTEM_NAMES) == expected


# --- the call itself ---------------------------------------------------------


def test_a_valid_response_round_trips(db_conn):
    client, sent = client_returning(VALID)

    result = translate(
        db_conn, client, language="fr", title="Fourniture d'un SIGFiP", body="", prompt_version="test1234"
    )

    assert result.output.title_en == "Supply of an IFMIS"
    assert result.attempts == 1
    assert result.cost_usd > 0
    assert len(sent) == 1


def test_the_request_carries_notice_text_and_nothing_else(db_conn):
    """Rule 19: no reviewer name, no staff name, no business record reaches a model."""
    client, sent = client_returning(VALID)

    translate(db_conn, client, language="de", title="Beschaffung", body="Ein IFMIS.", prompt_version="v")

    body = sent[0]
    assert body["model"] == MODEL
    assert body["system"] == SYSTEM_PROMPT
    assert body["messages"][0]["content"] == user_message(language="de", title="Beschaffung", body="Ein IFMIS.")
    assert len(body["messages"]) == 1


def test_one_retry_then_park(db_conn):
    """Rule 2's one documented retry: same model, same prompt, what was wrong appended."""
    client, sent = client_returning("not json at all", "still not json")

    with pytest.raises(SchemaError):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert len(sent) == 2, "exactly two attempts, never a third"
    assert "did not validate" in sent[1]["messages"][-1]["content"]
    assert sent[1]["model"] == MODEL, "the retry uses the same model, not a larger one"


def test_a_retry_that_succeeds_is_not_a_failure(db_conn):
    client, sent = client_returning("{ broken", VALID)

    result = translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert result.attempts == 2
    assert result.output.title_en == "Supply of an IFMIS"


def test_every_attempt_is_logged_including_the_failures(db_conn):
    """Rule 22: a failed call costs the same as a successful one."""
    client, _ = client_returning("nope", "still nope")

    with pytest.raises(SchemaError):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    calls = db_conn.execute("select count(*), sum(cost_usd) from model_calls where purpose = 'translate'").fetchone()
    assert calls[0] == 2
    assert calls[1] > 0


def test_the_response_schema_rejects_an_invented_field():
    with pytest.raises(ValidationError, match="summary"):
        TranslationOutput.model_validate({"title_en": "A title", "summary": "invented"})


def test_an_empty_title_is_invalid():
    with pytest.raises(ValidationError, match="title_en"):
        TranslationOutput.model_validate({"title_en": "", "body_en": "text"})
