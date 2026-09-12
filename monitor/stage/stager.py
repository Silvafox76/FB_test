"""Turn scored notices into candidates, and stage the ones worth a reviewer's time.

Two jobs that are one pass because they share a transaction per notice: the
deduper decides whether a scored notice joins an existing candidate or starts a new
one, and the stager decides which candidates reach the queue.

The staging rule is a threshold and a cap, and the cap is the part that matters.
A portal that publishes two hundred notices in a morning should not put two hundred
candidates in front of one person; `per_source_daily_cap` holds the rest at
`scored`, where they keep their score and can be staged tomorrow. Nothing is lost,
it is deferred, and `monitor status` shows the backlog.

Every transition writes an events row. Rule 13 is about approval specifically, but
the same principle applies here: a candidate that appeared, joined or was staged
should be explicable afterwards without re-running anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg
import structlog
import yaml

from monitor.dedupe.cluster import Candidate, Incoming, find_match
from monitor.fx.config import FxConfig
from monitor.fx.config import load as load_fx
from monitor.fx.convert import Converted, RateTable, convert
from monitor.fx.store import latest
from monitor.registry.load import CONFIG_DIR

log = structlog.get_logger(__name__)

ACTOR = "system (stager)"


@dataclass(frozen=True)
class StageCounts:
    scored_notices: int = 0
    candidates_created: int = 0
    joined: int = 0
    staged: int = 0
    held_by_cap: int = 0
    below_threshold: int = 0


def _config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


def stage_threshold() -> int:
    return int(_config()["stage_threshold"])


def per_source_daily_cap() -> int:
    return int(_config()["per_source_daily_cap"])


def region_for(country: str) -> str:
    regions = _config()["regions"]
    return regions.get(country, regions["default"])


# The top of the six-digit space `candidates_id_format` allows, reserved for test
# fixtures and drills. Not a tuning number and so not in thresholds.yaml (rule 6):
# it is a structural division of the id space the schema fixes at `C[0-9]{6}`, and
# moving it means moving every fixture with it.
#
# It exists because a fixture that picks an id at random from the whole space
# collides with a real candidate, and both halves of that collision are harmful. The
# insert fails, which aborts the fixture's setup *after* it has committed its source
# and notice rows on an autocommit owner connection, leaving an orphan that no query
# in the system can tell from real data - one was drawn into the golden set on
# 2026-09-12. And the teardown deletes by id, so a fixture that did not collide on
# insert but reused a real id would delete a real candidate.
#
# The reservation is declared here, next to the allocator, rather than in a test
# helper, because it is the allocator that has to respect it. A convention four
# fixture files follow independently is not a reservation; a sequence that refuses
# to cross the line is.
FIXTURE_ID_FLOOR = 900_000


def next_candidate_id(conn: psycopg.Connection) -> str:
    """C000001, from the sequence. Typed and read aloud by people, so not a uuid."""
    allocated = conn.execute("select nextval(%s)", ("candidate_id_seq",)).fetchone()[0]
    if allocated >= FIXTURE_ID_FLOOR:
        # Rule 4. Silently allocating into the reserved block would make the next
        # fixture teardown delete a real candidate, and nothing would report it.
        raise RuntimeError(
            f"candidate_id_seq has reached {allocated}, inside the block reserved for test "
            f"fixtures at {FIXTURE_ID_FLOOR}. Widen the id format in migrations and move "
            "FIXTURE_ID_FLOOR with it before staging anything else."
        )
    return f"C{allocated:06d}"


# One row per notice, and the `distinct on` is load-bearing rather than tidy.
#
# `scores` is one row per scoring CALL by design, so a notice legitimately carries
# more than one: a re-scored notice has two, and until the fix in
# `monitor/score/run.py` a notice with two translations was scored twice and also has
# two. Measured on 2026-09-12: 75 of the 146 scored notices carry two `scores` rows.
#
# This query used to `join scores` with nothing narrowing it, so those notices came
# back twice and the loop below read whichever row the planner returned first. That is
# not an untidy result set, it is a coin flip on the candidate's relevance — and
# relevance is what decides whether a candidate clears the staging threshold and
# reaches a reviewer at all. Measured across the 73 candidates built from a
# dual-scored notice: 16 took the higher of the two scores, 16 took the lower, 41 were
# tied. A 16/16 split is the signature of an arbitrary choice, not of a rule.
#
# `order by s.created_at desc, s.id` takes the most recent score, which is the same
# rule `SELECT_IN_BAND` in `monitor/score/run.py` already uses to decide what a
# notice's current score is. The stager and the rescorer must agree on that or a
# notice can be escalated on one score and staged on another. `s.id` is a second sort
# key only so the answer cannot depend on the planner when two rows share a timestamp.
SELECT_SCORED = """
    with current_score as (
        select distinct on (s.notice_id)
               s.notice_id, s.relevance, s.title_en, s.summary_en, s.matched_functions,
               s.system_names, s.procurement_type,
               s.eligibility_flags, s.deadline_at
        from scores s
        order by s.notice_id, s.created_at desc, s.id
    )
    select n.id, n.source_id, n.content_hash, n.country, n.admin_level, n.language, n.buyer,
           s.relevance, s.title_en, s.summary_en, s.matched_functions, s.system_names,
           s.procurement_type, n.estimated_value, n.value_currency, s.eligibility_flags,
           s.deadline_at, n.deadline_at
    from notices n
    join current_score s on s.notice_id = n.id
    where n.status = 'scored'
      and not exists (select 1 from candidate_notices cn where cn.notice_id = n.id)
    order by n.fetched_at
