"""The single write path into approved_records.

This is the checkpoint's application half. `tests/roles/test_roles.py` proves the
database refuses the pipeline; this proves the one code path that is allowed
through writes a complete record, a named reviewer, an audit trail, and all of it
or none of it.

It needs a live database with all migrations applied, and it does not skip when one
is absent, for the reason `test_roles.py` gives: a checkpoint test that quietly
passes because nothing was there to test is worse than no test.

The staged candidate it works on is in `conftest.py`, shared with the page tests.
"""

from __future__ import annotations

import psycopg
import pytest
import yaml

from monitor.registry.load import CONFIG_DIR
from review import decisions
from review.decisions import DecisionRefused, approve, reject, rejection_reasons

pytestmark = pytest.mark.roles


def approved_count(conn, candidate_id: str) -> int:
    return conn.execute("select count(*) from approved_records where candidate_id = %s", (candidate_id,)).fetchone()[0]


def events_for(conn, entity_id: str) -> list[tuple]:
    return conn.execute(
        "select action, actor, before, after from events where entity_id = %s order by id",
        (entity_id,),
    ).fetchall()


# --- a blank reviewer writes nothing -----------------------------------------


def test_approve_with_a_blank_reviewer_raises_and_writes_nothing(review, staged):
    with pytest.raises(DecisionRefused, match="reviewer name is required"):
        approve(review, staged, "   ")

    assert approved_count(review, staged) == 0
    assert review.execute("select status from candidates where id = %s", (staged,)).fetchone()[0] == "pending_review"


def test_reject_without_a_reason_raises(review, staged):
    with pytest.raises(DecisionRefused, match="rejection reason is required"):
        reject(review, staged, "Ryan Dear", "  ")

    assert review.execute("select status from candidates where id = %s", (staged,)).fetchone()[0] == "pending_review"


def test_reject_with_a_blank_reviewer_raises(review, staged):
    with pytest.raises(DecisionRefused, match="reviewer name is required"):
        reject(review, staged, "", "Not PFM")


# --- approve writes one complete record --------------------------------------


def test_approve_inserts_exactly_one_record_with_every_appendix_e_column(review, staged):
    record_id = approve(review, staged, "Ryan Dear")

    assert approved_count(review, staged) == 1

    row = review.execute(
        "select id, record, approved_by, edited from approved_records where candidate_id = %s",
        (staged,),
    ).fetchone()
    assert row[0] == record_id
    assert row[2] == "Ryan Dear"
    assert row[3] is False

    spec = yaml.safe_load((CONFIG_DIR / "record_defaults.yaml").read_text(encoding="utf-8"))
    expected = [column["name"] for column in spec["columns"]]

    record = row[1]
    missing = [name for name in expected if name not in record]
    assert missing == [], f"appendix E columns absent from the approved record: {missing}"
    # Presence and nothing beyond it. Column *order* is the export's contract with
    # Zoho's import mapper and it is asserted where it is decided, in
    # tests/unit/test_record.py: Postgres sorts jsonb keys on storage, so the order
    # read back here is Postgres's and not the builder's. Step 11's export writes
    # its header from record_defaults.yaml, so nothing downstream reads this order.
    assert set(record) - set(expected) == set(), "the record invented a column appendix E does not define"
    assert record["monitor_candidate_id"] == staged


def test_approve_transitions_the_candidate_and_links_the_record(review, staged):
    record_id = approve(review, staged, "Ryan Dear")

    status, reviewer, linked = review.execute(
        "select status, reviewer, approved_record_id from candidates where id = %s", (staged,)
    ).fetchone()
    assert (status, reviewer, linked) == ("approved", "Ryan Dear", record_id)


def test_approve_writes_both_events(review, staged):
    record_id = approve(review, staged, "Ryan Dear")

    candidate_events = events_for(review, staged)
    assert [event[0] for event in candidate_events] == ["approved"]
    assert candidate_events[0][1] == "Ryan Dear"
    assert candidate_events[0][2] == "pending_review"

    record_events = events_for(review, record_id)
    assert [event[0] for event in record_events] == ["created"]
    assert record_events[0][1] == "system (post-approval, as Ryan Dear)"


