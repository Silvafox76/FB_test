"""The acquire stage's real insert, against the real schema.

Every other test of `monitor.fetch` stubs the connection, and the contract tests
assert on the `Notice` object rather than on a stored row. That gap is how a commit
shipped with an insert naming a column migration 012 had dropped: the suite was
green and every `monitor fetch` was broken. This runs `_store_notice` for real,
rolled back, so the SQL and the model have to agree or this fails.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest

from monitor.fetch import _store_notice
from monitor.models import Notice
from monitor.normalise.mapped import MappedNotice
from monitor.registry import load_sources


@pytest.fixture
def source():
    return next(s for s in load_sources() if s.id == "liberia")


def _mapper_returning(notice: Notice):
    return lambda document: MappedNotice(notice=notice)


def _notice(source_id: str, **overrides) -> Notice:
    fields = dict(
        content_hash=f"sha256:test-{uuid.uuid4().hex}",
        source_id=source_id,
        url="https://example.invalid/n",
        title="Supply of a treasury system",
        country="LR",
        admin_level="national",
        language="en",
        language_confidence=1.0,
    )
    fields.update(overrides)
    return Notice(**fields)


def test_a_notice_with_a_published_value_is_stored_with_both_halves(db_conn, source):
    notice = _notice(source.id, estimated_value=Decimal("10625.00"), value_currency="USD")

    assert _store_notice(db_conn, source, json.dumps({}), _mapper_returning(notice), notice.url, "application/json")

    row = db_conn.execute(
        "select estimated_value, value_currency from notices where content_hash = %s", (notice.content_hash,)
    ).fetchone()
    assert row == (Decimal("10625.00"), "USD")


def test_a_notice_stating_no_value_is_stored_with_neither_half(db_conn, source):
    notice = _notice(source.id)

    assert _store_notice(db_conn, source, json.dumps({}), _mapper_returning(notice), notice.url, "application/json")

    row = db_conn.execute(
        "select estimated_value, value_currency from notices where content_hash = %s", (notice.content_hash,)
    ).fetchone()
    assert row == (None, None)


def test_the_same_hash_twice_is_not_new(db_conn, source):
    notice = _notice(source.id, estimated_value=Decimal("1.00"), value_currency="EUR")
    mapper = _mapper_returning(notice)

    assert _store_notice(db_conn, source, json.dumps({}), mapper, notice.url, "application/json")
    assert not _store_notice(db_conn, source, json.dumps({}), mapper, notice.url, "application/json")
