"""Translate every notice the filter held for want of a lexicon, then re-filter it.

Step 5 parks a notice at `needs translation` when its language has no lexicon: the
free filter cannot decide about it, so it is held rather than dropped. This is the
stage that releases those notices. On the day step 5 was measured that was 41 of
50 TED notices, so this is not an optimisation; it is what makes a source in a
language we do not speak usable at all.

Order matters and it is translate-then-filter, not translate-then-score. A
translated notice goes back through the same free filter as everything else,
against the English lexicon, so an irrelevant Polish notice is still dropped
before it costs a scoring call. Translation is cheap; scoring is not.

A notice whose translation fails validation twice is parked, not retried a third
time and not dropped: `parked` is a state a person looks at.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import anthropic
import psycopg
import structlog

from monitor import caps
from monitor.filter.run import NEEDS_TRANSLATION, decide, lexicons, pass_prefixes
from monitor.translate.client import SYSTEM_PROMPT, SchemaError, translate

log = structlog.get_logger(__name__)

# The language every translation produces, and the lexicon it is re-filtered
# against.
TARGET_LANGUAGE = "en"


@dataclass(frozen=True)
class TranslateCounts:
    considered: int = 0
    translated: int = 0
    passed: int = 0
    dropped: int = 0
    parked: int = 0
    flagged: int = 0
    cost_usd: float = 0.0


def prompt_version(system_prompt: str) -> str:
    """A stable id for the prompt that produced a translation (rule 9)."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]


def run(conn: psycopg.Connection, client: anthropic.Anthropic, limit: int = 0) -> TranslateCounts:
    """Translate held notices and re-filter them. `limit` 0 means all of them."""
    version = prompt_version(SYSTEM_PROMPT)
    prefixes = pass_prefixes()
    available = lexicons()

    query = """
        select id, source_id, language, title, coalesce(body, ''), cpv_codes
        from notices
        where status = 'detected' and filter_result = %s
        order by fetched_at
    """
    rows = conn.execute(
        query + (" limit %s" if limit else ""), (NEEDS_TRANSLATION, limit) if limit else (NEEDS_TRANSLATION,)
    ).fetchall()

    counts = TranslateCounts(considered=len(rows))
    translated = passed = dropped = parked = flagged = 0
    cost = 0.0

    for notice_id, source_id, language, title, body, cpv_codes in rows:
        bound = log.bind(notice_id=str(notice_id), source_id=source_id, language=language)
        try:
            result = translate(conn, client, language=language, title=title, body=body, prompt_version=version)
        except SchemaError as error:
            conn.execute("update notices set status = 'parked' where id = %s", (notice_id,))
            conn.commit()
            parked += 1
            bound.error("translate_parked", error=str(error))
            continue
        except caps.CapExceeded:
            conn.commit()
            bound.error("translate_cap_exceeded")
            raise

        translated += 1
        cost += result.cost_usd
        if result.dropped_acronyms:
            flagged += 1
            bound.warning("translation_dropped_acronyms", acronyms=result.dropped_acronyms)

        conn.execute(
            """
            insert into translations (notice_id, title_en, body_en, model, prompt_version,
                                      latency_ms, cost_usd)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (notice_id, prompt_version) do nothing
            """,
            (
                notice_id,
                result.output.title_en,
                result.output.body_en,
                result.model,
                version,
                result.latency_ms,
                result.cost_usd,
            ),
        )

        # Back through the same free filter, in English. An irrelevant notice is
        # still dropped before it costs a scoring call.
        outcome = decide(
            title=result.output.title_en,
            body=result.output.body_en,
            language=TARGET_LANGUAGE,
            cpv_codes=list(cpv_codes or []),
            prefixes=prefixes,
            available_lexicons=available,
        )
        if outcome.stage == "pass":
            passed += 1
        else:
            dropped += 1

        conn.execute(
            "update notices set status = %s, filter_result = %s where id = %s",
            (outcome.status, f"translated, {outcome.filter_result}", notice_id),
        )
        conn.commit()

    counts = TranslateCounts(
        considered=len(rows),
        translated=translated,
        passed=passed,
        dropped=dropped,
        parked=parked,
        flagged=flagged,
        cost_usd=cost,
    )
    log.info(
        "translate_run",
        considered=counts.considered,
        translated=counts.translated,
        passed=counts.passed,
        dropped=counts.dropped,
        parked=counts.parked,
        flagged=counts.flagged,
        cost_usd=round(counts.cost_usd, 4),
    )
    return counts
