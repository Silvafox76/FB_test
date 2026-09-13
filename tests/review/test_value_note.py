"""`value_note` (migration 016): a lot-only value read by a reviewer, not "not stated".

BOAMP's lot-only notices state no procedure total but do state per-lot amounts;
`estimated_value` and `value_currency` stay null for them (rule 9 — summing lots
would be this pipeline's own arithmetic, not the published figure), and
`value_note` carries what was published instead. Two things must be true of that
text:

  - a reviewer reading the candidate page (and the queue list) sees it verbatim
    in place of "not stated";
  - the record builder's Pricing Notes carries it too, without ever inventing a
    Total Opportunity Amount or a Currency from lots that were never summed.

The record-builder case needs no database: `build_record` is pure, so it is
tested directly against a hand-built `RecordCandidate`. The candidate-page case
is inserted here with the owner connection, following
`tests/review/test_export.py`'s `approved` fixture, rather than editing the
shared `staged` fixture in `tests/review/conftest.py` (owned by another agent
tonight).
"""

from __future__ import annotations

import re
import uuid
from datetime import date

import pytest
import yaml
from starlette.testclient import TestClient

from monitor.registry.load import CONFIG_DIR, load_function_map
from monitor.stage.record import RecordCandidate, build_record
from monitor.stage.stager import FIXTURE_ID_FLOOR
from review import decisions
from review.app import app
from tests.review.fixture_cleanup import safe_execute

pytestmark = pytest.mark.roles

# Verbatim from monitor/normalise/boamp.py's docstring, one of the 22 recorded
# lot-only notices (26-87466.xml) — not invented for this test.
NOTE = "Published per lot, no total: lot 1 440000 EUR; lot 2 not published"


