"""The five pages, driven end to end against a live database.

Not a screenshot test and not a test of the CSS. What it checks is the part that
would be wrong silently: that every page renders with real rows in it, that the
queue's region filter selects rather than decorates, that the candidate page carries
the D31 duplicate-check reminder the step makes permanent, and that a decision taken
through the form lands in `approved_records` and shows up in `/decided` and
`/audit`.

The app opens its own connections through `db.connect("review")`, so this needs
DATABASE_URL_REVIEW set, the same as everything else under tests/review.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from urllib.parse import unquote

import pytest
from starlette.testclient import TestClient

from monitor.stage.stager import FIXTURE_ID_FLOOR
from review.app import app

pytestmark = pytest.mark.roles

REMINDER = "The Monitor never searches the CRM (D31); this check is the only duplicate control."


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def staged_with_value(owner, review):
    """Stage a candidate carrying a chosen combination of the five value columns.

    `conftest.staged` fixes its row to USD at the identity rate, which cannot
    exercise the three cases a reviewer must tell apart (a value with a USD
    figure, a value with none, and no value at all). This is the same insert
    shape with the value columns left to the caller, and its own teardown.
    """
    created: list[tuple[str, int, str]] = []

    def make(
        *,
        estimated_value=None,
        value_currency=None,
        estimated_value_usd=None,
        value_rate=None,
        value_rate_date=None,
    ) -> str:
        marker = uuid.uuid4().hex[:8]
        candidate_id = f"C{FIXTURE_ID_FLOOR + int(marker, 16) % (1_000_000 - FIXTURE_ID_FLOOR):06d}"
        source_id = f"test-val-{marker}"
        content_hash = f"sha256:val{marker}"

        owner.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, 'Test source', 'GH', 'national', 'en', 'feed', 'api', 'FeedConnector',
                    1, 'cleared', false, 1, 50, 3)
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
                                 language, status)
            values (%s, %s, 'https://example.invalid/notice', 'Value display test notice', 'GH',
                    'national', 'en', 'scored')
            returning id
            """,
            (content_hash, source_id),
        ).fetchone()[0]
        owner.execute(
            """
            insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                    buyer, country, admin_level, summary_en, matched_functions,
                                    system_names, procurement_type, estimated_value, value_currency,
                                    estimated_value_usd, value_rate, value_rate_date,
                                    eligibility_flags, deadline_at)
            values (%s, %s, 78, 'pending_review', 'West Africa', 'en', 'Value display test candidate',
                    'Ministry of Finance', 'GH', 'national', 'Test summary', '[]'::jsonb, %s,
                    'system', %s, %s, %s, %s, %s, %s, '2026-11-30T17:00:00Z')
            """,
            (
                candidate_id,
                notice_id,
                [],
                estimated_value,
                value_currency,
                estimated_value_usd,
                value_rate,
                value_rate_date,
                [],
            ),
        )
        created.append((candidate_id, notice_id, source_id))
        return candidate_id

    yield make

    review.rollback()
    for candidate_id, notice_id, source_id in created:
        owner.execute("delete from events where entity_id = %s", (candidate_id,))
        owner.execute("delete from candidate_notices where candidate_id = %s", (candidate_id,))
        owner.execute("delete from candidates where id = %s", (candidate_id,))
        owner.execute("delete from notices where id = %s", (notice_id,))
        owner.execute("delete from notices_raw where source_id = %s", (source_id,))
        owner.execute("delete from sources where id = %s", (source_id,))


def test_the_queue_lists_the_staged_candidate_with_its_minutes(client, staged):
    page = client.get("/").text

    assert staged in page
    assert "minutes of review" in page
    assert "West Africa" in page


def test_the_region_filter_selects_rather_than_decorates(client, staged):
    """The fixture is West Africa, so filtering to Europe must drop it."""
    assert staged in client.get("/?region=West+Africa").text
    assert staged not in client.get("/?region=Europe").text


def test_the_queue_shows_the_value_with_its_own_currency_not_a_bare_number(client, staged):
    """`staged` carries USD 4,200,000; the header must not hardcode a currency either."""
    page = client.get("/").text

    assert "USD 4,200,000" in page
    assert "Value (USD)" not in page, "the currency comes from the row, never a literal in the template"


# --- the three value cases a reviewer must tell apart at a glance ------------


