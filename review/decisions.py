"""The only code path that inserts into `approved_records` (rule 12).

Everything this module does happens inside one transaction on a `monitor_review`
connection. A decision is either wholly recorded — the record, the candidate's new
status, the events that explain both — or not recorded at all. There is no partial
approval, no record without a reviewer, and no status change without an audit row.

Three things are worth saying about why the code is shaped this way.

**The reviewer's name is checked here and again in the database.** `refuse_pipeline_decision`
in `migrations/002_roles.sql` raises on a blank reviewer and `008` raises on a blank
rejection reason, so the check below is not what makes the rule hold. It exists so the
reviewer sees "reviewer name is required" rather than a Postgres exception, and so the
failure happens before the record is built. Rule 14's "client-side-only validation is a
finding" is about the form, not about this: the enforcement is in Postgres and this is
the message.

**Edits are applied over the built payload, and only over keys the payload has.**
An edit naming a column appendix E does not define would add a 74th column to the
export, whose header Zoho's import mapper would not match; rule 4 says that raises
rather than passing through. Every changed key gets its own `edited` event carrying
before and after, because "the reviewer changed something" is not an audit trail.

**The record is built here, not stored at staging time.** The payload a reviewer
approves is built from the candidate as it stands at the moment of the decision and
from the config versions live at that moment, so a threshold or placeholder changed
on Tuesday does not retroactively alter a record approved on Monday: Monday's record
is the jsonb that was inserted, and nothing updates it afterwards (the grant in 002
allows update on `exported_at` and `export_batch` and no other column).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import structlog
import yaml

from monitor.normalise.codes import country_name
from monitor.registry.load import CONFIG_DIR, load_function_map, load_record_defaults
from monitor.stage.record import ClusterSource, RecordCandidate, build_record

log = structlog.get_logger(__name__)

POST_APPROVAL_ACTOR = "system (post-approval, as {reviewer})"


class DecisionRefused(Exception):
    """The decision is not valid and nothing was written. The message is shown to the reviewer."""


def json_safe(value):
    """The one type-widening JSON serialisation of a built record needs (rule 4).

    `estimated_value` and `value_rate` arrive from Postgres as `Decimal` (numeric
    columns), which the standard library's JSON encoder does not know how to write.
    This is what keeps a real published amount from raising `TypeError` inside the
    approval transaction, which is not a hypothetical: every candidate the pipeline
    stages now carries one. `review/app.py` uses this too, as the `tojson` filter's
    default for the same reason on the same shape of dict, so the two do not
    diverge on what "JSON-safe" means for a candidate's record.

    A whole-currency-unit amount becomes a plain `int` rather than a `float` with a
    trailing ".0" — `4200000`, not `4200000.0` — because "a bare number, no
    thousands separators" is the export's own rule for this column and a bare
    number is what a person typed when the notice stated a round figure. Anything
    with real cents still becomes a `float`. Anything else reaching this function
    is a type `build_record` was never meant to produce, so it raises rather than
    being coerced silently.
    """
    if isinstance(value, Decimal):
        whole = value.to_integral_value()
        return int(whole) if value == whole else float(value)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def review_config() -> dict:
    return yaml.safe_load((CONFIG_DIR / "review.yaml").read_text(encoding="utf-8"))


def record_defaults() -> dict:
    """`config/record_defaults.yaml`, validated the same way every other config file is.

    Until 2026-09-12 this read the file directly with `yaml.safe_load`, the one call
    site `load_record_defaults` in `monitor/registry/load.py` was written to replace,
    and only `seed()` (`make up`) called that loader — so a typo in `value_basis` or a
    missing sentence key passed a restart of the review service alone and surfaced as
    a raw exception on a reviewer's page instead of failing loudly here (rule 4).
    """
    return load_record_defaults()


def rejection_reasons() -> list[str]:
    """The fixed list on the decision form. Config, not a list in this file (rule 6)."""
    return list(review_config()["rejection_reasons"])


SELECT_CANDIDATE = """
    select c.id, c.title_en, c.buyer, c.country, c.region, c.admin_level, c.score,
           c.summary_en, c.matched_functions, c.system_names, c.procurement_type,
           c.estimated_value, c.value_currency, c.estimated_value_usd, c.value_rate,
           c.value_rate_date, c.eligibility_flags, c.deadline_at, c.status, c.value_note
    from candidates c
    where c.id = %s
"""

SELECT_CLUSTER_SOURCES = """
    select distinct s.id, s.name, s.stream, s.admin_level
    from candidate_notices cn
    join notices n on n.id = cn.notice_id
    join sources s on s.id = n.source_id
    where cn.candidate_id = %s
    order by s.id
