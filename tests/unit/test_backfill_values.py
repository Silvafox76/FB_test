"""The value backfill, against the real schema.

`db_conn` is a `monitor_pipeline` connection whose work rolls back, which is why the
Liberia source row itself is not created here: it is seeded by the registry at
`make up` and `monitor_pipeline` has no more privilege on `sources` than the script
under test needs, so re-seeding it per test would be testing a fixture setup rather
than the backfill.

One real payload, shaped exactly like `monitor/connectors/liberia.py` writes it
(`{"listing": ..., "detail": {"releases": [...]}}`), written under `tmp_path` so the
script's `storage_root` argument is exercised rather than the real `storage/`
directory on disk.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

from monitor.fx.config import load as load_fx
from monitor.fx.nbu import Rate
from monitor.fx.store import store as store_rates
from scripts.backfill_values import backfill_candidates, backfill_notices, payload_path

SOURCE_ID = "liberia"

# A candidate id inside the block monitor/stage/stager.py reserves for fixtures
# (FIXTURE_ID_FLOOR = 900_000), so this can never collide with a real one.
CANDIDATE_ID = "C900901"

RATE_DAY = date.today()


def _store_a_rate_day(conn, day=None):
    """Same helper as tests/unit/test_stager.py: a day of rates under the real
    publisher id, so `monitor.fx.store.latest` (which the script calls exactly as
    the stager does) has something to read.
    """
    config = load_fx()
    store_rates(
        conn,
        [
            Rate(currency="USD", rate_date=day or RATE_DAY, uah_per_unit=Decimal("44.5483")),
            Rate(currency="EUR", rate_date=day or RATE_DAY, uah_per_unit=Decimal("51.6386")),
        ],
        config,
    )


def _liberia_payload(*, amount: int, currency: str) -> dict:
    """Shaped like `monitor/connectors/liberia.py`'s own payload: a search row
    paired with an OCDS release package. The value lives at
    `planning.budget.amount`, which is what `monitor/normalise/liberia.py` reads
    (not `tender.value`, which the module's own docstring says is sometimes a
    zeroed copy).
    """
    return {
        "listing": {"id": "test-listing", "url": "https://example.invalid/liberia/test"},
        "detail": {
            "releases": [
                {
                    "ocid": "ocds-test-000001",
                    "date": "2026-09-01T00:00:00Z",
                    "buyer": {"name": "Test Ministry of Finance"},
                    "planning": {"budget": {"amount": {"amount": amount, "currency": currency}}},
                    "tender": {
                        "title": "Backfill test: treasury system",
                        "description": "A description that differs from the title.",
                        "value": {"amount": 0, "currency": currency},
                        "tenderPeriod": {"endDate": "2026-10-01T00:00:00Z"},
                    },
                }
            ]
        },
    }


def _insert_notice(conn, *, content_hash: str, title: str = "Existing title, pre-backfill"):
    """A notices_raw + notices row exactly as they are before this script runs:
    the row exists, `estimated_value` and `value_currency` are both null.
    """
    conn.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, 'https://example.invalid/n', 'unused', 'application/json')
        """,
        (content_hash, SOURCE_ID),
    )
    return conn.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level, language, status)
        values (%s, %s, 'https://example.invalid/n', %s, 'LR', 'national', 'en', 'scored')
        returning id
        """,
        (content_hash, SOURCE_ID, title),
    ).fetchone()[0]


def _insert_candidate(conn, *, candidate_id: str, primary_notice_id):
    conn.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                country, admin_level, summary_en, matched_functions, procurement_type)
        values (%s, %s, 80, 'pending_review', 'West Africa', 'en', 'Backfill test candidate',
                'LR', 'national', 'A summary.', '[]'::jsonb, 'system')
        """,
        (candidate_id, primary_notice_id),
    )


def test_payload_path_matches_the_real_storage_layout(tmp_path):
    assert payload_path(tmp_path, "liberia", "abc123") == tmp_path / "liberia" / "abc123.json"


