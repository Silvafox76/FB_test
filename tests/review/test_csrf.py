"""SR-10: no CSRF or Origin control on the four decision/export POST endpoints.

`docs/security_review_2026-09.md` section 6.2. The four POST handlers write on the
reviewer's behalf with no session and no cookie, so nothing but a human reading the
page was ever supposed to stand between a candidate and an approved record. Without
an `Origin` check, any page the reviewer's browser has open can submit the same form
cross-origin and get the same write, because the loopback binding stops other
machines and does nothing about the reviewer's own browser.

Each test below exercises the real handler through `TestClient`, against the same
`staged` fixture the rest of `tests/review/` uses, and asserts database state rather
than only the status code: the status code is not what an attacker who never reads
the response cares about, so a passing test has to show that a forged-origin POST
left `candidates.status` and `approved_records` untouched.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from review import app as app_module
from review import export as export_module
from review.app import allowed_origin, app
from review.decisions import approve
from review.export import batch_paths

pytestmark = pytest.mark.roles

HOSTILE_ORIGIN = "https://attacker.example"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def exports_dir(tmp_path, monkeypatch) -> Path:
    """Batches are written to a temporary directory, never the repository's exports/."""
    monkeypatch.setenv(export_module.EXPORT_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def approved_record(review, staged) -> str:
    """The `staged` candidate, already approved, for the export and re-export tests.

    Approved directly through `decisions.approve` rather than through the form: what
    is under test in this file is the middleware in front of `/export` and
    `/export/re-export`, not the approve handler again.
    """
    return approve(review, staged, "Ryan Dear")


@pytest.fixture
def operator_name() -> str:
    """A per-test operator string, so an export's rows can only ever be this test's own.

    This runs against a shared, unpooled Postgres rather than a database created fresh
    per test run (`tests/roles` and `tests/review` both do), so a literal name like
    "Export Operator" is not safe to count rows by: another run, or the same suite run
    concurrently, can be mid-export under that exact string at the same moment.
    """
    return f"csrf-test-{uuid.uuid4().hex[:8]}"


def today_str() -> str:
    return datetime.now(UTC).date().isoformat()


def cleanup_batch(owner, record_id: str, batch_id: str) -> None:
    """Undo what a same-origin export in one of these tests leaves behind.

    `approved_records.export_batch` is a foreign key to `export_batches.batch_id`
    (`migrations/001_schema.sql`), so the stamp has to be cleared before the batch row
    can go, and a re-export writes its own event keyed by the batch id rather than the
    candidate or record id, so `conftest.staged`'s teardown does not reach it. Without
    this, a test in this file that actually exercises a real export would leave a batch
    row behind for the next run to trip over.
    """
    owner.execute("update approved_records set export_batch = null, exported_at = null where id = %s", (record_id,))
    owner.execute("delete from events where entity_type = 'export_batch' and entity_id = %s", (batch_id,))
    owner.execute("delete from export_batches where batch_id = %s", (batch_id,))


def candidate_status(review, candidate_id: str) -> str:
    return review.execute("select status from candidates where id = %s", (candidate_id,)).fetchone()[0]


def approved_count(review, candidate_id: str) -> int:
    return review.execute("select count(*) from approved_records where candidate_id = %s", (candidate_id,)).fetchone()[
        0
    ]


# --- the allowed origin, and where it comes from -------------------------------


def test_the_default_allowed_origin_is_the_apps_own_loopback_address(monkeypatch):
    monkeypatch.delenv(app_module.REVIEW_ORIGIN_ENV, raising=False)
    assert allowed_origin() == "http://127.0.0.1:8080"


def test_the_allowed_origin_is_read_from_the_environment(monkeypatch):
    """A deployment reached through an SSM tunnel on another port is not locked out.

    `allowed_origin` is read fresh on every call rather than cached at import time,
    so setting the variable here is enough; nothing needs reloading.
    """
    monkeypatch.setenv(app_module.REVIEW_ORIGIN_ENV, "http://127.0.0.1:9443")
    assert allowed_origin() == "http://127.0.0.1:9443"


# --- same-origin POSTs still work, on all four handlers ------------------------


def test_same_origin_approve_still_works(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/approve",
        data={"reviewer": "Ryan Dear"},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert candidate_status(review, staged) == "approved"
    assert approved_count(review, staged) == 1


def test_same_origin_reject_still_works(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/reject",
        data={"reviewer": "Matthew Olivier", "reason": "Out of geography", "note": ""},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert candidate_status(review, staged) == "rejected"


def test_same_origin_export_still_works(client, owner, review, exports_dir, approved_record, operator_name):
    today = today_str()
    posted = client.post(
        "/export",
        # record_ids names this test's own fixture record explicitly, so the batch can
        # only ever be this one row - never whatever else a real reviewer approved today
        # in the same shared database (the incident this restriction exists to prevent).
        data={"range_from": today, "range_to": today, "operator": operator_name, "record_ids": approved_record},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert posted.headers["location"].startswith("/export?batch=")

    batch_id = posted.headers["location"].removeprefix("/export?batch=")
    export_batch, exported_at = review.execute(
        "select export_batch, exported_at from approved_records where id = %s", (approved_record,)
    ).fetchone()
    assert export_batch == batch_id
    assert exported_at is not None

    cleanup_batch(owner, approved_record, batch_id)


def test_same_origin_re_export_still_works(client, owner, exports_dir, approved_record, operator_name):
    today = today_str()
    first = client.post(
        "/export",
        data={"range_from": today, "range_to": today, "operator": operator_name, "record_ids": approved_record},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    batch_id = first.headers["location"].removeprefix("/export?batch=")

    posted = client.post(
        "/export/re-export",
        data={"batch": batch_id, "operator": operator_name},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert f"batch={batch_id}" in posted.headers["location"]

    cleanup_batch(owner, approved_record, batch_id)


# --- a hostile Origin is refused, and nothing is written ------------------------


def test_hostile_origin_approve_is_refused_and_writes_nothing(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/approve",
        data={"reviewer": "Ryan Dear"},
        headers={"origin": HOSTILE_ORIGIN},
        follow_redirects=False,
    )
    assert posted.status_code == 403
    assert HOSTILE_ORIGIN in posted.text

    # The database state is the assertion that matters: the attacker never sees this
    # response, so a 403 that still let the write through would defeat the whole point.
    assert candidate_status(review, staged) == "pending_review"
    assert approved_count(review, staged) == 0


def test_hostile_origin_reject_is_refused_and_writes_nothing(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/reject",
        data={"reviewer": "Matthew Olivier", "reason": "Out of geography", "note": ""},
        headers={"origin": HOSTILE_ORIGIN},
        follow_redirects=False,
    )
    assert posted.status_code == 403
    assert candidate_status(review, staged) == "pending_review"


def test_hostile_origin_export_is_refused_and_produces_no_batch(
    client, review, exports_dir, approved_record, operator_name
):
    today = today_str()
    posted = client.post(
        "/export",
        data={"range_from": today, "range_to": today, "operator": operator_name, "record_ids": approved_record},
        headers={"origin": HOSTILE_ORIGIN},
        follow_redirects=False,
    )
    assert posted.status_code == 403

    assert list(exports_dir.iterdir()) == []
    export_batch, exported_at = review.execute(
        "select export_batch, exported_at from approved_records where id = %s", (approved_record,)
    ).fetchone()
    assert (export_batch, exported_at) == (None, None)
    assert (
        review.execute("select count(*) from export_batches where operator = %s", (operator_name,)).fetchone()[0] == 0
    )


def test_hostile_origin_re_export_is_refused(client, owner, exports_dir, approved_record, operator_name):
    today = today_str()
    real = client.post(
        "/export",
        data={"range_from": today, "range_to": today, "operator": operator_name, "record_ids": approved_record},
        headers={"origin": allowed_origin()},
        follow_redirects=False,
    )
    batch_id = real.headers["location"].removeprefix("/export?batch=")
    csv_path, _ = batch_paths(exports_dir, batch_id)
    original = csv_path.read_bytes()

    posted = client.post(
        "/export/re-export",
        data={"batch": batch_id, "operator": operator_name},
        headers={"origin": HOSTILE_ORIGIN},
        follow_redirects=False,
    )
    assert posted.status_code == 403
    assert csv_path.read_bytes() == original

    cleanup_batch(owner, approved_record, batch_id)


# --- no Origin at all is allowed, deliberately ----------------------------------


def test_no_origin_at_all_still_works(client, review, staged):
    """A same-origin form POST from an older browser, and the operator's own `curl`,
    both look exactly like this: no `Origin` header at all. Browsers attach `Origin`
    to every *cross-origin* POST by the Fetch standard's own requirement, so this is
    not a gap the hostile-page attack can use — the attack this control exists to stop
    is a browser being steered by a page it loaded, and that browser always sends the
    header. Refusing a request with no `Origin` would only ever refuse the harmless
    cases, which is why the middleware allows it.
    """
    posted = client.post(
        f"/candidate/{staged}/approve",
        data={"reviewer": "Ryan Dear"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert candidate_status(review, staged) == "approved"
