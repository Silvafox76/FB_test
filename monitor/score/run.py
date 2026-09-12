"""Score every notice the free filter passed.

Reads notices at `filtered_in`, scores each one, writes a `scores` row and moves
the notice to `scored`. A notice whose answer fails validation twice is parked with
an event, not dropped and not retried a third time: `parked` is a state a person
looks at, and the event is how they find out it happened.

The English rendering is passed through where one exists. For TED that is the
title, which arrives with the notice for free; for a notice that went through step
14 it is the model's own translation. Either way the original is what is sent first
and what is stored (rule 9); the rendering is an aid.

**The rescore (step 20).** A score inside the band `config/thresholds.yaml` calls
`rescore_band` gets one more call, on Sonnet 5, with the scorer's own prompt and
tool, and the higher-confidence of the two answers is the one that stages.

Three things that could be mistaken for something the rules forbid, said here so
nobody has to guess:

*It is not a fallback (rule 1).* Nothing failed. Haiku returned a valid score and
that score is kept unless the escalation beats it. The band is a decision someone
took about where a small model's confidence is worth a second opinion, written in
config with a version history, not a branch the code takes when something goes
wrong.

*It is not rule 2's retry.* That retry rescues a notice which would otherwise have
no score at all and then parks it. This notice has a score. So the escalation gets
exactly one call and an answer that does not validate ends it, leaving the Haiku
score standing rather than buying a third opinion.

*It runs before staging, not after.* A candidate takes its score when the stager
clusters the notice, so an escalation afterwards would change a number nobody reads
again. The select below only considers notices no candidate has claimed yet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
import psycopg
import structlog
import yaml
from pydantic import ValidationError

from monitor import caps
from monitor.cli import model_id
from monitor.models import Notice, Score
from monitor.registry.load import CONFIG_DIR

# `_tool_input` is imported rather than copied. Two definitions of "the arguments
# of the forced tool" is how they come to disagree, and the underscore is
# client.py's business; making it public is an edit to a file step 20 does not
# touch.
from monitor.score.client import (
    MAX_TOKENS,
    PURPOSE,
    SchemaError,
    ScoreResult,
    _tool_input,
    score_notice,
    user_message,
)
from monitor.score.prompt import prompt_version, system_prompt
from monitor.score.schema import tool_choice, tool_definition

log = structlog.get_logger(__name__)

# Step 20's escalation model, and the only place Sonnet 5 appears in the pipeline:
# Architecture v0.4 section 8 gives Haiku 4.5 everything that passes the filter.
# This is the logical name, as in `score/client.py` - `model_id` translates it for
# the bedrock route, while `model_calls` and the rate card keep this string.
RESCORE_MODEL = "claude-sonnet-5"

# The `model_calls.purpose` column's comment in migration 001 already lists this
# value alongside score and translate.
RESCORE_PURPOSE = "rescore"

# Appendix C's three confidence labels, ordered once, here. `scores.confidence` has
# been text since migration 003, so "the higher-confidence result" needs an
# ordering written down: comparing the labels as strings would rank "low" above
# "high" and quietly invert the whole rule.
CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

RESCORE_ACTOR = f"system ({RESCORE_PURPOSE})"


@dataclass(frozen=True)
class ScoreCounts:
    considered: int = 0
    scored: int = 0
    parked: int = 0
    cost_usd: float = 0.0
    prompt_version: str = ""

    @property
    def schema_validity(self) -> float:
        """Share of notices that produced a valid Score. The tracked metric."""
        return self.scored / self.considered if self.considered else 0.0


SELECT_FILTERED_IN = """
    select n.id, n.content_hash, n.source_id, n.external_id, n.url, n.title, n.buyer, n.country,
           n.admin_level, n.published_at, n.deadline_at, n.language, n.language_confidence,
           n.cpv_codes, n.estimated_value_usd, coalesce(n.body, ''), n.filter_result, n.status,
           coalesce(t.title_en, ''), coalesce(t.body_en, '')
    from notices n
    left join translations t on t.notice_id = n.id
    where n.status = 'filtered_in'
    order by n.fetched_at
