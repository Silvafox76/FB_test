"""Which credential the model client uses, and that it is chosen rather than found.

Rule 1: one correct path. Three credential routes exist because three deployments
exist, but nothing detects which one it is in and nothing falls back from one to
the other. `MODEL_ROUTE` says, and an unset or wrong value is an error rather than
a guess.

The property worth the test is what leaves the process. On the proxy route the
request must carry no auth header at all, because the credential is attached after
it leaves the sandbox and a header already there would be the wrong one. On the
bedrock route the model is named by an inference profile id rather than by the name
the direct API takes, so the route decides two things and both are asserted here.
"""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from monitor.cli import DEFAULT_AWS_REGION, MODEL_ROUTES, model_client, model_id, model_route


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    # AWS_REGION and BEDROCK_INFERENCE_GEO are cleared for the same reason as the
    # rest: a developer machine that happens to have them set would otherwise decide
    # what these tests assert.
    for name in (
        "MODEL_ROUTE",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "AWS_REGION",
        "BEDROCK_INFERENCE_GEO",
    ):
        monkeypatch.delenv(name, raising=False)


def auth_headers_sent(client: anthropic.Anthropic, seen: dict) -> dict:
    client.messages.create(model="claude-haiku-4-5", max_tokens=8, messages=[{"role": "user", "content": "hi"}])
    return {name: value for name, value in seen.items() if name in ("x-api-key", "authorization")}


def recording_transport() -> tuple[httpx2.Client, dict]:
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.update({name.lower(): value for name, value in request.headers.items()})
        return httpx2.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    return httpx2.Client(transport=httpx2.MockTransport(handler)), seen


# --- the route is chosen, never detected -------------------------------------


def test_the_default_route_is_direct():
    assert model_route() == "direct"


def test_an_unknown_route_raises_naming_the_valid_ones(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "guess")

    with pytest.raises(RuntimeError, match="direct, proxy, bedrock"):
        model_route()


def test_the_route_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "  PROXY ")

    assert model_route() == "proxy"


def test_bedrock_is_the_third_route(monkeypatch):
    """Step 12's cutover. Until it landed, asking for it was an error; now it is chosen."""
    assert "bedrock" in MODEL_ROUTES
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")

    assert model_route() == "bedrock"


# --- the direct route --------------------------------------------------------


