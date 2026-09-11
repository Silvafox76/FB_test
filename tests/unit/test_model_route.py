"""Which credential the model client uses, and that it is chosen rather than found.

Rule 1: one correct path. Two credential routes exist because two deployments
exist, but nothing detects which one it is in and nothing falls back from one to
the other. `MODEL_ROUTE` says, and an unset or wrong value is an error rather than
a guess.

The property worth the test is what leaves the process. On the proxy route the
request must carry no auth header at all, because the credential is attached after
it leaves the sandbox and a header already there would be the wrong one.
"""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from monitor.cli import MODEL_ROUTES, model_client, model_route


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("MODEL_ROUTE", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
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

    with pytest.raises(RuntimeError, match="direct, proxy"):
        model_route()


def test_the_route_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv("MODEL_ROUTE", "  PROXY ")

    assert model_route() == "proxy"


def test_bedrock_is_not_a_route_yet():
    """Step 12 adds it. Until then asking for it is an error, not a silent direct call."""
    assert "bedrock" not in MODEL_ROUTES


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
