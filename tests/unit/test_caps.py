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


def spend_now(conn) -> tuple[int, float]:
    """Today's calls and cost as they already stand.

    Every cap test below sets its cap relative to this rather than to zero. The
    versions that used absolute numbers passed only while model_calls was empty for
    the day, and broke the first time the deployment had really scored and
    translated anything: 761 calls sat in the table and a test asserting "cap is 3"
    tripped on the first line of the fixture. scripts/drills/drill3_call_cap.py had
    already learned this and computes its cap the same way.
    """
    spend = caps.spend_today(conn)
    return spend.calls, spend.usd


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
    """The call that would go over the line raises before the transport is touched."""
    calls, _ = spend_now(db_conn)
    fill_model_calls(db_conn, 3)
    cap = calls + 3
    monkeypatch.setenv("DAILY_CALL_CAP", str(cap))
    caps._thresholds.cache_clear()
    client, sent = counting_client()

    with pytest.raises(caps.CapExceeded, match=f"{cap} calls today"):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert sent == [], "a capped run must make no HTTP request at all"


def test_the_cost_cap_stops_the_run_before_any_request(db_conn, monkeypatch):
    _, usd = spend_now(db_conn)
    fill_model_calls(db_conn, 5, cost_each=0.25)
    # A full 0.25 of headroom rather than landing on the boundary: formatting the
    # cap to four places can round it a hair ABOVE the seeded spend, and then the
    # call goes through and the test fails for a reason that has nothing to do
    # with the cap.
    monkeypatch.setenv("DAILY_USD_CAP", f"{usd + 1.0:.4f}")
    monkeypatch.setenv("DAILY_CALL_CAP", "1000000")
    caps._thresholds.cache_clear()
    client, sent = counting_client()

    with pytest.raises(caps.CapExceeded, match="USD"):
        translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert sent == []


def test_under_the_cap_the_call_goes_through(db_conn, monkeypatch):
    """The control for the two above: the same setup, one call short of the cap."""
    calls, _ = spend_now(db_conn)
    fill_model_calls(db_conn, 2)
    monkeypatch.setenv("DAILY_CALL_CAP", str(calls + 5))
    caps._thresholds.cache_clear()
    client, sent = counting_client()

    translate(db_conn, client, language="fr", title="Titre", body="", prompt_version="v")

    assert len(sent) == 1


def test_cost_is_computed_from_the_configured_rate_card():
    """100 input and 50 output tokens of Haiku: (100 x 1.00 + 50 x 5.00) / 1e6."""
    assert caps.cost_usd(MODEL, tokens_in=100, tokens_out=50) == pytest.approx(0.00035)


def test_a_cache_read_is_billed_at_a_tenth_of_the_same_tokens_fresh():
    """The same 1,000 tokens, read from cache rather than sent fresh.

    Stated this way because the four counts the API reports are disjoint. The
    version of this test that passed before compared `tokens_in=1000` against
    `tokens_in=1000, cache_read_tokens=1000` and expected the second to be a tenth
    of the first, which is only true if the cached tokens are a subset of
    `tokens_in`. They are not, and the arithmetic that satisfied it subtracted one
    from the other and could go negative (migration 010).
    """
    fresh = caps.cost_usd(MODEL, tokens_in=1000, tokens_out=0)
    from_cache = caps.cost_usd(MODEL, tokens_in=0, tokens_out=0, cache_read_tokens=1000)

    assert from_cache == pytest.approx(fresh * 0.1)


def test_the_four_token_counts_are_disjoint_and_all_add_to_the_bill():
    """Fresh, cache write, cache read and output are four separate line items.

    The regression this pins: no combination of counts may produce a cost lower
    than the fresh-input-only cost, and none may go negative. `spend_today` sums
    this column to enforce the USD cap, so a negative row makes the day read
    cheaper than it was and the cap more permissive than configured.
    """
    fresh_only = caps.cost_usd(MODEL, tokens_in=600, tokens_out=200)
    with_read = caps.cost_usd(MODEL, tokens_in=600, tokens_out=200, cache_read_tokens=7500)
    with_write = caps.cost_usd(MODEL, tokens_in=600, tokens_out=200, cache_write_tokens=7500)

    assert with_read > fresh_only
    assert with_write > with_read, "writing the cache costs 1.25x input, reading it 0.1x"
    assert fresh_only > 0

    # The exact shape that recorded a negative cost on 28 of the first 29 real calls.
    assert caps.cost_usd(MODEL, tokens_in=614, tokens_out=189, cache_read_tokens=7500) > 0


def test_an_unpriced_model_raises_rather_than_costing_nothing():
    """A model with no rate would otherwise log every call at zero and never cap."""
    with pytest.raises(KeyError, match="no rate for model"):
        caps.cost_usd("claude-opus-5", tokens_in=100, tokens_out=50)


def test_the_cost_cap_is_the_one_that_binds():
    """Raising the call cap to 2,000 was safe because the USD cap did not move.

    One day of TED needed 1,132 translate calls against a 600 cap. 2,000 Haiku
    calls of that shape is about USD 8, still inside the USD 25 cap, so the cap
    that binds is the one denominated in the thing anyone actually cares about.
    """
    from monitor.translate.client import MODEL

    limits = caps.caps()
    a_full_day = limits.calls * caps.cost_usd(MODEL, tokens_in=1200, tokens_out=600)

    assert a_full_day < limits.usd, "the call cap can now exceed the cost cap; one of them is wrong"


def test_the_caps_come_from_config_and_the_environment_overrides_them(monkeypatch):
    monkeypatch.delenv("DAILY_CALL_CAP", raising=False)
    monkeypatch.delenv("DAILY_USD_CAP", raising=False)
    caps._thresholds.cache_clear()

    from_config = caps.caps()
    assert (from_config.calls, from_config.usd) == (2000, 25.0)

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