"""


def run(conn: psycopg.Connection, client: anthropic.Anthropic, limit: int = 0) -> ScoreCounts:
    """Score everything at filtered_in. `limit` 0 means all of it."""
    version = prompt_version()
    rows = conn.execute(SELECT_FILTERED_IN + (" limit %s" if limit else ""), (limit,) if limit else ()).fetchall()

    scored = parked = 0
    cost = 0.0

    for row in rows:
        notice_id, title_en, body_en = row[0], row[18], row[19]
        notice = _notice_from(row)
        bound = log.bind(notice_id=str(notice_id), source_id=notice.source_id)

        try:
            result = score_notice(conn, client, notice, title_en=title_en, body_en=body_en, prompt_version=version)
        except SchemaError as error:
            _park(conn, notice_id, str(error))
            parked += 1
            bound.error("score_parked", error=str(error))
            continue
        except caps.CapExceeded:
            conn.commit()
            bound.error("score_cap_exceeded", scored=scored, remaining=len(rows) - scored - parked)
            raise

        _write_score(conn, notice_id, result, version)
        conn.execute("update notices set status = 'scored' where id = %s", (notice_id,))
        conn.commit()
        scored += 1
        cost += result.cost_usd
        bound.info("scored", relevance=result.score.relevance, confidence=result.score.confidence)

    counts = ScoreCounts(considered=len(rows), scored=scored, parked=parked, cost_usd=cost, prompt_version=version)
    log.info(
        "score_run",
        considered=counts.considered,
        scored=counts.scored,
        parked=counts.parked,
        schema_validity=round(counts.schema_validity, 4),
        cost_usd=round(counts.cost_usd, 4),
        prompt_version=version,
    )
    return counts


def _notice_from(row) -> Notice:
    """One database row back into the validated model (rule 4, at every boundary)."""
    return Notice(
        content_hash=row[1],
        source_id=row[2],
        external_id=row[3] or "",
        url=row[4],
        title=row[5],
        buyer=row[6] or "",
        country=row[7],
        admin_level=row[8],
        published_at=row[9],
        deadline_at=row[10],
        language=row[11],
        language_confidence=row[12],
        cpv_codes=list(row[13] or []),
        estimated_value_usd=row[14],
        body=row[15],
        filter_result=row[16] or "",
        status=row[17],
    )


def _write_score(conn: psycopg.Connection, notice_id, result, version: str) -> None:
    import json

    score = result.score
    conn.execute(
        """
        insert into scores (notice_id, model, prompt_version, relevance, title_en, matched_functions,
                            system_names, procurement_type, estimated_value_usd, eligibility_flags,
                            deadline_at, summary_en, confidence, raw_json, tokens_in, tokens_out,
                            latency_ms, cost_usd)
        values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        """,
        (
            notice_id,
            result.model,
            version,
            score.relevance,
            score.title_en,
            json.dumps([f.model_dump() for f in score.matched_functions]),
            score.system_names,
            score.procurement_type,
            score.estimated_value_usd,
            score.eligibility_flags,
            score.deadline_at,
            score.summary_en,
            score.confidence,
            json.dumps(result.raw_input, default=str),
            result.tokens_in,
            result.tokens_out,
            result.latency_ms,
            result.cost_usd,
        ),
    )


def _park(conn: psycopg.Connection, notice_id, reason: str) -> None:
    """Park the notice and say so in the audit log, in one transaction."""
    with conn.transaction():
        conn.execute("update notices set status = 'parked' where id = %s", (notice_id,))
        conn.execute(
            """
            insert into events (entity_type, entity_id, action, actor, before, after)
            values ('notice', %s, 'parked', %s, 'filtered_in', %s)
            """,
            (str(notice_id), f"system ({PURPOSE})", reason[:500]),
        )


# --- the rescore (step 20) ---------------------------------------------------


@dataclass(frozen=True)
class RescoreCounts:
    in_band: int = 0
    escalated: int = 0
    replaced: int = 0
    held: int = 0
    abandoned: int = 0
    cost_usd: float = 0.0
    prompt_version: str = ""


def rescore_band() -> tuple[int, int]:
    """The `[low, high]` relevance band that earns a second opinion, from config.

    Read rather than hardcoded (rule 6): 40 and 70 are a tuning decision the weekly
    cadence at step 27 is expected to move, and moving it must be a config change
    with a version history rather than a deployment. Validated here because a band
    that arrived as `[70, 40]` or as a single number would otherwise select nothing
    and look like a quiet day (rule 4).
    """
    band = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))["rescore_band"]
    if not isinstance(band, list) or len(band) != 2:
        raise ValueError(f"rescore_band must be a two-element list, got {band!r}")
    low, high = int(band[0]), int(band[1])
    if not 0 <= low <= high <= 100:
        raise ValueError(f"rescore_band must be 0 <= low <= high <= 100, got {band!r}")
    return low, high


# One row per notice still waiting to be clustered whose current score is in band.
#
# `current_score` and `rendering` both exist because neither table holds one row
# per notice. `scores` is one row per scoring call by design, and `translations` is
# keyed on (notice_id, prompt_version), so a TED notice carries both its
# `ted-eforms`/`source-native` row and the step 14 machine translation: 630 of the
# 934 notices with a translation on 2026-09-12 have two. A plain join fans out over
# both and hands the caller the same notice twice, which for a paid model call is
# money rather than an untidy result set.
#
# `distinct on ... order by created_at desc` takes the latest of each. For the
# rendering that resolves to the machine translation on all 630 two-row notices,
# which is also the better text: TED's own rendering translates the CPV heading and
# leaves the buyer's words in the original language on 618 of 738 non-English TED
# notices. `prompt_version` is a second sort key only so the answer cannot depend on
# the planner. It decides nothing in practice - the two rows are written by
# different modules in different transactions, and no notice in the database has
# two translations sharing a `created_at` - but a query whose result is arbitrary
# when they do is a query that behaves differently on a machine that is busier.
#
# Both CTEs carry a second sort key for the same reason: a tie on `created_at`
# would otherwise leave the planner to choose. Two `scores` rows for one notice is
# not hypothetical either - 75 of the 146 scored notices have two on 2026-09-12,
# because `SELECT_FILTERED_IN` above joins `translations` without narrowing it to
# one row and so scores those notices twice. That is a defect in the scorer rather
# than in this query, and fixing it belongs to a step that owns the scorer.
#
# `prompt_version` must match the prompt in force now, because step 20 says the
# second call uses "the same prompt". A notice scored under an older prompt is not
# comparable to a Sonnet answer under this one; it needs re-scoring, not escalating.
#
# The `events` clause is what stops a notice being escalated twice. A rescore always
# writes its event, including when the escalation is abandoned, so the marker exists
# however the call turned out.
SELECT_IN_BAND = """
    with current_score as (
        select distinct on (s.notice_id)
               s.notice_id, s.id as score_id, s.relevance, s.confidence, s.model, s.prompt_version
        from scores s
        order by s.notice_id, s.created_at desc, s.id
    ),
    rendering as (
        select distinct on (t.notice_id) t.notice_id, t.title_en, t.body_en
        from translations t
        order by t.notice_id, t.created_at desc, t.prompt_version
    )
    select n.id, n.content_hash, n.source_id, n.external_id, n.url, n.title, n.buyer, n.country,
           n.admin_level, n.published_at, n.deadline_at, n.language, n.language_confidence,
           n.cpv_codes, n.estimated_value_usd, coalesce(n.body, ''), n.filter_result, n.status,
           coalesce(r.title_en, ''), coalesce(r.body_en, ''),
           c.score_id, c.relevance, c.confidence, c.model
    from notices n
    join current_score c on c.notice_id = n.id
    left join rendering r on r.notice_id = n.id
    where n.status = 'scored'
      and c.relevance between %s and %s
      and c.prompt_version = %s
      and not exists (select 1 from candidate_notices cn where cn.notice_id = n.id)
      and not exists (
          select 1 from events e
          where e.entity_type = 'notice' and e.entity_id = n.id::text and e.action = 'rescored')
    order by n.fetched_at
