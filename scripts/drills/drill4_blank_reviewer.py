"""Drill 4: approving with a blank reviewer, through the real form. It is refused twice.

    uv run python scripts/drills/drill4_blank_reviewer.py

**What it proves.** Checkpoint 13: no candidate reaches `approved` without a named reviewer
recorded on the decision. And rule 14's shape, which is about where a check lives rather
than whether one exists: the form's `required` attribute and `review/decisions.py`'s
message are both conveniences, and the enforcement is in Postgres. So the drill refuses
the decision twice, by two routes:

  1. **Through the form.** `POST /candidate/{id}/approve` with the reviewer field blank
     comes back a 303 to the candidate page carrying the reason, and nothing is written:
     the candidate is still `pending_review`, there is no `approved_records` row and there
     is no `approved` event.
  2. **Through the database.** The same status transition attempted directly on a
     `monitor_review` connection raises `check_violation` from `refuse_pipeline_decision`
     (`migrations/002_roles.sql`, extended by `008`). That is the check that would still
     hold if `review/app.py` were bypassed entirely, which is the point of rule 14.

**And once with a name, to prove the form is not simply broken.** A drill that only watches
a refusal cannot tell a working guard from a form that refuses everything, and a 303 with
an error is what `review/app.py` returns for *any* `DecisionRefused` - a candidate that is
not pending_review would look the same. So the last check posts the identical form with a
reviewer's name in it and watches the record appear. If that check fails, the earlier
refusal proved nothing.

**Why the reviewer field is three spaces and not empty.** An empty field is refused by
anything, including a careless `if not reviewer`. A name that is whitespace is the one that
gets through a check that does not strip, and `review/decisions.py` strips before it tests.
This is the case worth drilling.

**Why Starlette's `TestClient` and not a browser.** It posts to the real `app` object
in-process: the real route, the real handler, the real `db.connect("review")`, the real
decision path. What a browser would add is the HTML form's own `required` attribute - the
part that cannot enforce anything and that this drill exists to look past. A uvicorn server
on 127.0.0.1 would add a port and a process to clean up and nothing to the evidence.

**What it changes.** One candidate at `pending_review` and, in the last check, the approved
record for it. Both are removed on the way out, whether the drill passed or failed.
"""

from __future__ import annotations

from urllib.parse import unquote

from _drill import Drill, connection, owner, run, staged_candidates
from psycopg import errors
from starlette.testclient import TestClient

from review.app import app

# Posted in the reviewer field. Not empty: whitespace is what gets through a check that
# does not strip, and this is the check.
BLANK_REVIEWER = "   "
NAMED_REVIEWER = "Drill Four Reviewer"

# One appendix E field, posted the way the form posts all 73, so the request is the shape
# `review/app.py:approve_candidate` reads rather than a stripped-down version of it.
EDITED_FIELD = "field:Opportunity Name"
EDITED_VALUE = "Drill 4 fixture opportunity"

APPROVED_EVENTS = """
    select count(*) from events
    where entity_type = 'candidate' and entity_id = %s and action = 'approved'
"""


def main() -> int:
    drill = Drill(
        4,
        "the form refuses an approval with a blank reviewer, and so does the database",
        "checkpoint 13 (a named reviewer on every decision) and rule 14 (enforced server side)",
        "303 back to the candidate with the reason; nothing written; the same write refused in Postgres",
    )

    with owner() as owner_conn, staged_candidates(owner_conn, 1) as fixture:
        candidate_id = fixture.candidate_ids[0]
        drill.note(f"candidate {candidate_id} is staged at pending_review")

        with TestClient(app) as client:
            page = client.get(f"/candidate/{candidate_id}")
            drill.check(
                "the candidate page renders with the decision form on it",
                page.status_code == 200 and 'name="reviewer"' in page.text,
                f"HTTP {page.status_code}, {len(page.text)} bytes",
            )

            refused = client.post(
                f"/candidate/{candidate_id}/approve",
                data={"reviewer": BLANK_REVIEWER, EDITED_FIELD: EDITED_VALUE},
                follow_redirects=False,
            )
            # Percent-decoded before it is read: the reason travels in a query string, so
            # "a reviewer name is required" arrives as a%20reviewer%20name%20...
            location = unquote(refused.headers.get("location", ""))
            drill.check(
                "the form refused it and sent the reviewer back to the candidate with the reason",
                refused.status_code == 303 and f"/candidate/{candidate_id}" in location,
                f"HTTP {refused.status_code} -> {location}",
            )
            drill.check(
                "the reason names what was missing",
                "reviewer name is required" in location,
                location,
            )

            with connection("DATABASE_URL_READONLY") as reader:
                status = reader.execute("select status from candidates where id = %s", (candidate_id,)).fetchone()[0]
                records = reader.execute(
                    "select count(*) from approved_records where candidate_id = %s", (candidate_id,)
                ).fetchone()[0]
                approvals = reader.execute(APPROVED_EVENTS, (candidate_id,)).fetchone()[0]

            drill.check("the candidate is still pending_review", status == "pending_review", f"status {status!r}")
            drill.check("no approved record was written", records == 0, f"approved_records rows {records}")
            drill.check("no approval event was written", approvals == 0, f"approved events {approvals}")

            # The same decision, one layer lower. Nothing above this line depends on the
            # application's own check being present.
            with connection("DATABASE_URL_REVIEW") as review:
                raised = ""
                try:
                    review.execute(
                        "update candidates set status = 'approved', reviewer = %s where id = %s",
                        (BLANK_REVIEWER, candidate_id),
                    )
                except errors.CheckViolation as refusal:
                    raised = str(refusal).strip().splitlines()[0]
                review.rollback()
            drill.check(
                "Postgres refuses the same transition even with the app out of the way",
                bool(raised),
                raised or "the update was accepted, which means rule 14 rests on the form alone",
            )

            # The control: the identical form, with a name in it.
            accepted = client.post(
                f"/candidate/{candidate_id}/approve",
                data={"reviewer": NAMED_REVIEWER, EDITED_FIELD: EDITED_VALUE},
                follow_redirects=False,
            )
            with connection("DATABASE_URL_READONLY") as reader:
                decided = reader.execute(
                    "select status, reviewer, approved_record_id from candidates where id = %s",
                    (candidate_id,),
                ).fetchone()
            drill.check(
                "the control: the same form with a name approves, so the refusal was about the name",
                accepted.status_code == 303
                and accepted.headers.get("location") == "/decided"
                and decided[0] == "approved"
                and decided[1] == NAMED_REVIEWER,
                f"{decided[2]} approved by {decided[1]!r}, candidate now {decided[0]!r}",
            )

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