def test_the_donor_source_in_the_cluster_reaches_the_record(review, staged):
    """Not a record-builder test: proof the cluster query feeds it the second source."""
    approve(review, staged, "Ryan Dear")

    record = review.execute("select record from approved_records where candidate_id = %s", (staged,)).fetchone()[0]
    assert record["Funding Source"] == "Government budget", "the fixture's donor source is not a known donor stream"
    assert "World Bank procurement notices" in record["Partners Involved"]
    assert record["Shipping Country"] == "Ghana"


# --- edit then approve --------------------------------------------------------


def test_edit_then_approve_stores_the_edits_flags_them_and_audits_each_one(review, staged):
    edits = {
        "Opportunity Name": "Ghana GIFMIS Modernisation",
        "Total Opportunity Amount": "5000000",
        "Customer Type": "Existing Customer",
    }
    approve(review, staged, "Ryan Dear", edits)

    record, edited = review.execute(
        "select record, edited from approved_records where candidate_id = %s", (staged,)
    ).fetchone()
    assert edited is True
    assert record["Opportunity Name"] == "Ghana GIFMIS Modernisation"
    assert record["Total Opportunity Amount"] == "5000000"
    assert record["Customer Type"] == "Existing Customer"

    edit_events = [event for event in events_for(review, staged) if event[0] == "edited"]
    assert len(edit_events) == 3, "one edited event per changed key, with before and after"
    changed_keys = {event[3].split(":")[0] for event in edit_events}
    assert changed_keys == set(edits)
    name_event = next(event for event in edit_events if event[3].startswith("Opportunity Name"))
    assert "integrated financial management system" in name_event[2]


def test_an_edit_that_retypes_the_same_value_is_not_an_edit(review, staged):
    approve(review, staged, "Ryan Dear", {"Customer Type": "New Customer"})

    edited = review.execute("select edited from approved_records where candidate_id = %s", (staged,)).fetchone()[0]
    assert edited is False
    assert [event for event in events_for(review, staged) if event[0] == "edited"] == []


def test_an_edit_to_a_column_appendix_e_does_not_define_raises(review, staged):
    with pytest.raises(DecisionRefused, match="appendix E"):
        approve(review, staged, "Ryan Dear", {"Invented Column": "x"})

    assert approved_count(review, staged) == 0


# --- one transaction ----------------------------------------------------------


def test_a_failure_on_the_events_insert_leaves_no_approved_record(review, staged, monkeypatch):
    """The whole approve is one transaction, tested by breaking the last write in it."""

    def refuse(*args, **kwargs):
        raise psycopg.errors.InsufficientPrivilege("events insert failed")

    monkeypatch.setattr(decisions, "write_event", refuse)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        approve(review, staged, "Ryan Dear")

    assert approved_count(review, staged) == 0
    assert review.execute("select status from candidates where id = %s", (staged,)).fetchone()[0] == "pending_review"


def test_a_candidate_already_decided_cannot_be_decided_again(review, staged):
    approve(review, staged, "Ryan Dear")

    with pytest.raises(DecisionRefused, match="not pending_review"):
        approve(review, staged, "Ryan Dear")
    with pytest.raises(DecisionRefused, match="not pending_review"):
        reject(review, staged, "Ryan Dear", "Not PFM")

    assert approved_count(review, staged) == 1


# --- reject -------------------------------------------------------------------


def test_reject_records_the_reason_and_the_reviewer(review, staged):
    reject(review, staged, "Matthew Olivier", "Out of geography; buyer is outside the 47 countries")

    status, reviewer, reason = review.execute(
        "select status, reviewer, rejection_reason from candidates where id = %s", (staged,)
    ).fetchone()
    assert status == "rejected"
    assert reviewer == "Matthew Olivier"
    assert reason.startswith("Out of geography")

    events = events_for(review, staged)
    assert [event[0] for event in events] == ["rejected"]
    assert events[0][1] == "Matthew Olivier"


def test_the_database_refuses_a_rejection_with_no_reason_even_without_the_app(review, staged):
    """Rule 14 is enforced in Postgres, not only in decisions.py or the form."""
    with pytest.raises(psycopg.errors.CheckViolation):
        review.execute(
            "update candidates set status = 'rejected', reviewer = 'Ryan Dear' where id = %s",
            (staged,),
        )
    review.rollback()


def test_the_reason_list_is_config_not_code():
    reasons = rejection_reasons()
    assert "Not PFM" in reasons
    assert len(reasons) >= 5
