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

from urllib.parse import unquote

import pytest
from starlette.testclient import TestClient

from review.app import app

pytestmark = pytest.mark.roles

REMINDER = "The Monitor never searches the CRM (D31); this check is the only duplicate control."


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_the_queue_lists_the_staged_candidate_with_its_minutes(client, staged):
    page = client.get("/").text

    assert staged in page
    assert "minutes of review" in page
    assert "West Africa" in page


def test_the_region_filter_selects_rather_than_decorates(client, staged):
    """The fixture is West Africa, so filtering to Europe must drop it."""
    assert staged in client.get("/?region=West+Africa").text
    assert staged not in client.get("/?region=Europe").text


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