"""


def load_candidate(conn: psycopg.Connection, candidate_id: str) -> tuple[RecordCandidate, str]:
    """The candidate as the record builder needs it, and its current status."""
    row = conn.execute(SELECT_CANDIDATE, (candidate_id,)).fetchone()
    if row is None:
        raise DecisionRefused(f"candidate {candidate_id} does not exist")

    matched = row[8] if isinstance(row[8], list) else json.loads(row[8])
    deadline = row[17].date() if row[17] is not None else None

    candidate = RecordCandidate(
        id=row[0],
        title_en=row[1],
        buyer=row[2] or "",
        country=row[3],
        country_name=country_name(row[3]),
        region=row[4],
        admin_level=row[5],
        score=row[6],
        summary_en=row[7],
        matched_function_ids=tuple(entry["function_id"] for entry in matched),
        system_names=tuple(row[9] or ()),
        procurement_type=row[10],
        estimated_value=row[11],
        value_currency=row[12],
        estimated_value_usd=row[13],
        value_rate=row[14],
        value_rate_date=row[15],
        eligibility_flags=tuple(row[16] or ()),
        deadline_at=deadline,
        value_note=row[19] or "",
    )
    return candidate, row[18]


def load_cluster_sources(conn: psycopg.Connection, candidate_id: str) -> list[ClusterSource]:
    return [
        ClusterSource(id=row[0], name=row[1], stream=row[2], admin_level=row[3])
        for row in conn.execute(SELECT_CLUSTER_SOURCES, (candidate_id,)).fetchall()
    ]


def next_record_id(conn: psycopg.Connection) -> str:
    """R000001, from the sequence. It is read aloud and typed, so not a uuid."""
    return f"R{conn.execute('select nextval(%s)', ('approved_record_id_seq',)).fetchone()[0]:06d}"


def write_event(
    conn: psycopg.Connection,
    entity_type: str,
    entity_id: str,
    action: str,
    actor: str,
    before: str,
    after: str,
) -> None:
    conn.execute(
        """
        insert into events (entity_type, entity_id, action, actor, before, after)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (entity_type, entity_id, action, actor, before, after),
    )


def apply_edits(conn: psycopg.Connection, candidate_id: str, record: dict, edits: dict | None, reviewer: str) -> bool:
    """Overlay the reviewer's edits on the built record. Returns whether anything changed.

    One `edited` event per changed key, carrying the value before and after. The
    events hang off the candidate rather than the approved record: the edit is
    something the reviewer did while reviewing, and a person reading the candidate's
    history wants scored, staged, edited, approved in one column.
    """
    if not edits:
        return False

    changed = False
    for key, value in edits.items():
        if key not in record:
            raise DecisionRefused(
                f"{key!r} is not a column in Architecture v0.4 appendix E; "
                "the export's header row is the column spec and cannot gain a column here"
            )
        before = record[key]
        if _same(before, value):
            continue
        record[key] = value
        write_event(conn, "candidate", candidate_id, "edited", reviewer, f"{key}: {before}", f"{key}: {value}")
        changed = True

    return changed


def _same(before, after) -> bool:
    """An edit that retypes the value it was shown is not an edit.

    The form posts every field as a string, so a `Total Opportunity Amount` of
    4200000 comes back as "4200000". Comparing as text keeps that from being
    recorded as a change the reviewer did not make.
    """
    return str(before) == str(after)


def approve(conn: psycopg.Connection, candidate_id: str, reviewer: str, edits: dict | None = None) -> str:
    """Approve one candidate. Returns the approved record's id.

    One transaction: build, edit, insert, transition, two events. A failure anywhere
    leaves the candidate pending_review and `approved_records` untouched.
    """
    reviewer = (reviewer or "").strip()
    if not reviewer:
        raise DecisionRefused("a reviewer name is required: no candidate is approved by nobody")

    with conn.transaction():
        candidate, status = load_candidate(conn, candidate_id)
        if status != "pending_review":
            raise DecisionRefused(f"candidate {candidate_id} is {status}, not pending_review")

        sources = load_cluster_sources(conn, candidate_id)
        record = build_record(
            candidate,
            sources,
            load_function_map(),
            record_defaults(),
            reviewer=reviewer,
            approved_on=datetime.now(UTC).date(),
        )

        edited = apply_edits(conn, candidate_id, record, edits, reviewer)

        record_id = next_record_id(conn)
        record["monitor_candidate_id"] = candidate_id
        conn.execute(
            """
            insert into approved_records (id, candidate_id, record, approved_by, edited)
            values (%s, %s, %s::jsonb, %s, %s)
            """,
            (record_id, candidate_id, json.dumps(record, default=json_safe), reviewer, edited),
        )
        conn.execute(
            """
            update candidates
            set status = 'approved', reviewer = %s, approved_record_id = %s
            where id = %s
            """,
            (reviewer, record_id, candidate_id),
        )
        write_event(conn, "candidate", candidate_id, "approved", reviewer, "pending_review", f"approved as {record_id}")
        write_event(
            conn,
            "approved_record",
            record_id,
            "created",
            POST_APPROVAL_ACTOR.format(reviewer=reviewer),
            "",
            f"from {candidate_id}, {'edited' if edited else 'unedited'}",
        )

    log.info("candidate_approved", candidate_id=candidate_id, record_id=record_id, reviewer=reviewer, edited=edited)
    return record_id


def reject(conn: psycopg.Connection, candidate_id: str, reviewer: str, reason: str) -> None:
    """Reject one candidate. A reason is required and is stored as given.

    The reason is what the week 14 gate reads. A rejected candidate whose reason is
    blank cannot be tuned against afterwards, which is why the check is in the
    database as well (`migrations/008_rejection_reason_guard.sql`).
    """
    reviewer = (reviewer or "").strip()
    reason = (reason or "").strip()
    if not reviewer:
        raise DecisionRefused("a reviewer name is required: no candidate is rejected by nobody")
    if not reason:
        raise DecisionRefused("a rejection reason is required")

    with conn.transaction():
        _, status = load_candidate(conn, candidate_id)
        if status != "pending_review":
            raise DecisionRefused(f"candidate {candidate_id} is {status}, not pending_review")

        conn.execute(
            """
            update candidates
            set status = 'rejected', reviewer = %s, rejection_reason = %s
            where id = %s
            """,
            (reviewer, reason, candidate_id),
        )
        write_event(conn, "candidate", candidate_id, "rejected", reviewer, "pending_review", reason)

    log.info("candidate_rejected", candidate_id=candidate_id, reviewer=reviewer, reason=reason)