"""

SELECT_OPEN_CANDIDATES = """
    select c.id, c.country, c.title_en, c.system_names, c.deadline_at,
           coalesce(array_agg(n.content_hash) filter (where n.content_hash is not null), '{}')
    from candidates c
    left join candidate_notices cn on cn.candidate_id = c.id
    left join notices n on n.id = cn.notice_id
    where c.status in ('scored', 'staged', 'pending_review')
    group by c.id, c.country, c.title_en, c.system_names, c.deadline_at
"""


def load_candidates(conn: psycopg.Connection) -> list[Candidate]:
    return [
        Candidate(
            id=row[0],
            country=row[1],
            title_en=row[2],
            system_names=tuple(row[3] or ()),
            deadline_at=row[4],
            content_hashes=frozenset(row[5] or ()),
        )
        for row in conn.execute(SELECT_OPEN_CANDIDATES).fetchall()
    ]


def write_event(conn: psycopg.Connection, entity_id: str, action: str, before: str, after: str) -> None:
    conn.execute(
        """
        insert into events (entity_type, entity_id, action, actor, before, after)
        values ('candidate', %s, %s, %s, %s, %s)
        """,
        (entity_id, action, ACTOR, before, after),
    )


def run(conn: psycopg.Connection) -> StageCounts:
    """Dedupe every unclustered scored notice, then stage what clears the bar."""
    threshold = stage_threshold()
    cap = per_source_daily_cap()
    rows = conn.execute(SELECT_SCORED).fetchall()
    candidates = load_candidates(conn)

    # One rate table for the whole run, read once. Every candidate created by this
    # pass is stamped with the same rate and the same rate date, so two candidates
    # staged together can be compared without asking which minute each was written.
    # `latest` raises when there are no rates or the newest set is past the
    # tolerance in config/fx.yaml, which stops the run rather than quietly writing
    # a batch of records carrying last month's arithmetic (rule 4).
    #
    # The arithmetic itself is in monitor/fx/convert.py. The stager decides WHICH
    # rate table and WHICH amount; it does not know how a conversion works (rule 5).
    fx = load_fx()
    rates = latest(conn, fx)

    created = joined = 0

    for row in rows:
        notice_id, source_id, content_hash, country = row[0], row[1], row[2], row[3]
        relevance, title_en = row[7], row[8]
        system_names = tuple(row[11] or ())
        deadline = row[16] or row[17]
        converted = _converted(row[13], row[14], rates, fx)

        incoming = Incoming(
            notice_id=str(notice_id),
            country=country,
            title_en=title_en,
            system_names=system_names,
            deadline_at=deadline,
            content_hash=content_hash,
        )
        match = find_match(incoming, candidates)

        with conn.transaction():
            if match is None:
                candidate_id = next_candidate_id(conn)
                _insert_candidate(conn, candidate_id, row, deadline, relevance, converted)
                _link(conn, candidate_id, notice_id, "content_hash", 100)
                write_event(conn, candidate_id, "scored", "", f"relevance {relevance}, from {source_id}")
                created += 1
            else:
                candidate_id = match.candidate_id
                _link(conn, candidate_id, notice_id, match.method, match.score)
                # The candidate keeps the highest score of its notices: one source
                # describing a tender badly should not pull down another describing
                # it well.
                conn.execute(
                    "update candidates set score = greatest(score, %s), updated_at = now() where id = %s",
                    (relevance, candidate_id),
                )
                write_event(
                    conn,
                    candidate_id,
                    "duplicate_joined",
                    "",
                    f"{source_id} notice joined by {match.method} at {match.score}",
                )
                joined += 1

        candidates = load_candidates(conn)

    staged, held, below = _stage(conn, threshold, cap)
    conn.commit()

    counts = StageCounts(
        scored_notices=len(rows),
        candidates_created=created,
        joined=joined,
        staged=staged,
        held_by_cap=held,
        below_threshold=below,
    )
    log.info(
        "stage_run",
        scored_notices=counts.scored_notices,
        created=counts.candidates_created,
        joined=counts.joined,
        staged=counts.staged,
        held_by_cap=counts.held_by_cap,
        below_threshold=counts.below_threshold,
    )
    return counts


def _converted(amount, currency, rates: RateTable, fx: FxConfig) -> Converted | None:
    """The published amount in the target currency, or None when nothing is stated.

    Two different Nones meet here and they mean different things. No amount at all
    is "the notice states no value". An amount in a currency no rate covers - NGN,
    GHS, GMD, SLE, MRU today - also yields None for the USD column, but the
    candidate still carries the published figure and its currency, so the record
    shows what the tender is worth in the money the buyer named rather than an
    unexplained blank.
    """
    if amount is None or currency is None:
        return None
    return convert(amount, currency, rates, fx)


def _insert_candidate(
    conn: psycopg.Connection, candidate_id: str, row, deadline, relevance: int, converted: Converted | None
) -> None:
    conn.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                buyer, country, admin_level, summary_en, matched_functions,
                                system_names, procurement_type, estimated_value, value_currency,
                                estimated_value_usd, value_rate, value_rate_date,
                                eligibility_flags, deadline_at)
        values (%s, %s, %s, 'scored', %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            candidate_id,
            row[0],
            relevance,
            region_for(row[3]),
            row[5],
            row[8],
            row[6] or "",
            row[3],
            row[4],
            row[9],
            json.dumps(row[10]) if not isinstance(row[10], str) else row[10],
            list(row[11] or []),
            row[12],
            row[13],
            row[14],
            converted.amount if converted else None,
            converted.rate if converted else None,
            converted.rate_date if converted else None,
            list(row[15] or []),
            deadline,
        ),
    )


def _link(conn: psycopg.Connection, candidate_id: str, notice_id, method: str, score: int) -> None:
    conn.execute(
        """
        insert into candidate_notices (candidate_id, notice_id, match_method, match_score)
        values (%s, %s, %s, %s)
        on conflict (candidate_id, notice_id) do nothing
        """,
        (candidate_id, notice_id, method, score),
    )


def _stage(conn: psycopg.Connection, threshold: int, cap: int) -> tuple[int, int, int]:
    """Stage what clears the threshold, up to the per-source cap for today."""
    rows = conn.execute(
        """
        select c.id, c.score, n.source_id
        from candidates c
        join notices n on n.id = c.primary_notice_id
        where c.status = 'scored'
        order by c.score desc, c.id
        """
    ).fetchall()

    today = datetime.now(UTC).date()
    already = dict(
        conn.execute(
            """
            select n.source_id, count(*)
            from candidates c
            join notices n on n.id = c.primary_notice_id
            where c.status in ('staged', 'pending_review') and c.updated_at::date = %s
            group by n.source_id
            """,
            (today,),
        ).fetchall()
    )

    staged = held = below = 0
    for candidate_id, score, source_id in rows:
        if score < threshold:
            below += 1
            continue
        if already.get(source_id, 0) >= cap:
            held += 1
            continue

        conn.execute(
            "update candidates set status = 'pending_review', updated_at = now() where id = %s",
            (candidate_id,),
        )
        write_event(conn, candidate_id, "staged", "scored", f"pending_review, score {score}")
        already[source_id] = already.get(source_id, 0) + 1
        staged += 1

    return staged, held, below
