"""The acquire stage's real insert, against the real schema, and its one-or-many contract.

Every other test of `monitor.fetch` stubs the connection, and the contract tests
assert on the `Notice` object rather than on a stored row. That gap is how a commit
shipped with an insert naming a column migration 012 had dropped: the suite was
green and every `monitor fetch` was broken. This runs `_store_notices` for real,
rolled back, so the SQL and the model have to agree or this fails.

Since 2026-09-13 a payload may map to several notices (a PDF bulletin, decision
51). The cases below pin both shapes through the same function.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from monitor.fetch import NormaliseError, _store_notices
from monitor.models import Notice, RawNotice
from monitor.normalise.mapped import MappedNotice
from monitor.registry import load_sources

REPO_FIXTURES = Path(__file__).resolve().parent.parent / "contract" / "fixtures"


@pytest.fixture
def source():
    return next(s for s in load_sources() if s.id == "liberia")


def _raw(source_id: str, url: str = "https://example.invalid/n") -> RawNotice:
    return RawNotice(
        source_id=source_id, url=url, fetched_at=datetime.now(UTC), mime="application/json", payload=json.dumps({})
    )


def _mapper_returning(*notices: Notice):
    """One notice back, or a list of them, the way a real mapper would."""
    if len(notices) == 1:
        return lambda document: MappedNotice(notice=notices[0])
    return lambda document: [MappedNotice(notice=n) for n in notices]


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


def _stored_value(conn, notice: Notice):
    return conn.execute(
        "select estimated_value, value_currency from notices where content_hash = %s", (notice.content_hash,)
    ).fetchone()


def test_a_notice_with_a_published_value_is_stored_with_both_halves(db_conn, source):
    notice = _notice(source.id, estimated_value=Decimal("10625.00"), value_currency="USD")

    assert _store_notices(db_conn, source, _raw(source.id), _mapper_returning(notice)) == (1, 1)
    assert _stored_value(db_conn, notice) == (Decimal("10625.00"), "USD")


def test_a_notice_stating_no_value_is_stored_with_neither_half(db_conn, source):
    notice = _notice(source.id)

    assert _store_notices(db_conn, source, _raw(source.id), _mapper_returning(notice)) == (1, 1)
    assert _stored_value(db_conn, notice) == (None, None)


def test_a_notice_with_a_value_note_is_stored_with_it_and_no_figure(db_conn, source):
    """Migration 016: the lot-only shape. The note is stored beside empty value
    columns; a column missing from the insert's list is the bug class this file
    exists for."""
    note = "Published per lot, no total: lot 1 440000 EUR; lot 2 not published"
    notice = _notice(source.id, value_note=note)

    assert _store_notices(db_conn, source, _raw(source.id), _mapper_returning(notice)) == (1, 1)
    assert _stored_value(db_conn, notice) == (None, None)
    stored = db_conn.execute("select value_note from notices where content_hash = %s", (notice.content_hash,))
    assert stored.fetchone() == (note,)


def test_a_notice_with_nothing_to_note_stores_an_empty_note_not_a_null(db_conn, source):
    notice = _notice(source.id)

    _store_notices(db_conn, source, _raw(source.id), _mapper_returning(notice))
    stored = db_conn.execute("select value_note from notices where content_hash = %s", (notice.content_hash,))
    assert stored.fetchone() == ("",)


def test_the_same_hash_twice_is_seen_but_not_new(db_conn, source):
    notice = _notice(source.id, estimated_value=Decimal("1.00"), value_currency="EUR")
    mapper = _mapper_returning(notice)

    assert _store_notices(db_conn, source, _raw(source.id), mapper) == (1, 1)
    assert _store_notices(db_conn, source, _raw(source.id), mapper) == (1, 0)


def test_one_payload_may_map_to_many_notices_and_seen_counts_the_notices(db_conn, source):
    """The bulletin shape: `seen` is what the registry's expected_items_per_run means."""
    first, second, third = (_notice(source.id) for _ in range(3))

    assert _store_notices(db_conn, source, _raw(source.id), _mapper_returning(first, second, third)) == (3, 3)
    # Re-running the same payload sees the same three and inserts none of them.
    assert _store_notices(db_conn, source, _raw(source.id), _mapper_returning(first, second, third)) == (3, 0)


def test_a_mapper_returning_nothing_from_a_payload_that_decoded_is_a_failure(db_conn, source):
    """Rule 4: zero-yield on a source that normally yields is not an empty success."""
    with pytest.raises(NormaliseError, match="no notices"):
        _store_notices(db_conn, source, _raw(source.id), lambda document: [])


def test_a_mime_with_no_decoder_is_a_failure_not_a_guess(db_conn, source):
    raw = RawNotice(
        source_id=source.id,
        url="https://example.invalid/n",
        fetched_at=datetime.now(UTC),
        mime="text/csv",
        payload="a,b",
    )
    with pytest.raises(NormaliseError, match="no decoder for mime 'text/csv'"):
        _store_notices(db_conn, source, raw, _mapper_returning(_notice(source.id)))


def test_a_pdf_payload_is_filed_once_and_its_mapper_reads_the_text_beside_the_issue_url(db_conn, source, monkeypatch):
    """The bulletin path end to end: base64 PDF in, text out, notices stored (decision 51)."""
    import base64
    import shutil

    from monitor import fetch as fetch_module

    # `store_payload` returns a repository-relative path, so the scratch storage
    # root has to sit inside the repository; it is removed below.
    scratch = fetch_module.REPO / "storage" / f"test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(fetch_module, "STORAGE", scratch)
    pdf = REPO_FIXTURES / "burkina_faso_avis.pdf"
    raw = RawNotice(
        source_id=source.id,
        url="https://example.invalid/issue-4478.pdf",
        fetched_at=datetime.now(UTC),
        mime="application/pdf",
        payload=base64.b64encode(pdf.read_bytes()).decode("ascii"),
    )
    seen_documents: list[dict] = []

    def mapper(document):
        seen_documents.append(document)
        return [MappedNotice(notice=_notice(source.id, title=f"from the bulletin {i}")) for i in range(2)]

    assert _store_notices(db_conn, source, raw, mapper) == (2, 2)
    (document,) = seen_documents
    assert document["url"] == raw.url
    assert "Montant prévisionnel" in document["text"]
    stored = scratch / source.id
    assert [path.suffix for path in stored.iterdir()] == [".pdf"]
    shutil.rmtree(scratch)
