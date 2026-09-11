"""Drill 5: the pipeline tries to write an approved record. Postgres refuses it.

    uv run python scripts/drills/drill5_pipeline_insert.py

**What it proves.** Checkpoint 11 and checkpoint 12, in the only place they can be proved:
the grants. `monitor_pipeline` has no privilege of any kind on `approved_records` - not
insert, not select - so the one thing this whole design exists to prevent, a pipeline that
creates an approved record, is refused by the database rather than by anybody's discipline.
`tests/roles/test_roles.py` asserts the same thing on every commit; this is the version a
person runs on the host, against the grants that are actually deployed there, when they
want to see it happen.

**Why it connects through `monitor/db.py` and not with a URL of its own.** The question is
not whether some role somewhere is refused, it is whether *the role the pipeline gets* is
refused. `db.connect("pipeline")` is the only way any pipeline code obtains a connection,
and the role is chosen by which environment variable is read rather than by an argument
(rule 11), so going through it means the drill cannot accidentally prove something about a
connection the pipeline would never have.

**What "refused" has to mean here.** The exception class matters, not just that something
raised. `insufficient_privilege` is the refusal; a `foreign_key_violation` or a
`check_violation` would mean the insert had got past the grant and was stopped by the
table's own shape instead, which would be a finding: the privilege check would have been
absent and only the fixture's unreality would have saved us. So the drill uses a candidate
id that does not exist, on purpose. The privilege check happens first, and if it ever
stops happening, the error changes class and this drill fails.

**The control.** `monitor_review` reads the same table in the same statement and is
allowed to, which is what says the refusal is about the role and not about a table that is
broken or missing.

**What it changes.** Nothing. Both statements raise, both connections are rolled back, and
neither role holds delete on anything anyway.
"""

from __future__ import annotations

from _drill import Drill, connection, run
from psycopg import errors

from monitor import db

# A record id and a candidate id that do not exist. Deliberate: see the docstring. The
# privilege refusal is raised before either is looked at, and that ordering is the point.
RECORD_ID = "R999005"
ABSENT_CANDIDATE = "C999005"

INSERT_APPROVED_RECORD = """
    insert into approved_records (id, candidate_id, record, approved_by)
    values (%s, %s, '{}'::jsonb, 'the pipeline, which does not approve anything')
"""


def refusal(conn, sql: str, params: tuple) -> tuple[str, str]:
    """Run one statement that must be refused; return the error's class and first line."""
    try:
        conn.execute(sql, params)
    except errors.Error as refused:
        conn.rollback()
        return type(refused).__name__, str(refused).strip().splitlines()[0]
    conn.rollback()
    return "", "the statement was accepted"


def main() -> int:
    drill = Drill(
        5,
        "the pipeline cannot write, or even read, an approved record",
        "checkpoints 11 and 12: two runtime roles, and one code path into approved_records",
        "insert and select both raise InsufficientPrivilege as monitor_pipeline",
    )

    # The connection the pipeline itself would get, obtained the only way it can be.
    with db.connect("pipeline") as pipeline:
        role = pipeline.execute("select current_user").fetchone()[0]
        drill.check(
            "the drill is connected as the pipeline's own role", role == "monitor_pipeline", f"current_user {role!r}"
        )

        raised, message = refusal(pipeline, INSERT_APPROVED_RECORD, (RECORD_ID, ABSENT_CANDIDATE))
        drill.check(
            "the insert is refused, and refused for want of privilege rather than for want of a row",
            raised == "InsufficientPrivilege",
            f"{raised or 'nothing raised'}: {message}",
        )

        raised, message = refusal(pipeline, "select count(*) from approved_records", ())
        drill.check(
            "reading the table is refused too: no privilege of any kind, select included",
            raised == "InsufficientPrivilege",
            f"{raised or 'nothing raised'}: {message}",
        )

        left = pipeline.execute(
            "select count(*) from candidates where approved_record_id = %s", (RECORD_ID,)
        ).fetchone()[0]
        drill.check("nothing was written: the pipeline can still read its own tables", left == 0, "rolled back")

    # The control: the reviewer's role reads the same table in the same statement.
    with connection("DATABASE_URL_REVIEW") as review:
        rows = review.execute("select count(*) from approved_records").fetchone()[0]
        drill.check(
            "the control: monitor_review reads the same table, so the refusal is about the role",
            isinstance(rows, int),
            f"{rows} approved records visible to the reviewer",
        )

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