def test_the_direct_route_without_a_key_says_what_to_do(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "direct")

    with pytest.raises(RuntimeError) as raised:
        model_client()

    message = str(raised.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "MODEL_ROUTE=proxy" in message


def test_the_direct_route_sends_the_key(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "direct")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    transport, seen = recording_transport()

    client = anthropic.Anthropic(api_key="sk-ant-test-not-real", http_client=transport)

    assert auth_headers_sent(client, seen) == {"x-api-key": "sk-ant-test-not-real"}


def test_an_auth_token_also_satisfies_the_direct_route(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "direct")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-not-real")

    assert model_client() is not None


# --- the proxy route ---------------------------------------------------------


def test_the_proxy_route_needs_no_key(monkeypatch):
    """The whole point: the key is never in the environment."""
    monkeypatch.setenv("MODEL_ROUTE", "proxy")

    assert model_client() is not None


def test_the_proxy_route_sends_no_auth_header_at_all(monkeypatch):
    """The credential is attached after the request leaves; a header here is wrong."""
    monkeypatch.setenv("MODEL_ROUTE", "proxy")
    transport, seen = recording_transport()

    client = anthropic.Anthropic(api_key=None, default_headers={"X-Api-Key": anthropic.omit}, http_client=transport)

    assert auth_headers_sent(client, seen) == {}
    assert seen, "the request was never made"


def test_a_stale_key_in_the_environment_never_reaches_the_wire(monkeypatch):
    """The assertion has to be on the request, not on the client.

    `api_key=None` does not stop the SDK reading ANTHROPIC_API_KEY from the
    environment, so `client.api_key` is populated on the proxy route when a stale
    key happens to be set. What matters is that the omitted header wins and the
    request leaves carrying nothing, so the proxy attaches the right credential
    rather than the sandbox sending the wrong one.
    """
    monkeypatch.setenv("MODEL_ROUTE", "proxy")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale-not-real")
    transport, seen = recording_transport()

    client = anthropic.Anthropic(api_key=None, default_headers={"X-Api-Key": anthropic.omit}, http_client=transport)

    assert client.api_key == "sk-ant-stale-not-real", "the SDK does read the environment"
    assert auth_headers_sent(client, seen) == {}, "but nothing reaches the wire"


# --- the bedrock route (step 12) ---------------------------------------------


def test_the_bedrock_route_builds_a_bedrock_client_signed_for_montreal(monkeypatch):
    """Architecture v0.4 section 6: ca-central-1, and the region is a knob (rule 6).

    The assertion is on the base URL rather than on the constructor argument,
    because the base URL is what the request is actually sent to and what SigV4
    signs for. A client built for the wrong region fails with a signature error,
    not a routing error, and the message does not say "region".
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")

    client = model_client()

    assert isinstance(client, anthropic.AnthropicBedrock)
    assert DEFAULT_AWS_REGION == "ca-central-1"
    assert str(client.base_url) == "https://bedrock-runtime.ca-central-1.amazonaws.com"


def test_the_bedrock_route_needs_no_model_credential(monkeypatch):
    """The point of the cutover: the instance profile signs, so there is no key to hold."""
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")

    assert model_client() is not None


def test_the_region_is_config_and_not_a_literal(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")
    monkeypatch.setenv("AWS_REGION", "ca-west-1")

    assert str(model_client().base_url) == "https://bedrock-runtime.ca-west-1.amazonaws.com"


# --- the model id, which is not the model name on this route ------------------


def test_the_logical_name_is_the_wire_name_off_bedrock(monkeypatch):
    for route in ("direct", "proxy"):
        monkeypatch.setenv("MODEL_ROUTE", route)
        assert model_id("claude-haiku-4-5") == "claude-haiku-4-5"


def test_bedrock_names_an_inference_profile(monkeypatch):
    """Neither model runs in-region in ca-central-1, so the id names a profile.

    Both strings are read from the AWS model cards, and the asymmetry is real:
    Haiku 4.5 carries a date suffix and Sonnet 5 does not.
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")

    assert model_id("claude-haiku-4-5") == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert model_id("claude-sonnet-5") == "us.anthropic.claude-sonnet-5"


def test_the_global_profile_is_the_other_choice(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "global")

    assert model_id("claude-haiku-4-5") == "global.anthropic.claude-haiku-4-5-20251001-v1:0"


def test_an_unset_geo_stops_the_run_rather_than_choosing_one(monkeypatch):
    """Where inference happens is a residency decision, so there is no default (rule 1)."""
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")

    with pytest.raises(RuntimeError, match="us, global"):
        model_id("claude-haiku-4-5")


def test_an_unknown_geo_raises_rather_than_falling_back_to_one(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "ca")

    with pytest.raises(RuntimeError, match="us, global"):
        model_id("claude-haiku-4-5")


def test_a_model_with_no_recorded_profile_raises(monkeypatch):
    """The failure mode this prevents: the logical name passed through to Bedrock.

    `/model/claude-haiku-4-5/invoke` is a 400 from AWS with a message about the
    model id, which reads as a Bedrock outage rather than as a missing table entry.
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")
    monkeypatch.setenv("BEDROCK_INFERENCE_GEO", "us")

    with pytest.raises(RuntimeError, match="claude-opus-5"):
        model_id("claude-opus-5")


def test_the_bedrock_route_refuses_to_start_without_a_geo(monkeypatch):
    """Startup, not the first notice: a run of a thousand should not get one in.

    `model_id` checks again per call, so this is not a second gate on a second
    path - it is the same check, moved early enough to be useful.
    """
    monkeypatch.setenv("MODEL_ROUTE", "bedrock")

    with pytest.raises(RuntimeError, match="us, global"):
        model_client()
