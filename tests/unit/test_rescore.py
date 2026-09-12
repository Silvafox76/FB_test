"""Step 20's escalation: the 40-to-70 band gets a second opinion on Sonnet 5.

Same approach as `test_score_client.py` for the call — a real `anthropic.Anthropic`
over a mocked transport, so what is asserted is what actually goes over the wire —
and the same approach as `test_stager.py` for the database, because `rescore()`
commits per notice and the rolled-back `db_conn` fixture is not enough isolation on
its own.

Two things these tests are really about. The band decides whether money is spent at
all, which is the acceptance test the step names: 55 gets a call, 90 does not. And
`confidence` decides which of the two answers the stager will read afterwards,
which is the part a later reader is most likely to get backwards, because the
labels are text and the ordering lives in one dictionary in `score/run.py`.

The live test at the bottom makes a real Sonnet call and is skipped unless
`MONITOR_LIVE_RESCORE=1`. It costs about two cents; `make test` does not run it.
"""

from __future__ import annotations

import json
import os
import uuid

import anthropic
import httpx2
import psycopg
import pytest

from monitor import caps
from monitor.score.prompt import prompt_version
from monitor.score.run import (
    RESCORE_MODEL,
    RESCORE_PURPOSE,
    rescore,
    rescore_band,
    sonnet_wins,
)
from monitor.score.schema import TOOL_NAME

BAND_LOW, BAND_HIGH = rescore_band()

# What Sonnet returns in the mocked tests. Real in shape: this is the answer the
# first live Sonnet call gave on TED notice 619646-2026 on 2026-09-12, trimmed.
SONNET_ANSWER = {
    "relevance": 72,
    "title_en": "Acquisition and implementation of an asset management solution for the Nouvelle-Aquitaine region",
    "matched_functions": [{"function_id": "asset_inventory_management", "evidence": "gestion du patrimoine"}],
    "system_names": [],
    "procurement_type": "system",
    "eligibility_flags": [],
    "deadline_at": "2026-10-15",
    "summary_en": "A French region is buying an asset management system.",
    "confidence": "high",
}


@pytest.fixture(autouse=True)
def pinned_route(monkeypatch):
    """Pinned for the same reason as the scorer's tests: since step 12 the model
    name on the wire depends on MODEL_ROUTE, and a developer with bedrock in their
    .env would otherwise see an inference profile id here."""
    monkeypatch.setenv("MODEL_ROUTE", "direct")
    monkeypatch.delenv("BEDROCK_INFERENCE_GEO", raising=False)


@pytest.fixture
def owner():
    url = os.environ.get("DATABASE_URL_OWNER")
    if not url:
        pytest.fail("DATABASE_URL_OWNER is not set; run 'make up' before 'make test'")
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("set lock_timeout = '5s'")
        yield conn


