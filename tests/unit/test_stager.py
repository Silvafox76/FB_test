"""The stager: what reaches the reviewer's queue, and what waits.

Runs against the database, because the cap is counted over rows and the transitions
are what a later audit reads.

`stager.run()` commits per candidate on purpose: a run interrupted halfway should
keep what it already decided rather than losing it. That means the rolled-back
`db_conn` fixture is not enough isolation here, so these tests clean up after
themselves on an owner connection, the same way the checkpoint tests do, and every
assertion counts only rows belonging to the test's own source.

The rule worth the test is the cap. A portal publishing two hundred notices in a
morning must not put two hundred candidates in front of one person. The rest keep
their score at `scored` and can be staged tomorrow: held, not dropped.
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from monitor.stage.stager import per_source_daily_cap, region_for, run, stage_threshold


@pytest.fixture
def owner():
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        pytest.fail("DATABASE_URL_OWNER is not set; run 'make up' before 'make test'")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("set lock_timeout = '5s'")
        yield conn


@pytest.fixture
def seeded(db_conn, owner):
    """A source, a helper that makes scored notices, and a cleanup that removes them."""
    marker = uuid.uuid4().hex[:8]
    source_id = f"test-{marker}"
    db_conn.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min,
                             expected_max, max_consecutive_failures)
        values (%s, 'stager test source', 'GH', 'national', 'en', 'feed', 'api',
                'FeedConnector', 1, 'cleared', false, 1, 50, 3)
        """,
        (source_id,),
    )

    def scored_notice(relevance: int, title: str, *, country: str = "GH", system_names=None, deadline=None):
        content_hash = f"sha256:{uuid.uuid4().hex}"
        db_conn.execute(
            """
            insert into notices_raw (content_hash, source_id, url, storage_path, mime)
            values (%s, %s, 'https://example.invalid/n', 'raw/x.json', 'application/json')
            """,
            (content_hash, source_id),
        )
        notice_id = db_conn.execute(
            """
            insert into notices (content_hash, source_id, url, title, country, admin_level,
                                 language, status, deadline_at)
            values (%s, %s, 'https://example.invalid/n', %s, %s, 'national', 'en', 'scored', %s)
            returning id
            """,
            (content_hash, source_id, title, country, deadline),
        ).fetchone()[0]
        db_conn.execute(
            """
            insert into scores (notice_id, model, prompt_version, relevance, title_en,
                                matched_functions, system_names, procurement_type, summary_en,
                                confidence, raw_json, tokens_in, tokens_out, latency_ms, cost_usd,
                                deadline_at)
            values (%s, 'claude-haiku-4-5', 'testver', %s, %s, '[]'::jsonb, %s, 'system',
                    'A summary.', 'high', '{}'::jsonb, 100, 50, 10, 0.001, %s)
            """,
            (notice_id, relevance, title, list(system_names or []), deadline),
        )
        return notice_id

    yield source_id, scored_notice

    # stager.run() commits, so this is the only thing that undoes it.
    db_conn.rollback()
    owner.execute(
        """
        delete from events where entity_type = 'candidate' and entity_id in (
            select c.id from candidates c join notices n on n.id = c.primary_notice_id
            where n.source_id = %s)
        """,
        (source_id,),
    )
    owner.execute(
        """
        delete from candidate_notices where notice_id in (
            select id from notices where source_id = %s)
        """,
        (source_id,),
    )
    owner.execute(
        """
        delete from candidates where primary_notice_id in (
            select id from notices where source_id = %s)
        """,
        (source_id,),
    )
    owner.execute("delete from scores where notice_id in (select id from notices where source_id = %s)", (source_id,))
    owner.execute("delete from notices where source_id = %s", (source_id,))
    owner.execute("delete from notices_raw where source_id = %s", (source_id,))
    owner.execute("delete from sources where id = %s", (source_id,))


