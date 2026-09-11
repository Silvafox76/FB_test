"""Score every notice the free filter passed.

Reads notices at `filtered_in`, scores each one, writes a `scores` row and moves
the notice to `scored`. A notice whose answer fails validation twice is parked with
an event, not dropped and not retried a third time: `parked` is a state a person
looks at, and the event is how they find out it happened.

The English rendering is passed through where one exists. For TED that is the
title, which arrives with the notice for free; for a notice that went through step
14 it is the model's own translation. Either way the original is what is sent first
and what is stored (rule 9); the rendering is an aid.
"""

from __future__ import annotations

from dataclasses import dataclass

import anthropic
import psycopg
import structlog

from monitor import caps
from monitor.models import Notice
from monitor.score.client import PURPOSE, SchemaError, score_notice
from monitor.score.prompt import prompt_version

log = structlog.get_logger(__name__)


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
