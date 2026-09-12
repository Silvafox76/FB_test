"""The scoring call, driven through a real SDK client and a mocked transport.

Same approach as the translation tests: a real `anthropic.Anthropic`, a real
request built and serialised, a fake transport returning recorded shapes. What is
asserted is what actually goes over the wire, because that is the part a patched
function would never check.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import anthropic
import httpx2
import pytest

from monitor import caps
from monitor.models import Notice
from monitor.score.client import MODEL, SchemaError, score_notice, user_message
from monitor.score.prompt import prompt_version, system_prompt
from monitor.score.schema import TOOL_NAME

VALID_SCORE = {
    "relevance": 78,
    "title_en": "Supply of an integrated financial management system",
    "matched_functions": [{"function_id": "treasury_accounting", "evidence": "IFMIS"}],
    "system_names": ["IFMIS"],
    "procurement_type": "system",
    "estimated_value_usd": 1_000_000,
    "eligibility_flags": [],
    "deadline_at": "2026-11-30",
    "summary_en": "A ministry of finance is buying an IFMIS.",
    "confidence": "high",
}


@pytest.fixture(autouse=True)
def pinned_route(monkeypatch):
    """Every test here states its own route rather than inheriting the machine's.

    Since step 12 the request's model name depends on `MODEL_ROUTE`, so a developer
    with `MODEL_ROUTE=bedrock` in their .env would otherwise see the direct-route
    tests below fail on an inference profile id. The bedrock tests at the bottom set
    it themselves and win, because they set it after this.
    """
    monkeypatch.setenv("MODEL_ROUTE", "direct")
    monkeypatch.delenv("BEDROCK_INFERENCE_GEO", raising=False)


def notice(**overrides) -> Notice:
    payload = {
        "content_hash": "sha256:test",
        "source_id": "ted",
        "external_id": "619297-2026",
        "url": "https://ted.europa.eu/en/notice/-/detail/619297-2026",
        "title": "Fourniture d'un système de gestion des finances publiques",
        "buyer": "Ministère des Finances",
        "country": "SN",
        "admin_level": "national",
        "published_at": datetime(2026, 9, 9, tzinfo=UTC),
        "deadline_at": datetime(2026, 11, 30, tzinfo=UTC),
        "language": "fr",
        "language_confidence": 1.0,
        "cpv_codes": ["48440000"],
        "estimated_value_usd": None,
        "body": "Le ministère lance un appel d'offres pour un SIGFiP.",
        "status": "filtered_in",
    }
    payload.update(overrides)
    return Notice(**payload)


def tool_response(payload: dict | None, *, text: str = "", tokens_in: int = 7500, tokens_out: int = 300) -> dict:
    content = [{"type": "text", "text": text}] if text else []
    if payload is not None:
        content.append({"type": "tool_use", "id": "toolu_1", "name": TOOL_NAME, "input": payload})
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
    }


def client_returning(*documents) -> tuple[anthropic.Anthropic, list[dict]]:
    sent: list[dict] = []
    remaining = list(documents)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=remaining.pop(0))

    client = anthropic.Anthropic(
        api_key="test-key-not-real", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    return client, sent


# --- what goes over the wire -------------------------------------------------


def test_a_valid_answer_round_trips(db_conn):
    client, sent = client_returning(tool_response(VALID_SCORE))

    result = score_notice(db_conn, client, notice(), prompt_version="v1")

    assert result.score.relevance == 78
    assert result.attempts == 1
    assert result.raw_input == VALID_SCORE
    assert len(sent) == 1


def test_the_tool_is_forced_and_the_system_block_is_cached(db_conn):
    client, sent = client_returning(tool_response(VALID_SCORE))

    score_notice(db_conn, client, notice(), prompt_version="v1")

    body = sent[0]
    assert body["model"] == MODEL
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert [tool["name"] for tool in body["tools"]] == [TOOL_NAME]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["system"][0]["text"] == system_prompt()


def test_the_user_message_carries_notice_metadata_and_nothing_else(db_conn):
    """Rule 19: no reviewer, no staff name, no contact detail, no business record."""
    client, sent = client_returning(tool_response(VALID_SCORE))
    subject = notice()

    score_notice(db_conn, client, subject, prompt_version="v1")

    content = sent[0]["messages"][0]["content"]
    assert content == user_message(subject)
    for expected in [subject.title, subject.buyer, subject.country, subject.url, "48440000", "fr"]:
        assert expected in content
    assert len(sent[0]["messages"]) == 1


def test_the_body_is_truncated_to_the_configured_budget(db_conn):
    from monitor.score.prompt import body_budget

    long_body = "x" * (body_budget() * 3)
    content = user_message(notice(body=long_body))

    assert content.count("x") == body_budget()


def test_a_machine_translation_is_marked_as_one(db_conn):
    """The original is sent first and is what is stored; the rendering is an aid (rule 9)."""
    subject = notice()
    content = user_message(subject, title_en="Supply of a public finance management system")

    assert subject.title in content
    assert "machine translation" in content
    assert content.index(subject.title) < content.index("machine translation")


def test_an_english_notice_gets_no_duplicate_translation_line():
    subject = notice(language="en", title="Supply of an IFMIS")
    content = user_message(subject, title_en="Supply of an IFMIS")

    assert "machine translation" not in content


# --- the retry ---------------------------------------------------------------


def test_one_retry_then_schema_error(db_conn):
    """Rule 2's one documented retry: same model, same prompt, same forced tool."""
    invalid = dict(VALID_SCORE, relevance=101)
    client, sent = client_returning(tool_response(invalid), tool_response(invalid))

    with pytest.raises(SchemaError, match="twice"):
        score_notice(db_conn, client, notice(), prompt_version="v1")

    assert len(sent) == 2, "exactly two attempts, never a third"
    assert "did not validate" in sent[1]["messages"][-1]["content"]
    assert sent[1]["model"] == MODEL, "the retry does not escalate to a larger model"
    assert sent[1]["tool_choice"] == {"type": "tool", "name": TOOL_NAME}