@pytest.fixture
def seeded(db_conn, owner, request):
    """A source, a helper that makes scored-and-unclustered notices, and cleanup.

    `rescore()` commits, so nothing here is undone by the fixture's rollback. That
    includes the `model_calls` rows: a mocked call still computes a cost from its
    fabricated token counts and still counts against rule 22's daily cap, so the
    teardown removes the rows this test created rather than leaving invented spend
    in the ledger that a real run would later be refused for. Only rows newer than
    the snapshot and only of this purpose are touched.

    The live test at the bottom is the exception. Its call is a real charge on the
    real account, so its row stays: the ledger is what rule 22's cap is counted
    from, and a day's spend that quietly omits a call is worse than an untidy table.
    """
    marker = uuid.uuid4().hex[:8]
    source_id = f"test-{marker}"
    first_call_id = owner.execute("select coalesce(max(id), 0) from model_calls").fetchone()[0]

    db_conn.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min,
                             expected_max, max_consecutive_failures)
        values (%s, 'rescore test source', 'FR', 'national', 'fr', 'feed', 'api',
                'FeedConnector', 1, 'cleared', false, 1, 50, 3)
        """,
        (source_id,),
    )

    def scored_notice(
        relevance: int,
        *,
        confidence: str = "low",
        version: str | None = None,
        translations: tuple[tuple[str, str], ...] = (),
        title: str = "Acquisition d'une solution de gestion du patrimoine",
    ):
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
                                 language, language_confidence, status, deadline_at, body)
            values (%s, %s, 'https://example.invalid/n', %s, 'FR', 'national', 'fr', 1.0, 'scored',
                    '2026-10-15', 'Le conseil régional lance un appel d''offres.')
            returning id
            """,
            (content_hash, source_id, title),
        ).fetchone()[0]
        for order, (model, title_en) in enumerate(translations):
            # `created_at` is set explicitly because the rows are being written in
            # one transaction here and would otherwise share the transaction's
            # `now()`. In the pipeline they do not: the source's own rendering is
            # written by the normaliser and the machine translation by step 14,
            # minutes to hours apart, and the deduper's "latest rendering" rule is
            # only meaningful against that.
            db_conn.execute(
                """
                insert into translations (notice_id, title_en, body_en, model, prompt_version,
                                          latency_ms, cost_usd, created_at)
                values (%s, %s, 'The regional council is tendering.', %s, %s, 10, 0.0001,
                        now() - make_interval(hours => %s))
                """,
                (notice_id, title_en, model, f"pv-{model}", len(translations) - order),
            )
        score_id = db_conn.execute(
            """
            insert into scores (notice_id, model, prompt_version, relevance, title_en,
                                matched_functions, system_names, procurement_type, summary_en,
                                confidence, raw_json, tokens_in, tokens_out, latency_ms, cost_usd,
                                deadline_at)
            values (%s, 'claude-haiku-4-5', %s, %s, 'Acquisition of an asset management solution',
                    '[]'::jsonb, '{}', 'system', 'A French region is buying something.', %s,
                    '{}'::jsonb, 100, 50, 10, 0.001, '2026-10-15')
            returning id
            """,
            (notice_id, version or prompt_version(), relevance, confidence),
        ).fetchone()[0]
        return notice_id, score_id

    yield source_id, scored_notice

    db_conn.rollback()
    owner.execute(
        """
        delete from events where entity_type = 'notice' and entity_id in (
            select id::text from notices where source_id = %s)
        """,
        (source_id,),
    )
    owner.execute(
        "delete from candidate_notices where notice_id in (select id from notices where source_id = %s)",
        (source_id,),
    )
    owner.execute("delete from scores where notice_id in (select id from notices where source_id = %s)", (source_id,))
    owner.execute(
        "delete from translations where notice_id in (select id from notices where source_id = %s)", (source_id,)
    )
    owner.execute("delete from notices where source_id = %s", (source_id,))
    owner.execute("delete from notices_raw where source_id = %s", (source_id,))
    owner.execute("delete from sources where id = %s", (source_id,))
    if "real_sonnet_call" not in request.node.name:
        owner.execute("delete from model_calls where id > %s and purpose = %s", (first_call_id, RESCORE_PURPOSE))


def tool_response(payload: dict | None, *, tokens_in: int = 586, tokens_out: int = 518) -> dict:
    """The shape a real Sonnet 5 call returned on 2026-09-12: a forced tool call and nothing else."""
    content = [{"type": "tool_use", "id": "toolu_1", "name": TOOL_NAME, "input": payload}] if payload else []
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": RESCORE_MODEL,
        "content": content,
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out, "cache_creation_input_tokens": 6193},
    }