@pytest.fixture(scope="module")
def defaults() -> dict:
    return yaml.safe_load((CONFIG_DIR / "record_defaults.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def function_map() -> list[dict]:
    return load_function_map()


def test_pricing_notes_carries_a_lot_only_value_note_and_the_amount_stays_placeholder(defaults, function_map):
    """No procedure total, so nothing to convert and nothing to sum from the lots."""
    candidate = RecordCandidate(
        id="C000999",
        title_en="Renovation of two departmental buildings",
        buyer="Departement Test",
        country="FR",
        country_name="France",
        region="Europe",
        admin_level="local",
        score=60,
        summary_en="Renovation works, published per lot.",
        matched_function_ids=(),
        system_names=(),
        procurement_type="works",
        estimated_value=None,
        value_currency=None,
        estimated_value_usd=None,
        value_rate=None,
        value_rate_date=None,
        eligibility_flags=(),
        deadline_at=date(2026, 10, 1),
        value_note=NOTE,
    )

    record = build_record(candidate, [], function_map, defaults, reviewer="Ryan Dear", approved_on=date(2026, 9, 13))

    assert NOTE in record["Pricing Notes"]
    assert "not stated" not in record["Pricing Notes"]
    # Never summed from the lots: the placeholder currency and zero, exactly as a
    # candidate with no value at all would get.
    assert record["Total Opportunity Amount"] == defaults["placeholders"]["zero"]
    assert record["Currency"] == defaults["suggested"]["currency"]


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def lot_only_candidate(owner, review):
    """One pending_review candidate with no procedure total but a value_note."""
    marker = uuid.uuid4().hex[:8]
    candidate_id = f"C{FIXTURE_ID_FLOOR + int(marker, 16) % (1_000_000 - FIXTURE_ID_FLOOR):06d}"
    source_id = f"test-lot-{marker}"
    content_hash = f"sha256:lot{marker}"

    owner.execute(
        """
        insert into sources (id, name, country, admin_level, language, stream, access_type,
                             connector_class, wave, tos_status, enabled, expected_min,
                             expected_max, max_consecutive_failures)
        values (%s, 'BOAMP', 'FR', 'local', 'fr', 'feed', 'api', 'FeedConnector', 1, 'cleared',
                false, 1, 50, 3)
        """,
        (source_id,),
    )
    owner.execute(
        """
        insert into notices_raw (content_hash, source_id, url, storage_path, mime)
        values (%s, %s, 'https://example.invalid/notice', 'raw/test.json', 'application/json')
        """,
        (content_hash, source_id),
    )
    notice_id = owner.execute(
        """
        insert into notices (content_hash, source_id, url, title, country, admin_level,
                             language, status, value_note)
        values (%s, %s, 'https://example.invalid/notice', 'Lot-only value note test notice', 'FR',
                'local', 'fr', 'scored', %s)
        returning id
        """,
        (content_hash, source_id, NOTE),
    ).fetchone()[0]
    owner.execute(
        """
        insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                buyer, country, admin_level, summary_en, matched_functions,
                                system_names, procurement_type, estimated_value, value_currency,
                                estimated_value_usd, value_rate, value_rate_date, value_note,
                                eligibility_flags, deadline_at)
        values (%s, %s, 60, 'pending_review', 'Europe', 'fr', 'Lot-only value note test candidate',
                'Departement Test', 'FR', 'local', 'Renovation works, published per lot.', '[]'::jsonb,
                %s, 'works', null, null, null, null, null, %s,
                %s, '2026-10-01T12:00:00Z')
        """,
        (candidate_id, notice_id, [], NOTE, []),
    )
    owner.execute(
        """
        insert into candidate_notices (candidate_id, notice_id, match_method, match_score)
        values (%s, %s, 'content_hash', 100)
        """,
        (candidate_id, notice_id),
    )

    yield candidate_id

    review.rollback()
    safe_execute(owner, "delete from events where entity_id = %s", (candidate_id,), context="lot_only: events")
    safe_execute(
        owner,
        "delete from candidate_notices where candidate_id = %s",
        (candidate_id,),
        context="lot_only: candidate_notices",
    )
    safe_execute(owner, "delete from candidates where id = %s", (candidate_id,), context="lot_only: candidates")
    safe_execute(owner, "delete from notices where id = %s", (notice_id,), context="lot_only: notices")
    safe_execute(owner, "delete from notices_raw where source_id = %s", (source_id,), context="lot_only: notices_raw")
    safe_execute(owner, "delete from sources where id = %s", (source_id,), context="lot_only: sources")


def value_block(page: str) -> str:
    """The "Estimated value" fact, the value block this step changes.

    Scoped rather than a whole-page assertion because the same page also renders
    the proposed record preview (`review/decisions.py`'s `load_candidate`, out of
    this session's file list), which does not yet select `value_note` onto the
    `RecordCandidate` it builds and so still reads "not stated" in its Pricing
    Notes preview until that query is updated — see the report.
    """
    match = re.search(r"<dt>Estimated value</dt><dd>(.*?)</dd>", page, re.DOTALL)
    assert match, "candidate page has no Estimated value block"
    return match.group(1)


def test_candidate_page_shows_the_lot_only_value_note_instead_of_not_stated(client, lot_only_candidate):
    page = client.get(f"/candidate/{lot_only_candidate}").text
    block = value_block(page)

    assert NOTE in block
    assert "not stated" not in block


def test_the_queue_shows_the_lot_only_value_note_too(client, lot_only_candidate):
    """The smallest fix: the queue's Value column shares `value_narrative` with the
    candidate page, so a lot-only candidate reads its own published figures there
    too rather than "not stated"."""
    page = client.get("/").text

    assert NOTE in page


def test_approving_a_lot_only_candidate_carries_the_note_into_pricing_notes(owner, review, lot_only_candidate):
    """The real write path, not a hand-built RecordCandidate: `review/decisions.py`'s
    `SELECT_CANDIDATE` now reads `value_note`, so the record `approve()` inserts
    into `approved_records` reads the published note rather than "not stated"
    (rule 12: the same one write path, one more column read)."""
    record_id = decisions.approve(review, lot_only_candidate, "Ryan Dear")

    stored = owner.execute("select record from approved_records where id = %s", (record_id,)).fetchone()[0]

    assert NOTE in stored["Pricing Notes"]
    assert "not stated" not in stored["Pricing Notes"]

    review.rollback()
    safe_execute(owner, "delete from events where entity_id = any(%s::text[])", ([lot_only_candidate, record_id],))
    safe_execute(owner, "update candidates set approved_record_id = null where id = %s", (lot_only_candidate,))
    safe_execute(owner, "delete from approved_records where id = %s", (record_id,))