def test_a_retry_that_succeeds_is_not_a_failure(db_conn):
    client, sent = client_returning(tool_response(dict(VALID_SCORE, confidence="certain")), tool_response(VALID_SCORE))

    result = score_notice(db_conn, client, notice(), prompt_version="v1")

    assert result.attempts == 2
    assert result.score.confidence == "high"


def test_no_tool_call_at_all_is_a_schema_failure(db_conn):
    """A model that answers in prose has not answered."""
    client, sent = client_returning(
        tool_response(None, text="I think this is quite relevant."),
        tool_response(None, text="Still prose."),
    )

    with pytest.raises(SchemaError):
        score_notice(db_conn, client, notice(), prompt_version="v1")

    assert len(sent) == 2


def test_every_attempt_is_logged_including_the_failures(db_conn):
    """Rule 22: a failed call costs what a successful one costs."""
    before = caps.spend_today(db_conn)
    invalid = dict(VALID_SCORE, relevance=101)
    client, _ = client_returning(tool_response(invalid), tool_response(invalid))

    with pytest.raises(SchemaError):
        score_notice(db_conn, client, notice(), prompt_version="v1")

    after = caps.spend_today(db_conn)
    assert after.calls == before.calls + 2
    assert after.usd > before.usd


def test_the_cap_is_checked_before_any_request(db_conn, monkeypatch):
    monkeypatch.setenv("DAILY_CALL_CAP", "1")
    caps._thresholds.cache_clear()
    db_conn.execute(
        "insert into model_calls (purpose, model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms)"
        " values ('score', %s, 'v', 10, 10, 0.001, 5)",
        (MODEL,),
    )
    client, sent = client_returning(tool_response(VALID_SCORE))

    with pytest.raises(caps.CapExceeded):
        score_notice(db_conn, client, notice(), prompt_version="v1")

    assert sent == []


def test_a_cached_system_block_costs_less_than_sending_it_fresh(db_conn):
    """The system block is ~7,500 tokens and identical per notice; caching is the point.

    The usage shape matters here and the earlier version of this test got it wrong.
    It set `cache_read_input_tokens` to 7,000 while leaving `input_tokens` at 7,500,
    which the API never reports: the counts are disjoint, so a call that read 7,000
    tokens from cache reports a SMALL `input_tokens` for what was left. Asserting
    against an impossible response is how this test came to certify arithmetic that
    subtracted the cache read from the fresh input and could go negative.
    """
    document = tool_response(VALID_SCORE)
    document["usage"]["input_tokens"] = 500  # what was not already cached
    document["usage"]["cache_read_input_tokens"] = 7000
    client, _ = client_returning(document)

    result = score_notice(db_conn, client, notice(), prompt_version="v1")

    all_fresh = caps.cost_usd(MODEL, tokens_in=7500, tokens_out=300)
    assert result.cost_usd < all_fresh
    assert result.cost_usd > 0