def test_pass_one_fills_a_notice_from_its_stored_payload(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)

    directory = tmp_path / SOURCE_ID
    directory.mkdir(parents=True)
    (directory / f"{content_hash}.json").write_text(
        json.dumps(_liberia_payload(amount=4428444, currency="UAH")), encoding="utf-8"
    )

    counts, notice_values = backfill_notices(db_conn, tmp_path)

    # `storage_root` is `tmp_path`, so our injected payload is the only file that
    # can possibly be found - every other real notice in the table is legitimately
    # `skipped_no_payload` here, which is why this only asserts our own row's
    # outcome and the two counts that no unrelated row could ever affect.
    assert counts.updated == 1
    assert counts.skipped_mapper_error == 0
    assert notice_values[notice_id] == (Decimal("4428444.00"), "UAH", "")

    row = db_conn.execute("select estimated_value, value_currency from notices where id = %s", (notice_id,)).fetchone()
    assert row == (Decimal("4428444.00"), "UAH")


def test_pass_one_running_twice_changes_nothing(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    _insert_notice(db_conn, content_hash=content_hash)

    directory = tmp_path / SOURCE_ID
    directory.mkdir(parents=True)
    (directory / f"{content_hash}.json").write_text(
        json.dumps(_liberia_payload(amount=10625, currency="USD")), encoding="utf-8"
    )

    first, _ = backfill_notices(db_conn, tmp_path)
    second, _ = backfill_notices(db_conn, tmp_path)

    # `examined` is not compared between the two calls: this scans the whole real
    # table, and this database is shared with whatever else is running against it
    # (this repo's own scheduled fetches, other sessions) - a row count that moves
    # between two calls a few milliseconds apart is that, not a bug in idempotency.
    # `updated` is what idempotency actually means here, and it is exact: nothing
    # but our own payload lives under tmp_path, so no other row can ever update.
    assert first.updated == 1
    assert second.updated == 0


def test_pass_one_logs_and_skips_a_missing_payload(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)
    # No payload file is written under tmp_path for this content_hash.

    counts, notice_values = backfill_notices(db_conn, tmp_path)

    # >= 1 rather than == 1: every other real notice in the table is also missing
    # its payload under this test's empty tmp_path storage root, so this count
    # includes them too. `updated` is exact - nothing under tmp_path can update.
    assert counts.skipped_no_payload >= 1
    assert counts.updated == 0
    # The row is left exactly as it was, not guessed at.
    assert notice_values[notice_id] == (None, None, "")
    row = db_conn.execute("select estimated_value, value_currency from notices where id = %s", (notice_id,)).fetchone()
    assert row == (None, None)


def test_pass_one_logs_and_skips_a_raising_mapper(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)

    directory = tmp_path / SOURCE_ID
    directory.mkdir(parents=True)
    # A Liberia payload with no releases at all: monitor/normalise/liberia.py's own
    # _release() raises ValueError on this shape.
    (directory / f"{content_hash}.json").write_text(
        json.dumps({"listing": {"id": "x"}, "detail": {"releases": []}}), encoding="utf-8"
    )

    counts, notice_values = backfill_notices(db_conn, tmp_path)

    assert counts.skipped_mapper_error == 1
    assert counts.updated == 0
    assert notice_values[notice_id] == (None, None, "")


def test_pass_one_skips_a_source_with_no_connector(db_conn, tmp_path: Path):
    marker = uuid.uuid4().hex[:8]
    source_id = f"test-no-connector-{marker}"
    db_conn.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min,
                             expected_max, max_consecutive_failures)
        values (%s, 'no connector test source', 'GH', 'national', 'en', 'feed', 'api',
                'FeedConnector', 1, 'cleared', false, 1, 50, 3)
        """,
        (source_id,),
    )
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    db_conn.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, 'https://example.invalid/n', 'unused', 'application/json')
        """,
        (content_hash, source_id),
    )
    notice_id = db_conn.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level, language, status)
        values (%s, %s, 'https://example.invalid/n', 'x', 'GH', 'national', 'en', 'scored')
        returning id
        """,
        (content_hash, source_id),
    ).fetchone()[0]

    counts, notice_values = backfill_notices(db_conn, tmp_path)

    assert counts.skipped_no_connector >= 1
    assert notice_values[notice_id] == (None, None, "")


def test_pass_two_converts_a_candidate_at_the_stored_rate(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)

    directory = tmp_path / SOURCE_ID
    directory.mkdir(parents=True)
    (directory / f"{content_hash}.json").write_text(
        json.dumps(_liberia_payload(amount=4428444, currency="UAH")), encoding="utf-8"
    )

    _insert_candidate(db_conn, candidate_id=CANDIDATE_ID, primary_notice_id=notice_id)
    _store_a_rate_day(db_conn)

    _, notice_values = backfill_notices(db_conn, tmp_path)
    counts = backfill_candidates(db_conn, notice_values)

    # Not asserted as exact equality: this scans every real candidate in the
    # table too, and a real run of this same script against the live database
    # (which CLAUDE.md's own instructions call for) leaves some of them already
    # converted. `>= 1` is what this test actually owns - that OUR candidate is
    # among the converted - and the row check below is what proves it precisely.
    assert counts.converted >= 1
    assert counts.updated >= 1

    row = db_conn.execute(
        """
        select estimated_value, value_currency, estimated_value_usd, value_rate, value_rate_date
        from candidates where id = %s
        """,
        (CANDIDATE_ID,),
    ).fetchone()
    assert row[0] == Decimal("4428444.00")
    assert row[1] == "UAH"
    assert row[2] == 99408
    assert row[3] == Decimal("44.548300")
    # Whatever day `latest` actually picked, not one this test assumed.
    from monitor.fx.store import latest

    assert row[4] == latest(db_conn, load_fx()).rate_date
    assert int((row[0] / row[3]).quantize(Decimal("1"), rounding="ROUND_HALF_UP")) == row[2]


def test_pass_two_leaves_a_candidate_with_no_stated_value_alone(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)
    # No payload written: the notice states no value after pass 1 either.

    _insert_candidate(db_conn, candidate_id=CANDIDATE_ID, primary_notice_id=notice_id)
    _store_a_rate_day(db_conn)

    _, notice_values = backfill_notices(db_conn, tmp_path)
    counts = backfill_candidates(db_conn, notice_values)

    # `converted`, `no_rate` and `no_value` are not asserted here: they sum over
    # every real candidate in the table, not just ours (see the note in
    # test_pass_two_converts_a_candidate_at_the_stored_rate). The row check below
    # is what proves our own candidate landed in `no_value`.
    assert counts.examined >= 1

    row = db_conn.execute(
        "select estimated_value, value_currency, estimated_value_usd from candidates where id = %s",
        (CANDIDATE_ID,),
    ).fetchone()
    assert row == (None, None, None)


def test_pass_two_running_twice_changes_nothing(db_conn, tmp_path: Path):
    content_hash = f"backfilltest{uuid.uuid4().hex}"
    notice_id = _insert_notice(db_conn, content_hash=content_hash)

    directory = tmp_path / SOURCE_ID
    directory.mkdir(parents=True)
    (directory / f"{content_hash}.json").write_text(
        json.dumps(_liberia_payload(amount=10625, currency="USD")), encoding="utf-8"
    )

    _insert_candidate(db_conn, candidate_id=CANDIDATE_ID, primary_notice_id=notice_id)
    _store_a_rate_day(db_conn)

    _, notice_values = backfill_notices(db_conn, tmp_path)
    first = backfill_candidates(db_conn, notice_values)
    second = backfill_candidates(db_conn, notice_values)

    # `first.updated` is at least this fixture's candidate and, on a database that
    # already holds converted candidates, every one of those too: `_store_a_rate_day`
    # replaces today's rates inside this rolled-back transaction, so their USD figure
    # is recomputed against it. Until 2026-09-13 this asserted exactly 1, which held
    # only while no candidate had been converted yet. Idempotency is the second line.
    assert first.updated >= 1
    assert second.updated == 0
    row = db_conn.execute("select estimated_value_usd from candidates where id = %s", (CANDIDATE_ID,)).fetchone()
    assert row == (Decimal("10625.00"),)
