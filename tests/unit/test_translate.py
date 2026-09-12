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
    SchemaError,
    TranslationOutput,
    acronyms_in,
    body_chars,
    dropped_acronyms,
    system_names,
    system_prompt,
    translate,
    user_message,
)


def message_response(text: str, *, tokens_in: int = 800, tokens_out: int = 200, stop_reason: str = "end_turn") -> dict:
    """The shape the Messages API returns, as the SDK expects to parse it."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


def client_that_runs_out_of_room() -> anthropic.Anthropic:
    """A response cut off at the token limit: valid prefix, no closing brace."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        cut = '{"title_en":"Recruitment of a consultant for the public financial man'
        return httpx2.Response(200, json=message_response(cut, tokens_out=4096, stop_reason="max_tokens"))

    return anthropic.Anthropic(
        api_key="test-key-not-real", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )


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

    # Returned in config order, which is deterministic; the set is what matters.
    assert set(dropped_acronyms(original, translated)) == {"TSA", "SIGFiP"}


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

    assert set(system_names()) == expected


def test_the_prompt_names_every_system_name_it_checks_for():
    """The one copy rule 6 asks for: the prompt and the check read the same list.

    A name the model is never told about is one it will translate away, and the
    check would then report a drop the prompt never tried to prevent.
    """
    prompt = system_prompt()

    for name in system_names():
        assert name in prompt, f"{name} is checked for but the model is never told to keep it"


def test_the_prompt_version_changes_when_the_name_list_changes(monkeypatch):
    """A translation is traceable to the list that was in force when it was made."""
    from monitor.translate import client
    from monitor.translate.run import prompt_version

    base = list(system_names())
    before = prompt_version(system_prompt())

    monkeypatch.setattr(client, "load_system_names", lambda: [*base, "NEWSYS"])
    client._cached_system_names.cache_clear()
    after = prompt_version(client.system_prompt())
    client._cached_system_names.cache_clear()

    assert before != after


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
    assert body["system"] == system_prompt()
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
    """Rule 22: a failed call costs the same as a successful one.

    Counted against a baseline taken in this test rather than against the whole
    table. The version that asserted `count == 2` outright passed only while
    model_calls was empty and broke the first time the deployment had really
    translated anything: it had 640 rows in it and the assertion read 642.
    """
    client, _ = client_returning("nope", "still nope")
    before = db_conn.execute("select count(*), coalesce(sum(cost_usd), 0) from model_calls").fetchone()

    with pytest.raises(SchemaError):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    after = db_conn.execute("select count(*), coalesce(sum(cost_usd), 0) from model_calls").fetchone()
    assert after[0] - before[0] == 2, "both attempts logged, the failed one included"
    assert after[1] - before[1] > 0


def test_the_response_schema_rejects_an_invented_field():
    with pytest.raises(ValidationError, match="summary"):
        TranslationOutput.model_validate({"title_en": "A title", "summary": "invented"})


def test_an_empty_title_is_invalid():
    with pytest.raises(ValidationError, match="title_en"):
        TranslationOutput.model_validate({"title_en": "", "body_en": "text"})


# --- the body cap and the truncated response --------------------------------


def test_the_body_sent_is_cut_to_the_configured_cap():
    """Rule 6: the number is in config/thresholds.yaml, not here.

    The scorer never reads past its own equal cap, so translating further buys
    English nobody looks at - and, more to the point, a body long enough to overflow
    the response comes back as unparseable JSON rather than as a short translation.
    """
    cap = body_chars()
    message = user_message(language="pl", title="Tytul", body="x" * (cap * 4))

    assert message.count("x") == cap
    assert user_message(language="pl", title="T", body="short").count("short") == 1


def test_a_response_cut_off_at_the_token_limit_says_so(db_conn):
    """Rule 4, and the message is the whole point of this test.

    Left to the validator, a truncated response surfaces as "Invalid JSON: EOF while
    parsing a string at line 1 column 19371", which reads as a parser bug and sent
    one reader looking for one. It is a capacity failure and the error has to name
    the limit, the body size and the fact that retrying cannot help.
    """
    client = client_that_runs_out_of_room()

    with pytest.raises(SchemaError, match="token limit and was cut off"):
        translate(db_conn, client, language="pl", title="Tytul", body="x" * 500, prompt_version="v")


def test_a_truncated_response_is_not_retried(db_conn):
    """The one documented retry (rule 2) is for a model that answered badly. The same
    input truncating again is not a second chance, it is a second bill."""
    client = client_that_runs_out_of_room()
    before = db_conn.execute("select count(*) from model_calls").fetchone()[0]

    with pytest.raises(SchemaError):
        translate(db_conn, client, language="pl", title="Tytul", body="x" * 500, prompt_version="v")

    after = db_conn.execute("select count(*) from model_calls").fetchone()[0]
    assert after - before == 1, "one call, not two"