def test_a_candidate_over_the_threshold_is_staged(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(stage_threshold() + 10, "An IFMIS replacement")

    counts = run(db_conn)

    assert counts.candidates_created == 1
    assert _statuses(db_conn, seeded[0]) == ["pending_review"]


def test_a_candidate_below_the_threshold_stays_scored(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(stage_threshold() - 1, "Office furniture")

    run(db_conn)

    assert _statuses(db_conn, seeded[0]) == ["scored"]


def test_the_sixteenth_candidate_from_one_source_on_one_day_stays_scored(db_conn, seeded):
    """The acceptance test. per_source_daily_cap is 15."""
    cap = per_source_daily_cap()
    _, scored_notice = seeded
    for index in range(cap + 1):
        scored_notice(stage_threshold() + 10, f"Distinct financial management tender number {index}")

    counts = run(db_conn)
    statuses = _statuses(db_conn, seeded[0])

    assert counts.candidates_created == cap + 1
    assert statuses.count("pending_review") == cap
    assert statuses.count("scored") == 1, "the sixteenth candidate must wait, not vanish"


def test_the_cap_holds_rather_than_drops(db_conn, seeded):
    """A held candidate keeps its score and can be staged tomorrow."""
    cap = per_source_daily_cap()
    _, scored_notice = seeded
    for index in range(cap + 1):
        scored_notice(90, f"Distinct financial management tender number {index}")

    run(db_conn)

    assert _scores(db_conn, seeded[0], "scored") == [90]


def test_the_highest_scoring_candidates_are_staged_first(db_conn, seeded):
    """If the cap bites, it should bite the weakest candidates."""
    cap = per_source_daily_cap()
    _, scored_notice = seeded
    scored_notice(61, "The weakest distinct tender of them all")
    for index in range(cap):
        scored_notice(95, f"A strong distinct financial management tender number {index}")

    run(db_conn)

    assert _scores(db_conn, seeded[0], "scored") == [61]


def test_a_second_notice_about_the_same_tender_joins_rather_than_making_a_candidate(db_conn, seeded):
    _, scored_notice = seeded
    deadline = "2026-11-30T00:00:00+00:00"
    scored_notice(80, "Supply of an integrated financial management information system", deadline=deadline)
    run(db_conn)

    scored_notice(
        85,
        "Supply of an integrated financial management information system for the Treasury",
        deadline=deadline,
    )
    counts = run(db_conn)

    assert counts.candidates_created == 0
    assert counts.joined == 1


def test_a_joined_candidate_takes_the_higher_score(db_conn, seeded):
    """One source describing a tender badly must not pull down another describing it well."""
    _, scored_notice = seeded
    deadline = "2026-11-30T00:00:00+00:00"
    scored_notice(70, "Supply of an integrated financial management information system", deadline=deadline)
    run(db_conn)
    scored_notice(
        92,
        "Supply of an integrated financial management information system for the Treasury",
        deadline=deadline,
    )
    run(db_conn)

    assert _scores(db_conn, seeded[0]) == [92]


def test_every_transition_writes_an_event(db_conn, seeded):
    """A candidate that appeared, joined or was staged is explicable afterwards."""
    _, scored_notice = seeded
    scored_notice(stage_threshold() + 10, "An IFMIS replacement")

    run(db_conn)

    actions = [
        row[0]
        for row in db_conn.execute("select action from events where entity_type = 'candidate' order by id").fetchall()
    ]
    assert "scored" in actions
    assert "staged" in actions


def test_a_joined_notice_writes_a_duplicate_joined_event(db_conn, seeded):
    _, scored_notice = seeded
    deadline = "2026-11-30T00:00:00+00:00"
    scored_notice(80, "Supply of an integrated financial management information system", deadline=deadline)
    run(db_conn)
    scored_notice(
        82,
        "Supply of an integrated financial management information system for the Treasury",
        deadline=deadline,
    )
    run(db_conn)

    actions = [
        row[0]
        for row in db_conn.execute("select action from events where entity_type = 'candidate' order by id").fetchall()
    ]
    assert "duplicate_joined" in actions


def test_a_notice_already_in_a_candidate_is_not_clustered_twice(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(80, "An IFMIS replacement")
    run(db_conn)

    counts = run(db_conn)

    assert counts.scored_notices == 0


def test_candidate_ids_are_the_readable_form(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(80, "An IFMIS replacement")

    run(db_conn)

    candidate_id = _ids(db_conn, seeded[0])[0]
    assert len(candidate_id) == 7
    assert candidate_id.startswith("C")
    assert candidate_id[1:].isdigit()


def _rows(conn, source_id: str, column: str, status: str = "") -> list:
    """One column of this source's candidates, newest first. Scoped, so tests do not see each other."""
    clause = " and c.status = %s" if status else ""
    parameters = (source_id, status) if status else (source_id,)
    return [
        row[0]
        for row in conn.execute(
            f"""
            select c.{column} from candidates c
            join notices n on n.id = c.primary_notice_id
            where n.source_id = %s{clause}
            order by c.id desc
            """,
            parameters,
        ).fetchall()
    ]


def _statuses(conn, source_id: str) -> list[str]:
    return _rows(conn, source_id, "status")


def _scores(conn, source_id: str, status: str = "") -> list[int]:
    return _rows(conn, source_id, "score", status)


def _ids(conn, source_id: str) -> list[str]:
    return _rows(conn, source_id, "id")


def _actions(conn, source_id: str) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            """
            select e.action from events e
            join candidates c on c.id = e.entity_id
            join notices n on n.id = c.primary_notice_id
            where e.entity_type = 'candidate' and n.source_id = %s
            order by e.id
            """,
            (source_id,),
        ).fetchall()
    ]


# --- the region lookup -------------------------------------------------------


@pytest.mark.parametrize(
    ("country", "region"),
    [
        ("GH", "West Africa"),
        ("SN", "West Africa"),
        ("UA", "Balkans and Ukraine"),
        ("XK", "Balkans and Ukraine"),
        ("DE", "Europe"),
        ("AR", "Europe"),
    ],
)
def test_region_comes_from_config(country, region):
    assert region_for(country) == region