def client_returning(*documents) -> tuple[anthropic.Anthropic, list[dict]]:
    sent: list[dict] = []
    remaining = list(documents)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=remaining.pop(0))

    client = anthropic.Anthropic(
        api_key="test-key-not-real", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    return client, sent


def events_for(conn, notice_id) -> list[tuple[str, str]]:
    return conn.execute(
        "select before, after from events where entity_type = 'notice' and entity_id = %s and action = 'rescored'",
        (str(notice_id),),
    ).fetchall()


def score_row(conn, score_id) -> tuple:
    return conn.execute(
        "select model, relevance, confidence, title_en from scores where id = %s", (score_id,)
    ).fetchone()


# --- the band: the acceptance test the step names ----------------------------


def test_a_55_scoring_notice_gets_a_sonnet_call(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(55)
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    counts = rescore(db_conn, client)

    assert counts.in_band == 1
    assert counts.escalated == 1
    assert len(sent) == 1
    assert sent[0]["model"] == RESCORE_MODEL


def test_a_90_scoring_notice_does_not(db_conn, seeded):
    """Nothing is sent, so nothing is spent, and the score stands as Haiku wrote it."""
    _, scored_notice = seeded
    _, score_id = scored_notice(90, confidence="low")
    client, sent = client_returning()

    counts = rescore(db_conn, client)

    assert counts.in_band == 0
    assert sent == []
    assert score_row(db_conn, score_id)[0] == "claude-haiku-4-5"


@pytest.mark.parametrize(
    ("relevance", "in_band"),
    [
        (BAND_LOW - 1, False),
        (BAND_LOW, True),
        (55, True),
        (BAND_HIGH, True),
        (BAND_HIGH + 1, False),
    ],
)
def test_the_band_edges_are_inclusive(db_conn, seeded, relevance, in_band):
    """The band is read from config/thresholds.yaml, so these cases move with it (rule 6)."""
    _, scored_notice = seeded
    scored_notice(relevance)
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    counts = rescore(db_conn, client)

    assert counts.in_band == (1 if in_band else 0)
    assert len(sent) == (1 if in_band else 0)


# --- which answer stages -----------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "escalated", "expected"),
    [
        ("low", "high", True),
        ("low", "medium", True),
        ("medium", "high", True),
        ("high", "low", False),
        ("high", "medium", False),
        ("medium", "low", False),
        ("low", "low", True),
        ("medium", "medium", True),
        ("high", "high", True),
    ],
)
def test_sonnet_wins_on_confidence_and_on_a_tie(stored, escalated, expected):
    assert sonnet_wins(stored, escalated) is expected


def test_a_label_outside_appendix_c_raises(db_conn):
    """Rule 4. Migration 003's check constraint allows three; a fourth is a schema bug."""
    with pytest.raises(KeyError):
        sonnet_wins("high", "certain")


def test_the_higher_confidence_answer_replaces_the_score_the_stager_reads(db_conn, seeded):
    _, scored_notice = seeded
    notice_id, score_id = scored_notice(55, confidence="low")
    client, _ = client_returning(tool_response(SONNET_ANSWER))

    counts = rescore(db_conn, client)

    assert (counts.replaced, counts.held) == (1, 0)
    model, relevance, confidence, title_en = score_row(db_conn, score_id)
    assert (model, relevance, confidence) == (RESCORE_MODEL, 72, "high")
    assert title_en == SONNET_ANSWER["title_en"]
    assert events_for(db_conn, notice_id) == [
        ("claude-haiku-4-5 relevance 55 confidence low", f"{RESCORE_MODEL} relevance 72 confidence high")
    ]


def test_a_less_confident_escalation_leaves_the_haiku_score_standing(db_conn, seeded):
    """The call still happened and is still logged. What it bought was a confirmation."""
    _, scored_notice = seeded
    notice_id, score_id = scored_notice(55, confidence="high")
    client, sent = client_returning(tool_response(dict(SONNET_ANSWER, confidence="low", relevance=12)))

    counts = rescore(db_conn, client)

    assert (counts.replaced, counts.held) == (0, 1)
    assert len(sent) == 1
    assert score_row(db_conn, score_id)[:3] == ("claude-haiku-4-5", 55, "high")
    assert "held" in events_for(db_conn, notice_id)[0][1]


def test_a_tie_on_confidence_goes_to_sonnet(db_conn, seeded):
    _, scored_notice = seeded
    _, score_id = scored_notice(55, confidence="high")
    client, _ = client_returning(tool_response(dict(SONNET_ANSWER, confidence="high", relevance=41)))

    counts = rescore(db_conn, client)

    assert counts.replaced == 1
    assert score_row(db_conn, score_id)[:3] == (RESCORE_MODEL, 41, "high")


# --- one call, and only one --------------------------------------------------


def test_an_answer_that_does_not_validate_ends_the_escalation_without_a_retry(db_conn, seeded):
    """Rule 2's retry rescues a notice with no score. This one has a score already."""
    _, scored_notice = seeded
    notice_id, score_id = scored_notice(55, confidence="medium")
    client, sent = client_returning(tool_response(dict(SONNET_ANSWER, relevance=101)))

    counts = rescore(db_conn, client)

    assert (counts.abandoned, counts.replaced, counts.held) == (1, 0, 0)
    assert len(sent) == 1, "one call, never a second"
    assert score_row(db_conn, score_id)[:3] == ("claude-haiku-4-5", 55, "medium")
    assert "did not validate" in events_for(db_conn, notice_id)[0][1]


def test_answering_in_prose_is_an_answer_that_does_not_validate(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(55)
    client, sent = client_returning(tool_response(None))

    counts = rescore(db_conn, client)

    assert counts.abandoned == 1
    assert len(sent) == 1


def test_a_notice_is_never_escalated_twice(db_conn, seeded):
    """The `rescored` event is the marker. Without it the band refills every run."""
    _, scored_notice = seeded
    scored_notice(55)
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    first = rescore(db_conn, client)
    second = rescore(db_conn, client)

    assert (first.escalated, second.escalated) == (1, 0)
    assert len(sent) == 1


def test_an_abandoned_escalation_is_not_retried_on_the_next_run(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(55)
    client, sent = client_returning(tool_response(dict(SONNET_ANSWER, relevance=101)))

    rescore(db_conn, client)
    second = rescore(db_conn, client)

    assert second.in_band == 0
    assert len(sent) == 1


def test_a_notice_already_in_a_candidate_is_not_escalated(db_conn, seeded, owner):
    """A candidate takes its score at clustering, so escalating afterwards changes nothing."""
    source_id, scored_notice = seeded
    notice_id, _ = scored_notice(55)
    candidate_id = f"C{uuid.uuid4().int % 900000 + 99:06d}"
    db_conn.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                country, admin_level, summary_en, matched_functions, procurement_type)
        values (%s, %s, 55, 'scored', 'Europe', 'fr', 'A title', 'FR', 'national', 'A summary.',
                '[]'::jsonb, 'system')
        """,
        (candidate_id, notice_id),
    )
    db_conn.execute(
        "insert into candidate_notices (candidate_id, notice_id, match_method, match_score)"
        " values (%s, %s, 'content_hash', 100)",
        (candidate_id, notice_id),
    )
    client, sent = client_returning()

    counts = rescore(db_conn, client)

    assert (counts.in_band, sent) == (0, [])
    db_conn.rollback()
    owner.execute("delete from candidate_notices where candidate_id = %s", (candidate_id,))
    owner.execute("delete from candidates where id = %s", (candidate_id,))


def test_a_score_from_another_prompt_version_is_not_escalated(db_conn, seeded):
    """Step 20 says "the same prompt". A score made under a different one is not
    comparable to a Sonnet answer under this one; it needs re-scoring, not escalating."""
    _, scored_notice = seeded
    scored_notice(55, version="an-older-prompt")
    client, sent = client_returning()

    counts = rescore(db_conn, client)

    assert (counts.in_band, sent) == (0, [])


def test_a_notice_with_two_translation_rows_is_escalated_once(db_conn, seeded):
    """TED notices carry both their own `ted-eforms` rendering and the step 14 one.

    630 of the 934 notices with a translation on 2026-09-12 have two rows, and a
    plain join over them hands the caller the same notice twice. For a paid call
    that is money, not an untidy result set.
    """
    _, scored_notice = seeded
    scored_notice(
        55,
        translations=(
            ("ted-eforms", "France – Software package and information systems – Acquisition d'une solution"),
            ("claude-haiku-4-5", "Acquisition of an asset management solution"),
        ),
    )
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    counts = rescore(db_conn, client)

    assert counts.escalated == 1
    assert len(sent) == 1
    # The later row is the machine translation, and that is the rendering sent.
    assert "Acquisition of an asset management solution" in sent[0]["messages"][0]["content"]


# --- rule 22: capped and logged ----------------------------------------------


def test_the_call_is_logged_against_its_own_purpose(db_conn, seeded):
    _, scored_notice = seeded
    scored_notice(55)
    before = caps.spend_today(db_conn)
    client, _ = client_returning(tool_response(SONNET_ANSWER))

    rescore(db_conn, client)

    after = caps.spend_today(db_conn)
    assert after.calls == before.calls + 1
    assert after.usd > before.usd
    purpose, model = db_conn.execute("select purpose, model from model_calls order by id desc limit 1").fetchone()
    assert (purpose, model) == (RESCORE_PURPOSE, RESCORE_MODEL)


def test_the_cap_is_checked_before_the_request(db_conn, seeded, monkeypatch):
    _, scored_notice = seeded
    scored_notice(55)
    monkeypatch.setenv("DAILY_CALL_CAP", "1")
    caps._thresholds.cache_clear()
    db_conn.execute(
        "insert into model_calls (purpose, model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms)"
        " values (%s, %s, 'v', 10, 10, 0.001, 5)",
        (RESCORE_PURPOSE, RESCORE_MODEL),
    )
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    with pytest.raises(caps.CapExceeded):
        rescore(db_conn, client)

    assert sent == []


def test_the_request_carries_the_scorers_own_prompt_and_forced_tool(db_conn, seeded):
    """ "The same prompt" is the whole basis of the comparison: only the model differs."""
    from monitor.score.prompt import system_prompt

    _, scored_notice = seeded
    scored_notice(55)
    client, sent = client_returning(tool_response(SONNET_ANSWER))

    rescore(db_conn, client)

    body = sent[0]
    assert body["system"][0]["text"] == system_prompt()
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert [tool["name"] for tool in body["tools"]] == [TOOL_NAME]
    assert len(body["messages"]) == 1, "one call: no retry turn appended"


# --- the real thing ----------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("MONITOR_LIVE_RESCORE") != "1",
    reason="makes a real Sonnet 5 call; set MONITOR_LIVE_RESCORE=1 to run it",
)
def test_a_real_sonnet_call_escalates_a_real_notice(db_conn, seeded):
    """End to end against the live API, on real French notice text.

    The title and body are TED 619646-2026 as fetched on 2026-09-12. The assertions
    are deliberately about the path rather than the answer — a model is entitled to
    a different opinion tomorrow, and a test that pins Sonnet's relevance to 45
    would fail for the wrong reason.
    """
    from monitor.cli import model_client

    _, scored_notice = seeded
    notice_id, score_id = scored_notice(
        55,
        confidence="low",
        title=(
            "France – Logiciels et systèmes d'information – ACQUISITION ET MISE EN PLACE D'UNE "
            "SOLUTION DE GESTION DU PATRIMOINE DE LA REGION NOUVELLE AQUITAINE"
        ),
    )

    counts = rescore(db_conn, model_client())

    assert counts.escalated == 1
    assert counts.abandoned == 0, "the live answer validated against appendix C"
    assert counts.replaced + counts.held == 1
    assert counts.cost_usd > 0
    assert len(events_for(db_conn, notice_id)) == 1

    model, relevance, confidence, _ = score_row(db_conn, score_id)
    if counts.replaced:
        assert model == RESCORE_MODEL
    else:
        assert (model, relevance, confidence) == ("claude-haiku-4-5", 55, "low")
