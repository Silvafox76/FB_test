"""The guard in front of every model call (rule 22).

Two numbers, both per day, both checked *before* the call rather than after it,
from `config/thresholds.yaml`: 2,000 calls and USD 25. Exceeding either stops the
run with an event, and the run stops rather than trimming its work quietly,
because a pipeline that silently does less is a pipeline nobody notices has
stopped working. The USD cap is the binding one by design; the call cap was raised
from 600 when one day of TED needed 1,132 translate calls.

This module is shared by every purpose that calls a model: translate at step 14,
score at step 6, rescore at step 20. One guard, not one per caller (rule 1).

Cost is computed here from the call's own token counts and the rate card in
`config/thresholds.yaml`, because the API does not return a price. Every call is
logged to `model_calls`, including the ones that failed validation: a failed call
costs the same as a successful one and the daily total has to say so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import psycopg
import structlog
import yaml

from monitor.registry.load import CONFIG_DIR

log = structlog.get_logger(__name__)

PER_MILLION = 1_000_000


class CapExceeded(Exception):
    """A call would exceed a daily cap. Raised before any request is made."""


@dataclass(frozen=True)
class Caps:
    calls: int
    usd: float


@dataclass(frozen=True)
class Spend:
    """What today has cost so far."""

    calls: int
    usd: float


@lru_cache(maxsize=1)
def _thresholds() -> dict:
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


def caps() -> Caps:
    """The daily caps. Environment overrides config, so a drill can set them to 2."""
    thresholds = _thresholds()
    return Caps(
        calls=int(os.environ.get("DAILY_CALL_CAP", thresholds["daily_call_cap"])),
        usd=float(os.environ.get("DAILY_USD_CAP", thresholds["daily_usd_cap"])),
    )


def cost_usd(
    model: str,
    tokens_in: int,
    tokens_out: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """What one call cost, from its token counts and the configured rate card.

    The four token counts the API reports are DISJOINT, and getting that wrong is
    how this function returned a negative number for 28 of the first 29 calls ever
    made on this deployment. `usage.input_tokens` is the fresh, uncached input
    only; `cache_read_input_tokens` and `cache_creation_input_tokens` are counted
    separately and are not included in it. The original code subtracted the cache
    reads from `tokens_in`, which is right for an API that reported a single
    inclusive total and wrong for this one: the scorer caches a ~7,500-token system
    block, so from the second call of a run onwards the cache read dwarfs the fresh
    input and the subtraction goes below zero.

    That mattered beyond a wrong number in a log. `spend_today` sums this column to
    enforce rule 22's USD cap, so negative rows made the day's spend read lower
    than it was and the cap more permissive than it was configured to be. A cost
    line that can go negative is not a cost line.

    Billing, as the four components actually work:
      fresh input       input_tokens                  x input rate
      cache write       cache_creation_input_tokens   x input rate x 1.25
      cache read        cache_read_input_tokens       x input rate x 0.10
      output            output_tokens                 x output rate
    """
    rate_card = _thresholds()["rate_card"]
    if model not in rate_card:
        raise KeyError(f"no rate for model {model!r}; add it to rate_card in config/thresholds.yaml")

    rates = rate_card[model]
    fresh = tokens_in * rates["input"]
    written = cache_write_tokens * rates["input"] * rate_card["cache_write_factor"]
    read = cache_read_tokens * rates["input"] * rate_card["cache_read_factor"]
    output = tokens_out * rates["output"]
    return (fresh + written + read + output) / PER_MILLION


def spend_today(conn: psycopg.Connection) -> Spend:
    calls, usd = conn.execute(
        "select count(*), coalesce(sum(cost_usd), 0) from model_calls where at >= date_trunc('day', now())"
    ).fetchone()
    return Spend(calls=calls, usd=float(usd))


def check(conn: psycopg.Connection, *, purpose: str) -> Spend:
    """Refuse the next call if it would take the day over either cap.

    Called before the request, not after. Returns today's spend so a caller can
    log it; raises `CapExceeded` otherwise.
    """
    limits = caps()
    spend = spend_today(conn)

    if spend.calls >= limits.calls:
        raise CapExceeded(f"{purpose}: {spend.calls} calls today, cap is {limits.calls}")
    if spend.usd >= limits.usd:
        raise CapExceeded(f"{purpose}: USD {spend.usd:.2f} today, cap is {limits.usd:.2f}")
    return spend


def record(
    conn: psycopg.Connection,
    *,
    purpose: str,
    model: str,
    prompt_version: str,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Log one call and return what it cost. Never logs the request body (rule 20).

    The two cache counters are stored alongside the cost so the cost is auditable:
    without them a cached call's figure cannot be checked against the counts beside
    it, which is how a sign error in `cost_usd` went unnoticed until the first real
    scoring run (migration 010).
    """
    cost = cost_usd(model, tokens_in, tokens_out, cache_read_tokens, cache_write_tokens)
    conn.execute(
        """
        insert into model_calls (purpose, model, prompt_version, tokens_in, tokens_out,
                                 cost_usd, latency_ms, cache_read_tokens, cache_write_tokens)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            purpose,
            model,
            prompt_version,
            tokens_in,
            tokens_out,
            cost,
            latency_ms,
            cache_read_tokens,
            cache_write_tokens,
        ),
    )
    log.info(
        "model_call",
        purpose=purpose,
        model=model,
        prompt_version=prompt_version,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=round(cost, 6),
        latency_ms=latency_ms,
    )
    return cost
