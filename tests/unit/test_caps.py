"""The guard in front of every model call (rule 22).

The property that matters: the cap is checked *before* the request, so a run that
would exceed it makes no HTTP call at all. The test proves that by mocking at the
transport layer and asserting nothing was sent, rather than by patching the
function under test, which would prove only that the mock was called.
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from monitor import caps
from monitor.translate.client import MODEL, SchemaError, translate


def counting_client() -> tuple[anthropic.Anthropic, list]:
    sent: list = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [{"type": "text", "text": json.dumps({"title_en": "T", "body_en": "B"})}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    client = anthropic.Anthropic(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return client, sent


def fill_model_calls(conn, count: int, cost_each: float = 0.001) -> None:
    for _ in range(count):
        conn.execute(
            """
            insert into model_calls (purpose, model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms)
            values ('translate', %s, 'test', 100, 50, %s, 10)
            """,
            (MODEL, cost_each),
        )


def test_the_call_cap_stops_the_run_before_any_request(db_conn, monkeypatch):
    """The fourth call raises before the transport is touched."""
    monkeypatch.setenv("DAILY_CALL_CAP", "3")
    caps._thresholds.cache_clear()
    fill_model_calls(db_conn, 3)
    client, sent = counting_client()

    with pytest.raises(caps.CapExceeded, match="3 calls today"):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert sent == [], "a capped run must make no HTTP request at all"


def test_the_cost_cap_stops_the_run_before_any_request(db_conn, monkeypatch):
    monkeypatch.setenv("DAILY_USD_CAP", "1")
    monkeypatch.setenv("DAILY_CALL_CAP", "10000")
    caps._thresholds.cache_clear()
    fill_model_calls(db_conn, 5, cost_each=0.25)
    client, sent = counting_client()

    with pytest.raises(caps.CapExceeded, match="USD"):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert sent == []


def test_under_the_cap_the_call_goes_through(db_conn, monkeypatch):
    monkeypatch.setenv("DAILY_CALL_CAP", "5")
    caps._thresholds.cache_clear()
    fill_model_calls(db_conn, 2)
    client, sent = counting_client()

    translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert len(sent) == 1


def test_cost_is_computed_from_the_configured_rate_card():
    """100 input and 50 output tokens of Haiku: (100 x 1.00 + 50 x 5.00) / 1e6."""
    assert caps.cost_usd(MODEL, tokens_in=100, tokens_out=50) == pytest.approx(0.00035)


def test_a_cache_read_is_billed_at_a_tenth():
    full = caps.cost_usd(MODEL, tokens_in=1000, tokens_out=0)
    cached = caps.cost_usd(MODEL, tokens_in=1000, tokens_out=0, cache_read_tokens=1000)

    assert cached == pytest.approx(full * 0.1)


def test_an_unpriced_model_raises_rather_than_costing_nothing():
    """A model with no rate would otherwise log every call at zero and never cap."""
    with pytest.raises(KeyError, match="no rate for model"):
        caps.cost_usd("claude-opus-5", tokens_in=100, tokens_out=50)


def test_the_caps_come_from_config_and_the_environment_overrides_them(monkeypatch):
    monkeypatch.delenv("DAILY_CALL_CAP", raising=False)
    monkeypatch.delenv("DAILY_USD_CAP", raising=False)
    caps._thresholds.cache_clear()

    from_config = caps.caps()
    assert (from_config.calls, from_config.usd) == (600, 25.0)

    monkeypatch.setenv("DAILY_CALL_CAP", "2")
    assert caps.caps().calls == 2


def test_every_call_is_recorded_with_its_purpose_and_cost(db_conn):
    cost = caps.record(
        db_conn,
        purpose="translate",
        model=MODEL,
        prompt_version="abc123",
        tokens_in=1000,
        tokens_out=500,
        latency_ms=420,
    )

    row = db_conn.execute(
        "select purpose, model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms "
        "from model_calls order by id desc limit 1"
    ).fetchone()

    assert row[:5] == ("translate", MODEL, "abc123", 1000, 500)
    assert float(row[5]) == pytest.approx(cost)
    assert row[6] == 420


def test_a_schema_failure_still_counts_against_the_cap(db_conn):
    """A failed call costs the same as a successful one."""
    before = caps.spend_today(db_conn)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "msg",
                "type": "message",
                "role": "assistant",
                "model": MODEL,
                "content": [{"type": "text", "text": "not json"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    client = anthropic.Anthropic(api_key="t", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    with pytest.raises(SchemaError):
        translate(db_conn, client, language="fr", title="T", body="", prompt_version="v")

    after = caps.spend_today(db_conn)
    assert after.calls == before.calls + 2
    assert after.usd > before.usd