"""


def rescore(conn: psycopg.Connection, client: anthropic.Anthropic, limit: int = 0) -> RescoreCounts:
    """Escalate every in-band score to Sonnet 5 once. `limit` 0 means all of them.

    `limit` is not a convenience. Each row here is a paid call on the larger model
    - USD 0.022 for the first in a run and about a third of that for the rest,
    measured 2026-09-12 - so a run is something a person sizes before starting.
    Nothing reports the band's depth yet: `monitor status` and `health/metrics.py`
    are another lane's files and neither knows this path exists.
    """
    version = prompt_version()
    low, high = rescore_band()
    rows = conn.execute(
        SELECT_IN_BAND + (" limit %s" if limit else ""),
        (low, high, version, limit) if limit else (low, high, version),
    ).fetchall()

    escalated = replaced = held = abandoned = 0
    cost = 0.0

    for row in rows:
        notice_id, score_id = row[0], row[20]
        stored_relevance, stored_confidence, stored_model = row[21], row[22], row[23]
        notice = _notice_from(row)
        bound = log.bind(notice_id=str(notice_id), source_id=notice.source_id, purpose=RESCORE_PURPOSE)
        before = f"{stored_model} relevance {stored_relevance} confidence {stored_confidence}"

        try:
            result = _escalate(conn, client, notice, title_en=row[18], body_en=row[19], version=version)
        except caps.CapExceeded:
            conn.commit()
            bound.error("rescore_cap_exceeded", escalated=escalated, remaining=len(rows) - escalated)
            raise

        escalated += 1
        cost += result.cost_usd if result else 0.0

        if result is None:
            _rescore_event(conn, notice_id, before, f"{RESCORE_MODEL} answer did not validate; {before} stands")
            conn.commit()
            abandoned += 1
            bound.warning("rescore_abandoned", stored_relevance=stored_relevance)
            continue

        escalated_score = result.score
        if sonnet_wins(stored_confidence, escalated_score.confidence):
            after = f"{RESCORE_MODEL} relevance {escalated_score.relevance} confidence {escalated_score.confidence}"
            with conn.transaction():
                _replace_score(conn, score_id, result, version)
                _rescore_event(conn, notice_id, before, after)
            replaced += 1
        else:
            after = (
                f"{stored_model} held; {RESCORE_MODEL} said relevance {escalated_score.relevance} "
                f"confidence {escalated_score.confidence}"
            )
            _rescore_event(conn, notice_id, before, after)
            held += 1

        conn.commit()
        bound.info("rescored", before=before, after=after, cost_usd=round(result.cost_usd, 6))

    counts = RescoreCounts(
        in_band=len(rows),
        escalated=escalated,
        replaced=replaced,
        held=held,
        abandoned=abandoned,
        cost_usd=cost,
        prompt_version=version,
    )
    log.info(
        "rescore_run",
        band=[low, high],
        in_band=counts.in_band,
        escalated=counts.escalated,
        replaced=counts.replaced,
        held=counts.held,
        abandoned=counts.abandoned,
        cost_usd=round(counts.cost_usd, 4),
        prompt_version=version,
    )
    return counts


def sonnet_wins(stored: str, escalated: str) -> bool:
    """Which of the two answers stages: the higher confidence, ties to Sonnet.

    The tie-break is what paying for the escalation bought. The band is where the
    small model's own confidence is worth a second opinion, so an equally confident
    second opinion from the larger model is the better-informed of the two, not a
    draw to be settled by leaving things as they were.

    An unknown label raises rather than sorting to the bottom (rule 4). The check
    constraint added in migration 003 allows three, and a fourth means either the
    schema or this dictionary is wrong and both should be looked at.
    """
    return CONFIDENCE_RANK[escalated] >= CONFIDENCE_RANK[stored]


def _escalate(
    conn: psycopg.Connection,
    client: anthropic.Anthropic | anthropic.AnthropicBedrock,
    notice: Notice,
    *,
    title_en: str,
    body_en: str,
    version: str,
) -> ScoreResult | None:
    """One Sonnet call, built exactly as the scorer builds its own. None if it does not validate.

    Same system prompt, same cached block, same forced tool, same body budget, same
    user message: the only difference between this call and the Haiku one is the
    model, which is the entire point of the comparison. The cap is checked before
    the request and the call is logged whatever it returns (rule 22).

    Measured on the first real call, 2026-09-12: 6,193 tokens written to cache, 586
    fresh input, 518 output, 13.5 seconds, USD 0.022. A second call inside the cache
    window is about a third of that.
    """
    caps.check(conn, purpose=RESCORE_PURPOSE)

    started = time.monotonic()
    response = client.messages.create(
        model=model_id(RESCORE_MODEL),
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_message(notice, title_en=title_en, body_en=body_en)}],
        tools=[tool_definition()],
        tool_choice=tool_choice(),
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    cost = caps.record(
        conn,
        purpose=RESCORE_PURPOSE,
        model=RESCORE_MODEL,
        prompt_version=version,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        latency_ms=latency_ms,
        cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    )

    raw_input = _tool_input(response)
    try:
        score = Score.model_validate(raw_input)
    except ValidationError as error:
        log.warning("rescore_schema_invalid", prompt_version=version, error=str(error))
        return None

    return ScoreResult(
        score=score,
        raw_input=raw_input,
        model=RESCORE_MODEL,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        latency_ms=latency_ms,
        cost_usd=cost,
        attempts=1,
    )


def _replace_score(conn: psycopg.Connection, score_id, result, version: str) -> None:
    """Write the winning answer over the score row the stager will read.

    The row is replaced rather than a second one inserted, and that is a deliberate
    trade. `stage/stager.py` joins `scores` on `notice_id`, so a second row would
    make the stager see one notice twice and settle the question with
    `greatest(score)` - the higher relevance, not the higher confidence, which is
    the opposite of what step 20 asks for. Leaving one row is the only way to make
    "the higher-confidence result is what stages" true without editing a module
    this step does not own.

    What is lost is the Haiku answer's `raw_json`. What is kept: both calls stay in
    `model_calls` with their tokens and cost, so the day's spend and rule 22's cap
    are unaffected, and the `rescored` event records both sides' relevance and
    confidence. Keeping the superseded row itself needs a `superseded_by` column and
    a stager that picks the winner - a migration, and a step that owns both.
    """
    import json

    score = result.score
    conn.execute(
        """
        update scores
           set model = %s, prompt_version = %s, relevance = %s, title_en = %s,
               matched_functions = %s::jsonb, system_names = %s, procurement_type = %s,
               estimated_value_usd = %s, eligibility_flags = %s, deadline_at = %s,
               summary_en = %s, confidence = %s, raw_json = %s::jsonb, tokens_in = %s,
               tokens_out = %s, latency_ms = %s, cost_usd = %s
         where id = %s
        """,
        (
            result.model,
            version,
            score.relevance,
            score.title_en,
            json.dumps([f.model_dump() for f in score.matched_functions]),
            score.system_names,
            score.procurement_type,
            score.estimated_value_usd,
            score.eligibility_flags,
            score.deadline_at,
            score.summary_en,
            score.confidence,
            json.dumps(result.raw_input, default=str),
            result.tokens_in,
            result.tokens_out,
            result.latency_ms,
            result.cost_usd,
            score_id,
        ),
    )


def _rescore_event(conn: psycopg.Connection, notice_id, before: str, after: str) -> None:
    """Say what the escalation did, and by existing, say it happened at all.

    This row is the only thing stopping the same notice being escalated again on the
    next run, so it is written on every outcome - replaced, held, or abandoned on an
    answer that did not validate.
    """
    conn.execute(
        """
        insert into events (entity_type, entity_id, action, actor, before, after)
        values ('notice', %s, 'rescored', %s, %s, %s)
        """,
        (str(notice_id), RESCORE_ACTOR, before[:500], after[:500]),
    )