def test_candidate_page_shows_a_value_with_its_usd_derivation_and_rate(client, staged_with_value):
    candidate_id = staged_with_value(
        estimated_value=Decimal("4428444.00"),
        value_currency="UAH",
        estimated_value_usd=99_408,
        value_rate=Decimal("44.5483"),
        value_rate_date=date(2026, 9, 14),
    )
    page = client.get(f"/candidate/{candidate_id}").text

    assert "UAH 4,428,444" in page
    assert "USD 99,408" in page
    assert "44.5483" in page
    assert "2026-09-14" in page


def test_candidate_page_shows_a_value_with_no_usd_rate_held(client, staged_with_value):
    candidate_id = staged_with_value(estimated_value=Decimal("5000000.00"), value_currency="NGN")
    page = client.get(f"/candidate/{candidate_id}").text

    assert "NGN 5,000,000" in page
    assert "no USD rate held for NGN" in page


def test_candidate_page_shows_no_value_stated(client, staged_with_value):
    candidate_id = staged_with_value()
    page = client.get(f"/candidate/{candidate_id}").text

    assert "not stated" in page


def test_the_candidate_page_carries_the_duplicate_check_reminder(client, staged):
    page = client.get(f"/candidate/{staged}").text

    assert REMINDER in page
    assert "Ministry of Finance" in page
    assert "integrated financial management system" in page
    # The proposed payload is on the page as the reviewer would read it, and every
    # appendix E column is an editable field.
    assert 'name="field:Opportunity Name"' in page
    assert 'name="field:Total Opportunity Amount"' in page


def test_an_unknown_candidate_is_a_404_not_a_traceback(client):
    assert client.get("/candidate/C999999").status_code == 404


def test_approving_through_the_form_writes_the_record_and_shows_it(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/approve",
        data={"reviewer": "Ryan Dear", "field:Opportunity Name": "Ghana GIFMIS Modernisation"},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert posted.headers["location"] == "/decided"

    record = review.execute(
        "select record, approved_by, edited from approved_records where candidate_id = %s", (staged,)
    ).fetchone()
    assert record[1] == "Ryan Dear"
    assert record[2] is True
    assert record[0]["Opportunity Name"] == "Ghana GIFMIS Modernisation"

    decided = client.get("/decided").text
    assert staged in decided
    assert "Ryan Dear" in decided

    audit = client.get("/audit?entity_type=candidate").text
    assert staged in audit
    assert "approved" in audit


def test_approving_with_a_blank_reviewer_comes_back_with_the_message(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/approve",
        data={"reviewer": "  "},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert posted.headers["location"].startswith(f"/candidate/{staged}?error=")
    assert "reviewer name is required" in unquote(posted.headers["location"])

    assert review.execute("select count(*) from approved_records where candidate_id = %s", (staged,)).fetchone()[0] == 0


def test_rejecting_through_the_form_stores_the_reason_and_the_note(client, review, staged):
    client.post(
        f"/candidate/{staged}/reject",
        data={"reviewer": "Matthew Olivier", "reason": "Out of geography", "note": "buyer is in Argentina"},
        follow_redirects=False,
    )

    status, reason = review.execute(
        "select status, rejection_reason from candidates where id = %s", (staged,)
    ).fetchone()
    assert status == "rejected"
    assert reason == "Out of geography: buyer is in Argentina"


def test_rejecting_with_no_reason_is_refused(client, review, staged):
    posted = client.post(
        f"/candidate/{staged}/reject",
        data={"reviewer": "Matthew Olivier", "reason": "", "note": ""},
        follow_redirects=False,
    )
    assert "rejection reason is required" in unquote(posted.headers["location"])
    assert review.execute("select status from candidates where id = %s", (staged,)).fetchone()[0] == "pending_review"


def test_the_sources_page_shows_health(client, staged):
    page = client.get("/sources").text

    assert "World Bank procurement notices" in page
    assert "Zero-yield runs" in page


def test_the_audit_page_filters_by_entity(client, staged):
    client.post(f"/candidate/{staged}/approve", data={"reviewer": "Ryan Dear"}, follow_redirects=False)

    records = client.get("/audit?entity_type=approved_record").text
    assert "system (post-approval, as Ryan Dear)" in records
    assert "rejected" not in records or "approved_record" in records


def test_a_decided_candidate_shows_its_decision_instead_of_the_form(client, staged):
    client.post(f"/candidate/{staged}/approve", data={"reviewer": "Ryan Dear"}, follow_redirects=False)

    page = client.get(f"/candidate/{staged}").text
    assert "Already decided" in page
    assert REMINDER not in page, "the decision panel is gone, and so is its reminder"
