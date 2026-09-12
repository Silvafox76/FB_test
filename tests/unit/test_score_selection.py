"""One row per notice out of the scorer's selection, because two meant two paid calls.

`translations` is keyed on (notice_id, prompt_version), not on notice_id. A TED notice
carries both its `ted-eforms`/`source-native` row, written by the fetcher from what TED
itself published, and the step 14 machine translation. Measured on the live database on
2026-09-12: **630 of the 934 notices with any translation have two.**

`SELECT_FILTERED_IN` used to `left join translations` with nothing narrowing it to one
row, so every one of those notices came back twice and was scored twice. **75 of the 146
scored notices carry two `scores` rows** — 75 paid Haiku calls out of 250, about 30% of
the scorer's spend, bought for nothing.

**Why it survived a full pipeline run and a test suite.** A duplicate row is not an error
at any boundary this project defends. Pydantic validates each one; the cap in
`monitor/caps.py` counts each call honestly, so the spend was real and correctly logged;
and both scores are genuine answers to the same question, so neither is wrong. The only
visible symptom was a `scores` table holding more rows than there are scored notices —
and that reads as intended, because `scores` is one row per scoring call by design and a
re-scored notice legitimately has two. Nothing failed. The work was simply done twice.

That is the shape worth keeping from this: the failure was invisible precisely because
every individual part behaved correctly, which is the same shape as the four defects the
first real model run found. Rule 4 catches what raises. It does not catch what quietly
costs money.
"""

from __future__ import annotations

import uuid

import pytest

from monitor.score.run import SELECT_FILTERED_IN

SOURCE = "test-sel"


@pytest.fixture
def notice_with(db_conn):
    """A filtered_in notice, with however many translation rows the test asks for.

    Rows are written with explicit, ordered `created_at` values rather than `now()`:
    the query's whole job is to pick the latest, and two rows written in one
    transaction can share a timestamp to the microsecond.

    Nothing is cleaned up and nothing is committed, deliberately. `db_conn` rolls its
    transaction back when the test ends, which removes every row below. Writing an
    explicit teardown here would need DELETE on `translations`, and `monitor_pipeline`
    does not have it - correctly, because the pipeline has no business deleting a
    translation it paid for. Relying on the rollback keeps this test inside the role
    the pipeline actually runs as, which is the point of testing against it (rule 11).
    """

    def make(*renderings: tuple[str, str, str]) -> uuid.UUID:
        marker = uuid.uuid4().hex[:8]
        content_hash = f"sha256:{marker}"
        db_conn.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, 'Selection fixture', 'GH', 'national', 'en', 'feed', 'api',
                    'FeedConnector', 1, 'pending', false, 1, 50, 3)
            on conflict (id) do nothing
            """,
            (SOURCE,),
        )
        db_conn.execute(
            """
            insert into notices_raw (content_hash, source_id, url, storage_path, mime)
            values (%s, %s, 'https://example.invalid/n', 'raw/x.json', 'application/json')
            """,
            (content_hash, SOURCE),
        )
        notice_id = db_conn.execute(
            """
            insert into notices (content_hash, source_id, url, title, country, admin_level,
                                 language, status)
            values (%s, %s, 'https://example.invalid/n', 'Fourniture d''un systeme', 'GH',
                    'national', 'fr', 'filtered_in')
            returning id
            """,
            (content_hash, SOURCE),
        ).fetchone()[0]

        for offset, (model, prompt_version, title_en) in enumerate(renderings):
            db_conn.execute(
                """
                insert into translations (notice_id, title_en, body_en, model, prompt_version,
                                          latency_ms, cost_usd, created_at)
                values (%s, %s, 'body', %s, %s, 10, 0, now() + make_interval(secs => %s))
                """,
                (notice_id, title_en, model, prompt_version, offset),
            )
        return notice_id

    return make


def selected(conn, notice_id):
    return [row for row in conn.execute(SELECT_FILTERED_IN).fetchall() if row[0] == notice_id]


def test_a_notice_with_two_renderings_is_selected_once(db_conn, notice_with):
    """The defect, in one assertion. Two rows here meant two paid model calls."""
    notice_id = notice_with(
        ("ted-eforms", "source-native", "Supply of a system"),
        ("claude-haiku-4-5", "d2580a36a5d2", "Supply of an integrated financial management system"),
    )

    assert len(selected(db_conn, notice_id)) == 1


def test_the_rendering_chosen_is_the_machine_translation(db_conn, notice_with):
    """Not an arbitrary one of the two: the later row, which is the better English.

    TED's own `source-native` rendering translates the standardised CPV heading and
    leaves the buyer's own words in the original language, on 618 of 738 non-English
    TED notices. Scoring against that is scoring against half a translation.
    """
    notice_id = notice_with(
        ("ted-eforms", "source-native", "Supply of a system"),
        ("claude-haiku-4-5", "d2580a36a5d2", "Supply of an integrated financial management system"),
    )

    assert selected(db_conn, notice_id)[0][18] == "Supply of an integrated financial management system"


def test_the_order_the_rows_were_written_in_does_not_change_the_answer(db_conn, notice_with):
    """`created_at` decides, not insertion order and not whatever the planner returns."""
    notice_id = notice_with(
        ("claude-haiku-4-5", "d2580a36a5d2", "written first, older"),
        ("ted-eforms", "source-native", "written second, newer"),
    )

    rows = selected(db_conn, notice_id)
    assert len(rows) == 1
    assert rows[0][18] == "written second, newer"


def test_a_notice_with_one_rendering_is_unaffected(db_conn, notice_with):
    """304 of the 934 translated notices have exactly one. They must behave as before."""
    notice_id = notice_with(("claude-haiku-4-5", "d2580a36a5d2", "Supply of an IFMIS"))

    rows = selected(db_conn, notice_id)
    assert len(rows) == 1
    assert rows[0][18] == "Supply of an IFMIS"


def test_a_notice_with_no_rendering_is_still_selected(db_conn, notice_with):
    """33 of 146 scored notices had no translations row at all, nine of them French.

    The join is a `left join` for this reason and must stay one: a notice with no
    English rendering is still scored, on its original text, and dropping it here
    would silently narrow the scorer's input to translated notices only.
    """
    notice_id = notice_with()

    rows = selected(db_conn, notice_id)
    assert len(rows) == 1
    assert rows[0][18] == ""
    assert rows[0][19] == ""


def test_three_renderings_still_yield_one_row(db_conn, notice_with):
    """Nothing about the fix depends on there being exactly two."""
    notice_id = notice_with(
        ("ted-eforms", "source-native", "oldest"),
        ("claude-haiku-4-5", "d2580a36a5d2", "middle"),
        ("claude-haiku-4-5", "e3691b47b6e3", "newest"),
    )

    rows = selected(db_conn, notice_id)
    assert len(rows) == 1
    assert rows[0][18] == "newest"
