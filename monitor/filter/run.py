"""The free filter: two stages, no model call, no cost.

Runs over every notice nobody has filtered yet and writes two things: a
`filter_result` sentence a reviewer can read, and a status. Nothing here calls a
model, and nothing here decides relevance; it decides only what is worth paying to
score.

The order is CPV then lexicon, cheapest first:

  1. CPV. Codes present and none passing -> dropped. No codes -> on to the lexicon.
  2. Lexicon, in the notice's own language (rule 9). A language with no lexicon
     stops here as "needs translation" and keeps status `detected`, so step 14
     picks it up rather than it being lost. No match in a language we do have ->
     dropped. A match -> `filtered_in`, waiting for the scorer.

`filter_result` is written on drops and passes alike. A reviewer asking why they
never saw a notice gets a sentence, not an absence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import psycopg
import structlog
import yaml

from monitor.filter import cpv as cpv_stage
from monitor.filter import lexicon as lexicon_stage
from monitor.registry.load import CONFIG_DIR, LEXICONS, load_lexicon

log = structlog.get_logger(__name__)

# How many matched phrases go into the sentence the reviewer reads.
PHRASES_IN_RESULT = 3

NEEDS_TRANSLATION = "needs translation"


@dataclass(frozen=True)
class FilterCounts:
    considered: int = 0
    passed: int = 0
    dropped_cpv: int = 0
    dropped_lexicon: int = 0
    needs_translation: int = 0

    @property
    def dropped(self) -> int:
        return self.dropped_cpv + self.dropped_lexicon

    @property
    def drop_rate(self) -> float:
        """Share of considered notices the free filter removed, 0 to 1."""
        return self.dropped / self.considered if self.considered else 0.0


# Which stage decided, as a value rather than as a substring of the sentence the
# reviewer reads. Counting by sniffing `filter_result` would couple the statistics
# to wording that exists to be read by a person and changed when it reads badly.
Stage = Literal["cpv", "lexicon", "translation", "pass"]


@dataclass(frozen=True)
class Outcome:
    """What the filter decided about one notice, and which stage decided it."""

    status: str
    filter_result: str
    stage: Stage


def pass_prefixes() -> list[str]:
    thresholds = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))
    return thresholds["cpv_pass_prefixes"]


def lexicons() -> dict[str, dict[str, list[str]]]:
    """Every language we can filter in, by two-letter code."""
    return {language: load_lexicon(language)[0] for language in LEXICONS}


def decide(
    *,
    title: str,
    body: str,
    language: str,
    cpv_codes: list[str],
    prefixes: list[str],
    available_lexicons: dict[str, dict[str, list[str]]],
) -> Outcome:
    """The whole filter as one pure function. No database, no clock, no model."""
    verdict = cpv_stage.check(cpv_codes, prefixes)
    if not verdict.passed:
        return Outcome(status="filtered_out", filter_result=verdict.reason, stage="cpv")

    if language not in available_lexicons:
        # Not a drop. Step 14 translates these; until then they stop here and are
        # counted, so nobody mistakes silence for an empty day.
        return Outcome(status="detected", filter_result=NEEDS_TRANSLATION, stage="translation")

    lexicon_verdict = lexicon_stage.check(title, body, available_lexicons[language])
    if not lexicon_verdict.matched:
        return Outcome(status="filtered_out", filter_result=f"no lexicon match ({language})", stage="lexicon")

    phrases = ", ".join(lexicon_verdict.phrases[:PHRASES_IN_RESULT])
    return Outcome(
        status="filtered_in",
        filter_result=f"{verdict.reason}, lexicon {language}: {phrases}",
        stage="pass",
    )


def run(conn: psycopg.Connection) -> FilterCounts:
    """Filter every notice that has not been filtered yet."""
    prefixes = pass_prefixes()
    available = lexicons()

    rows = conn.execute(
        """
        select id, source_id, title, coalesce(body, ''), language, cpv_codes
        from notices
        where status = 'detected' and coalesce(filter_result, '') = ''
        order by fetched_at
        """
    ).fetchall()

    considered = passed = dropped_cpv = dropped_lexicon = needs_translation = 0

    for notice_id, source_id, title, body, language, cpv_codes in rows:
        considered += 1
        outcome = decide(
            title=title,
            body=body,
            language=language,
            cpv_codes=list(cpv_codes or []),
            prefixes=prefixes,
            available_lexicons=available,
        )

        if outcome.stage == "pass":
            passed += 1
        elif outcome.stage == "translation":
            needs_translation += 1
        elif outcome.stage == "cpv":
            dropped_cpv += 1
        else:
            dropped_lexicon += 1

        conn.execute(
            "update notices set status = %s, filter_result = %s where id = %s",
            (outcome.status, outcome.filter_result, notice_id),
        )
        log.debug(
            "filtered",
            notice_id=str(notice_id),
            source_id=source_id,
            status=outcome.status,
            filter_result=outcome.filter_result,
        )

    conn.commit()
    counts = FilterCounts(
        considered=considered,
        passed=passed,
        dropped_cpv=dropped_cpv,
        dropped_lexicon=dropped_lexicon,
        needs_translation=needs_translation,
    )
    log.info(
        "filter_run",
        considered=counts.considered,
        passed=counts.passed,
        dropped_cpv=counts.dropped_cpv,
        dropped_lexicon=counts.dropped_lexicon,
        needs_translation=counts.needs_translation,
    )
    return counts