# --- the prompt --------------------------------------------------------------


def test_the_prompt_version_is_a_hash_of_the_prompt():
    assert prompt_version() == prompt_version()
    assert len(prompt_version()) == 12


def test_the_prompt_carries_what_the_architecture_says_it_should():
    """Section 8: rubric, the 33 functions with type weights, both lexicons,
    the system names, eligibility rules and the geography weights."""
    prompt = system_prompt()

    assert "relevance" in prompt
    assert "treasury_accounting" in prompt and "type weight" in prompt
    assert "exécution budgétaire" in prompt, "the French lexicon is not in the prompt"
    assert "SIGFiP" in prompt
    assert "national_only" in prompt
    assert "1.0: BF" in prompt or "1.0: BJ" in prompt


# --- the bedrock route (step 12) ---------------------------------------------


def bedrock_client_returning(*documents) -> tuple[anthropic.AnthropicBedrock, list[dict]]:
    """A real AnthropicBedrock over a mocked transport, as the direct-route helper is.

    The keys are fake and never leave the process: SigV4 signs with whatever it is
    given, so a mocked transport needs no AWS account and this test runs anywhere.
    They are passed explicitly rather than left to boto3 to resolve, because an
    unset credential sends botocore looking for the instance metadata endpoint and
    the test would hang on a machine that has one.
    """
    sent: list[dict] = []
    remaining = list(documents)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append({"url": str(request.url), "body": json.loads(request.content)})
        return httpx2.Response(200, json=remaining.pop(0))

    client = anthropic.AnthropicBedrock(
        aws_region="ca-central-1",
        aws_access_key="AKIAnotareal",
        aws_secret_key="notarealsecret",
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return client, sent


def test_bedrock_is_called_by_inference_profile_id_not_by_model_name(db_conn, monkeypatch):
    """The assertion is on the URL, because that is where Bedrock carries the model.

    `AnthropicBedrock` pops `model` out of the body and puts it in the path,
    `/model/{id}/invoke`. So the direct-route test's `body["model"] == MODEL` would
    pass here by finding nothing, and the one thing this step can get wrong - the
    logical name going over the wire unchanged - would not be caught.
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")
    client, sent = bedrock_client_returning(tool_response(VALID_SCORE))

    score_notice(db_conn, client, notice(), prompt_version="v1")

    assert sent[0]["url"].endswith("/model/us.anthropic.claude-haiku-4-5-20251001-v1:0/invoke")
    assert "model" not in sent[0]["body"], "Bedrock takes the model in the path, not the body"
    assert sent[0]["body"]["tool_choice"] == {"type": "tool", "name": TOOL_NAME}


def test_the_cutover_logs_the_logical_name_so_the_two_runs_compare(db_conn, monkeypatch):
    """Step 12's acceptance is `make golden` on Bedrock matching the direct-API run.

    That only means anything if both runs write the same `model` to `model_calls`.
    It is also what makes the row costable at all: `config/thresholds.yaml`'s rate
    card is keyed on the logical name, so a row saying
    `us.anthropic.claude-haiku-4-5-20251001-v1:0` would raise a KeyError in
    `caps.cost_usd` before it ever reached the cap arithmetic.
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "global")
    client, sent = bedrock_client_returning(tool_response(VALID_SCORE))

    result = score_notice(db_conn, client, notice(), prompt_version="v1")

    assert "global.anthropic" in sent[0]["url"], "the profile id did go over the wire"
    assert result.model == MODEL
    logged = db_conn.execute("select model from model_calls order by at desc limit 1").fetchone()[0]
    assert logged == MODEL


def test_the_retry_reaches_the_same_bedrock_profile(db_conn, monkeypatch):
    """Rule 2's one retry is the same model. Resolving the id once is what makes it so."""
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")
    invalid = dict(VALID_SCORE, relevance=101)
    client, sent = bedrock_client_returning(tool_response(invalid), tool_response(invalid))

    with pytest.raises(SchemaError):
        score_notice(db_conn, client, notice(), prompt_version="v1")

    assert len(sent) == 2
    assert sent[0]["url"] == sent[1]["url"]
