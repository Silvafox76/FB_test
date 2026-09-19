"""The only way anything leaves this system: a CSV file and a manifest beside it (rule 16).

One command (`make export`) and one button (`/export`) produce a batch. Nothing is sent
anywhere. There is no CRM client, no OAuth flow, no webhook and no import path here or
anywhere else in the repository (rules 15 to 18): the file lands in a directory and a
named person imports it into Zoho by hand. Postgres is the system of record up to
approval and never after.

**Why this module may write to `approved_records` at all.** Rule 12 gives
`review/decisions.py` the only path that *creates* an approved record, and that still
holds: nothing here inserts into that table. The grant in `migrations/002_roles.sql`
lets `monitor_review` update exactly two columns, `exported_at` and `export_batch`, and
`record` is not one of them, so the payload a reviewer approved cannot be altered by the
export even by accident. Stamping a record as having left is not deciding anything.

**The header row is the contract with Zoho's import mapper**, so it is read from
`config/record_defaults.yaml` — appendix E's column list, in appendix E's order, the same
file `monitor/stage/record.py` builds against (rule 6). It is not read off a stored
record: jsonb does not preserve key order, so the order Postgres hands back is Postgres's
and not the builder's. `monitor_candidate_id` and `monitor_export_batch` are already the
last two of the 73 columns; nothing appends them a second time.

**The BOM is not decoration.** Excel and Zoho's import mapper read a UTF-8 CSV without
one as the local codepage, and every accented buyer name in the West African set arrives
mangled — "Ministère de l'Économie" as "MinistÃ¨re". `utf-8-sig` is the encoding that
writes it and `tests/review/test_export.py` asserts the first three bytes.

**The order of the writes, which drill 6 exists to kill halfway.** One transaction on one
connection, with exactly one filesystem step inside it, placed so that no kill can leave a
record marked exported for a file nobody has:

  1. select the unexported records in the range;
  2. take the batch id from `export_batch_id_seq`;
  3. build the CSV bytes in memory and hash them;
  4. insert the `export_batches` row — the foreign key on `approved_records.export_batch`
     means the batch must exist before any row can point at it;
  5. write the CSV and the manifest into `.B0001.part/` and rename that directory into
     place, one rename, so the pair appears together or not at all;
  6. stamp `exported_at` and `export_batch` on exactly the rows selected;
  7. one `exported` event per record;
  8. commit.

A kill before step 8 leaves no batch row, no stamp, and nothing the next export can see. A
kill between 5 and 8 also leaves a complete CSV with a matching manifest in a directory no
`export_batches` row names. That orphan is the one residue this design accepts, and it is
the right way round: an orphan file is visible, verifiable against its own sha256 and
discardable, while a stamped row whose file was never written would be invisible and
unrecoverable, because that record would never be selected again. Committing last is what
chooses which of the two can happen.

**A record is exported once.** The selection takes only rows with `exported_at` null and
the stamp repeats that condition and counts: if anything took a row between the select and
the update, the counts disagree and the transaction raises instead of writing a second
batch over the first. Re-exporting a named batch is the separate explicit call `re_export`
below, and it reissues the file that left rather than producing a new one: no new batch id,
no stamp touched, and the rebuilt bytes checked against the sha256 the batch recorded.

**A batch with no rows is still a batch.** A second export of the same range writes a
header-only CSV, a manifest saying nought rows, and an `export_batches` row. One code path
rather than two (rule 1), and "we ran the export on Tuesday and there was nothing new"
becomes a recorded fact rather than something somebody remembers.

**The backlog is one query and this module owns it.** What is approved and not yet exported
is read by `backlog` and by nothing else: the queue page, the export page and `monitor
status` all render the same reading, so the three cannot quietly disagree about how much
work is waiting (rule 1). The age of its oldest record is the CloudWatch metric
`ExportBacklogAgeDays`; `Backlog.age_days` says where the alarm that reads it is declared
and why no threshold is repeated here.

**Where the files go.** `MONITOR_EXPORT_DIR`, or `exports/` beside this repository when it
is unset: the directory `docker-compose.yml` already mounts at `/app/exports` and
`.gitignore` already excludes. A directory is deployment state like the database URLs, not
BD configuration — rule 6 keeps keywords, thresholds and placeholder strings out of .py
files, and a path is none of those.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import structlog

from monitor import db
from review.decisions import record_defaults, write_event

log = structlog.get_logger(__name__)

EXPORT_DIR_ENV = "MONITOR_EXPORT_DIR"
DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent.parent / "exports"


class ExportRefused(Exception):
    """The batch was not produced. The message is what the operator is shown."""


SELECT_UNEXPORTED = """
    select id, candidate_id, record, approved_by
    from approved_records
    where exported_at is null
      and created_at >= %s
      and created_at <  %s
    order by id
"""

SELECT_UNEXPORTED_SUBSET = """
    select id, candidate_id, record, approved_by
    from approved_records
    where exported_at is null
      and created_at >= %s
      and created_at <  %s
      and id = any(%s::text[])
    order by id
"""

INSERT_BATCH = """
    insert into export_batches (batch_id, operator, row_count, range_from, range_to,
                                file_path, manifest_path, sha256)
    values (%s, %s, %s, %s, %s, %s, %s, %s)
    returning created_at
"""

STAMP_EXPORTED = """
    update approved_records
    set exported_at = now(), export_batch = %s
    where id = any(%s::text[]) and exported_at is null
"""


def export_dir() -> Path:
    """The directory batches are written into."""
    return Path(os.environ.get(EXPORT_DIR_ENV) or DEFAULT_EXPORT_DIR)


def batch_paths(directory: Path, batch_id: str) -> tuple[Path, Path]:
    """The CSV and its sibling manifest, in the batch's own directory.

    A directory per batch rather than two files loose in one folder, because the
    rename that publishes a batch has to publish both files at once: a CSV whose
    manifest is missing is exactly the half-written state drill 6 refuses.
    """
    folder = directory / batch_id
    return folder / f"{batch_id}.csv", folder / f"{batch_id}.manifest.json"


def columns() -> list[str]:
    """Appendix E's column names, in appendix E's order, from config (rule 6)."""
    return [column["name"] for column in record_defaults()["columns"]]


def cell(record: dict, name: str, record_id: str) -> str:
    """One CSV cell from the stored record. Absent or of the wrong type raises (rule 4).

    Dates are already ISO 8601 strings and amounts already bare numbers, both written
    that way by `monitor/stage/record.py`; nothing is reformatted here, because a second
    place that decides how a value looks is a second spec.
    """
    if name not in record:
        raise ExportRefused(
            f"{record_id} has no {name!r}: the approved record and appendix E's column list disagree, "
            "and the header row is the import mapper's contract"
        )
    value = record[name]
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    raise ExportRefused(f"{record_id}'s {name!r} is a {type(value).__name__}; a CSV cell is text or a bare number")


def csv_bytes(rows: list[tuple], batch_id: str) -> bytes:
    """The whole file in memory, so the sha256 is of the bytes that reach the disk.

    `monitor_export_batch` is stamped into the row here and not into the stored record:
    the approved payload is immutable after the decision, and the grant would refuse the
    update anyway. The candidate id is checked against the table's own column, because
    tracing an imported record back to its notice and its audit trail is the reason that
    column exists.
    """
    names = columns()
    buffer = io.StringIO()
    writer = csv.writer(buffer)  # RFC 4180 as Zoho and Excel read it: quoted where needed, CRLF rows
    writer.writerow(names)

    for record_id, candidate_id, record, _approved_by in rows:
        if record.get("monitor_candidate_id") != candidate_id:
            raise ExportRefused(
                f"{record_id} is approved for {candidate_id} but its payload names "
                f"{record.get('monitor_candidate_id')!r}"
            )
        stamped = record | {"monitor_export_batch": batch_id}
        writer.writerow([cell(stamped, name, record_id) for name in names])

    return buffer.getvalue().encode("utf-8-sig")


def manifest_for(
    batch_id: str,
    created_at: datetime,
    operator: str,
    rows: list[tuple],
    range_from: datetime,
    range_to: datetime,
    sha256: str,
) -> dict:
    """The receipt for one batch: what left, when, produced by whom, and the file's hash.

    `range_to` is the exclusive end instant the batch row stores, not the last day the
    operator typed; `day_range` explains the conversion. The reviewer names are the people
    who approved the records in the batch, which is not the operator who produced it.
    """
    return {
        "batch_id": batch_id,
        "created_at": created_at.isoformat(),
        "operator": operator,
        "row_count": len(rows),
        "range_from": range_from.isoformat(),
        "range_to": range_to.isoformat(),
        "reviewers": sorted({row[3] for row in rows}),
        "csv": f"{batch_id}.csv",
        "sha256": sha256,
    }


def write_fsynced(path: Path, payload: bytes) -> None:
    """Written and flushed to the device before the rename that publishes it."""
    with open(path, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def write_batch_files(directory: Path, batch_id: str, data: bytes, manifest: dict) -> None:
    """Both files into a staging directory, then one rename into place.

    `mkdir` on the staging directory does not pass `exist_ok`: a leftover `.B0001.part`
    is the wreckage of an earlier attempt at this same batch id, and a person should look
    at it rather than have it silently overwritten.
    """
    csv_path, manifest_path = batch_paths(directory, batch_id)
    staging = directory / f".{batch_id}.part"
    staging.mkdir(parents=True)

    write_fsynced(staging / csv_path.name, data)
    write_fsynced(staging / manifest_path.name, json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))

    os.rename(staging, csv_path.parent)


def next_batch_id(conn: psycopg.Connection) -> str:
    """B0001, from the sequence. Four digits because `export_batches_id_format` says so.

    A rolled-back export keeps its number, since a sequence is not rolled back, so gaps in
    the series are expected and each one is an export that did not finish.
    """
    return f"B{conn.execute('select nextval(%s)', ('export_batch_id_seq',)).fetchone()[0]:04d}"


def export(
    range_from: datetime,
    range_to: datetime,
    operator: str,
    record_ids: list[str] | None = None,
) -> str:
    """Produce one batch from the approved records not yet exported. Returns its batch id.

    Connects as `monitor_review` through `db.connect` and as nothing else (rule 11). The
    range is on `approved_records.created_at`, the moment the reviewer approved, and it is
    half open: `range_from` inclusive, `range_to` exclusive.

    **`record_ids` is the reconfirmation step (decision 69).** The export page now lists every
    waiting record with its tag and the operator ticks or unticks it, so the batch is exactly
    the ids posted rather than everything the range would otherwise match. `None` is the old
    behaviour and every caller that never mentions this parameter is unaffected. An id that is
    not waiting in this range — already exported, approved outside it, or not a real record —
    is refused by name inside the same transaction that would have selected it, never silently
    dropped from the batch: a smaller batch than the operator asked for is a batch nobody
    reconfirmed. An empty list is refused too, because "select nothing" is not a batch.
    """
    operator = (operator or "").strip()
    if not operator:
        raise ExportRefused("an operator name is required: a batch records the person who produced it")
    if range_to <= range_from:
        raise ExportRefused(f"the range ends before it starts: {range_from.isoformat()} to {range_to.isoformat()}")
    if record_ids is not None and not record_ids:
        raise ExportRefused("nothing selected: at least one waiting record must be included in the batch")

    directory = export_dir()

    with db.connect("review") as conn, conn.transaction():
        if record_ids is None:
            rows = conn.execute(SELECT_UNEXPORTED, (range_from, range_to)).fetchall()
        else:
            rows = conn.execute(SELECT_UNEXPORTED_SUBSET, (range_from, range_to, record_ids)).fetchall()
            found = {row[0] for row in rows}
            missing = [record_id for record_id in record_ids if record_id not in found]
            if missing:
                raise ExportRefused(
                    f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not waiting in this range "
                    "(already exported, approved outside it, or not a real record); nothing was exported"
                )

        batch_id = next_batch_id(conn)
        data = csv_bytes(rows, batch_id)
        sha256 = hashlib.sha256(data).hexdigest()
        csv_path, manifest_path = batch_paths(directory, batch_id)

        created_at = conn.execute(
            INSERT_BATCH,
            (batch_id, operator, len(rows), range_from, range_to, str(csv_path), str(manifest_path), sha256),
        ).fetchone()[0]

        write_batch_files(
            directory,
            batch_id,
            data,
            manifest_for(batch_id, created_at, operator, rows, range_from, range_to, sha256),
        )

        stamped = conn.execute(STAMP_EXPORTED, (batch_id, [row[0] for row in rows])).rowcount
        if stamped != len(rows):
            raise ExportRefused(
                f"{batch_id}: {len(rows)} records were selected and {stamped} could be stamped, so another "
                "export holds some of them. The batch is rolled back and its file names a batch that does not exist."
            )

        for record_id, _candidate_id, _record, _approved_by in rows:
            write_event(conn, "approved_record", record_id, "exported", operator, "", f"{batch_id}: {csv_path.name}")

    log.info(
        "export_batch_written",
        batch_id=batch_id,
        row_count=len(rows),
        operator=operator,
        sha256=sha256,
        csv_path=str(csv_path),
        manifest_path=str(manifest_path),
    )
    return batch_id


def day_start(text: str, label: str) -> datetime:
    """One typed date as an instant. UTC, because every timestamp in this database is."""
    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        # Converted, not swallowed: the operator is told which of the two fields is wrong
        # instead of reading a traceback off a form post.
        raise ExportRefused(f"the {label} date is not a date: {text!r}; use YYYY-MM-DD") from None
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def day_range(range_from: str, range_to: str) -> tuple[datetime, datetime]:
    """The two dates an operator typed, as the half-open instant range the batch stores.

    Both are inclusive days as a person means them: a batch "from the 1st to the 12th"
    contains everything approved on the 12th, so the stored `range_to` is midnight at the
    start of the 13th. One conversion, used by both the CLI and the page, so the button
    and the command cannot disagree about what a day is.
    """
    return day_start(range_from, "from"), day_start(range_to, "to") + timedelta(days=1)


# --- the backlog: approved and not yet exported ---------------------------------

SELECT_BACKLOG = """
    select id, candidate_id, approved_by, created_at, review_tag
    from approved_records
    where exported_at is null
    order by created_at, id
"""

READONLY_URL_ENV = "DATABASE_URL_READONLY"


@dataclass(frozen=True)
class Waiting:
    """One approved record that has not left yet.

    `review_tag` defaults to "" so every existing construction of this dataclass — the pure
    arithmetic tests that build a `Backlog` by hand — keeps working unchanged; `backlog()`
    below always supplies it from the row.
    """

    record_id: str
    candidate_id: str
    approved_by: str
    approved_at: datetime
    review_tag: str = ""


@dataclass(frozen=True)
class Backlog:
    """Everything approved and not yet exported, oldest approval first, at one reading of the clock.

    `as_of` is the database's `now()` and not the host's, read in the same transaction as
    the rows, so the age below is the age of these records and not of a list taken a moment
    earlier against a clock that may not agree.
    """

    records: tuple[Waiting, ...]
    as_of: datetime

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def oldest(self) -> Waiting | None:
        """The record that has waited longest, or None when nothing is waiting.

        None rather than a sentinel: an empty backlog has no oldest record, and the two
        callers both have something different to say about that case.
        """
        return self.records[0] if self.records else None

    @property
    def age_days(self) -> float:
        """How long the oldest record has waited. Nought when nothing is waiting.

        **This is `ExportBacklogAgeDays`**, the metric the `${project}-export-backlog-age`
        alarm in `infra/terraform/observability.tf` is declared against: custom namespace
        `var.metric_namespace`, statistic Maximum, alarming above
        `var.alarm_thresholds.export_backlog_days`. Nothing publishes it yet. When
        `monitor/health/metrics.py` is written it should call PutMetricData with this
        number, read on a `monitor_readonly` connection, and no threshold belongs on this
        side: the comparison is the alarm's and lives in the Terraform, so a second copy
        of seven in a .py file would be a second place to change it (rule 6).
        """
        if not self.records:
            return 0.0
        return (self.as_of - self.records[0].approved_at).total_seconds() / 86400


def backlog(conn: psycopg.Connection) -> Backlog:
    """What is waiting to leave, oldest first. Reads; writes nothing."""
    as_of = conn.execute("select now()").fetchone()[0]
    return Backlog(
        records=tuple(Waiting(*row) for row in conn.execute(SELECT_BACKLOG).fetchall()),
        as_of=as_of,
    )


def render_backlog(waiting: Backlog) -> str:
    """The export backlog as `monitor status` prints it. Plain text, read in a terminal."""
    if waiting.oldest is None:
        return "export backlog: nothing waiting, every approved record has been exported (ExportBacklogAgeDays 0.0)"

    oldest = waiting.oldest
    return (
        f"export backlog: {waiting.count} approved record{'' if waiting.count == 1 else 's'} waiting, "
        f"oldest {oldest.record_id} approved {waiting.age_days:.1f} days ago "
        f"({oldest.approved_at:%Y-%m-%d}, {oldest.approved_by})\n"
        f"                ExportBacklogAgeDays {waiting.age_days:.1f} — the number the export-backlog-age "
        "alarm in infra/terraform/observability.tf reads, which nothing publishes yet"
    )


def reporting_connection() -> psycopg.Connection:
    """A `monitor_readonly` connection, for reading the backlog from outside the review app.

    `monitor status` runs as the pipeline, and `migrations/002_roles.sql` revokes all on
    `approved_records` from `monitor_pipeline`, so the backlog cannot be read on that
    command's own connection — and connecting the pipeline as `monitor_review` to get at it
    is precisely what rule 11 calls blocking. `monitor_readonly` is the role that exists for
    reporting: select on everything, and nothing else, ever.

    It is opened here rather than through `db.connect` because that factory offers the two
    runtime roles and says in its own docstring that reporting does not come through it. If
    a third role is added there this function becomes `db.connect("readonly")` and nothing
    else changes.
    """
    url = os.environ.get(READONLY_URL_ENV)
    if not url:
        raise RuntimeError(
            f"{READONLY_URL_ENV} is not set; copy .env.example to .env and fill it in. "
            "The export backlog lives in approved_records, which the pipeline role cannot read."
        )
    return psycopg.connect(url)


# --- the batches that have been produced, and reissuing one of them --------------

SELECT_BATCH = """
    select batch_id, created_at, operator, row_count, range_from, range_to,
           file_path, manifest_path, sha256
    from export_batches
    where batch_id = %s
"""

SELECT_BATCHES = """
    select batch_id, created_at, operator, row_count, range_from, range_to,
           file_path, manifest_path, sha256
    from export_batches
    order by batch_id desc
    limit %s
"""

SELECT_BATCH_RECORDS = """
    select id, candidate_id, record, approved_by
    from approved_records
    where export_batch = %s
    order by id
"""

# The listing's ceiling. The pilot produces roughly one batch a week for fourteen
# weeks, so this is every batch it will ever have and then some; it is here so the
# page has a bound at all rather than because fifty is a meaningful number.
BATCH_LIST_LIMIT = 50


@dataclass(frozen=True)
class Batch:
    """One row of `export_batches`: a batch that was produced, and where its file went."""

    batch_id: str
    created_at: datetime
    operator: str
    row_count: int
    range_from: datetime
    range_to: datetime
    file_path: str
    manifest_path: str
    sha256: str

    @property
    def on_disk(self) -> bool:
        """Whether the CSV this row names is still where it was written.

        The batch row is the fact and the file is what can go missing — deleted, moved, or
        on a host that was replaced — so this is checked against the recorded path rather
        than against the current `MONITOR_EXPORT_DIR`. It is what the operator needs before
        deciding whether a re-export is a reissue or a no-op.
        """
        return Path(self.file_path).exists()


def load_batch(conn: psycopg.Connection, batch_id: str) -> Batch | None:
    """One batch by id, or None when there is no such batch."""
    row = conn.execute(SELECT_BATCH, (batch_id,)).fetchone()
    return Batch(*row) if row else None


def list_batches(conn: psycopg.Connection, limit: int = BATCH_LIST_LIMIT) -> list[Batch]:
    """Every batch produced, newest first. Batch ids are issued in order, so the id sorts."""
    return [Batch(*row) for row in conn.execute(SELECT_BATCHES, (limit,)).fetchall()]


@dataclass(frozen=True)
class ReExport:
    """What a re-export did. `rewritten` is False when the file was already in place."""

    batch_id: str
    row_count: int
    sha256: str
    csv_path: Path
    manifest_path: Path
    rewritten: bool


def batch_directory(batch: Batch) -> Path:
    """The export directory the batch row names, checked against this module's own layout.

    Taken from the recorded `file_path` and not from `MONITOR_EXPORT_DIR`, because
    `monitor_review` has insert and no update on `export_batches`: the row cannot be
    corrected, so the file has to go back where the row says it went. A row whose path is
    not `<directory>/<batch_id>/<batch_id>.csv` was written by something other than this
    module and is not reissued by guessing.
    """
    recorded = Path(batch.file_path)
    directory = recorded.parent.parent
    expected, _ = batch_paths(directory, batch.batch_id)
    if expected != recorded:
        raise ExportRefused(
            f"{batch.batch_id} records its CSV at {recorded}, which is not the "
            f"{expected.parent.name}/{expected.name} layout this exporter writes; "
            "reissue it by hand from the manifest rather than from here"
        )
    return directory


def re_export(batch_id: str, operator: str) -> ReExport:
    """Write a batch that has already left out to disk again. Returns what it did.

    This is the separate explicit call step 11 left for later, and what it must not do is
    the whole design. A batch id is a record's statement of which file it left in, so:

      - no new batch id is issued and no `export_batches` row is inserted. A second row for
        the same records would make two batches claim them and the first file's provenance
        would be gone;
      - no stamp is written, cleared or refreshed. `exported_at` is when the record left,
        which already happened, and `export_batch` still names the file this call reissues.
        Clearing either to "export it again" would put a record that BD has already imported
        back in the backlog as if it were new;
      - the file's bytes are rebuilt and checked against the sha256 the manifest recorded. A
        re-export reissues the file that left; it never produces a different one under the
        same name. If the column list in `config/record_defaults.yaml` has moved since, the
        rebuild does not match and this refuses rather than quietly reissuing a batch whose
        header is not the header BD mapped;
      - a published batch directory is never overwritten. Either the bytes on disk are the
        batch, in which case there is nothing to do, or they are not, in which case a person
        looks at them.

    So the only thing this writes is the pair of files, and only when they are missing, plus
    one `re_exported` event naming who asked. The operator on the manifest stays the person
    who produced the batch; the person reissuing it is in the event and not in the file,
    because the file is a copy of what left and not a new statement.
    """
    operator = (operator or "").strip()
    if not operator:
        raise ExportRefused("an operator name is required: a re-export is recorded against the person who asked")

    with db.connect("review") as conn, conn.transaction():
        batch = load_batch(conn, batch_id)
        if batch is None:
            raise ExportRefused(f"there is no batch {batch_id!r}; a batch id is issued by an export, not chosen")

        rows = conn.execute(SELECT_BATCH_RECORDS, (batch.batch_id,)).fetchall()
        if len(rows) != batch.row_count:
            raise ExportRefused(
                f"{batch.batch_id} was written with {batch.row_count} record(s) and {len(rows)} are stamped "
                "with it now; the batch row and the table disagree, so nothing is reissued"
            )

        data = csv_bytes(rows, batch.batch_id)
        sha256 = hashlib.sha256(data).hexdigest()
        if sha256 != batch.sha256:
            raise ExportRefused(
                f"{batch.batch_id} rebuilds to a different file: the batch recorded {batch.sha256} and these "
                f"records render as {sha256}. The column list in config/record_defaults.yaml has changed since "
                "the batch was produced. A re-export reissues the file that left, so this one is refused; "
                "export the affected records in a new batch instead."
            )

        directory = batch_directory(batch)
        csv_path, manifest_path = batch_paths(directory, batch.batch_id)
        published = csv_path.parent

        if not published.exists():
            write_batch_files(
                directory,
                batch.batch_id,
                data,
                manifest_for(
                    batch.batch_id, batch.created_at, batch.operator, rows, batch.range_from, batch.range_to, sha256
                ),
            )
            rewritten = True
        elif csv_path.exists() and manifest_path.exists() and csv_path.read_bytes() == data:
            rewritten = False
        else:
            raise ExportRefused(
                f"{published} already exists and is not {batch.batch_id} as it left: the CSV is missing, its "
                "manifest is missing, or its bytes are not the ones the batch recorded. Move the directory aside "
                "and ask for the re-export again; a published batch is never overwritten in place."
            )

        write_event(
            conn,
            "export_batch",
            batch.batch_id,
            "re_exported",
            operator,
            "",
            f"{batch.batch_id}: {'rewritten' if rewritten else 'verified in place'}, {len(rows)} row(s), "
            f"sha256 {sha256}",
        )

    log.info(
        "export_batch_reissued",
        batch_id=batch.batch_id,
        row_count=len(rows),
        operator=operator,
        sha256=sha256,
        rewritten=rewritten,
        csv_path=str(csv_path),
    )
    return ReExport(
        batch_id=batch.batch_id,
        row_count=len(rows),
        sha256=sha256,
        csv_path=csv_path,
        manifest_path=manifest_path,
        rewritten=rewritten,
    )


# --- the column specification, generated so it cannot drift ----------------------

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "export_spec.md"

# Appendix E's three categories, in the order the appendix lists them. The wording is
# the appendix's, held here because this is the document generator and nothing reads
# it as configuration; the categories themselves come from record_defaults.yaml, and a
# column carrying one that is not in this table raises rather than being left out of
# the legend (rule 4).
CATEGORY_MEANINGS = {
    "D": "Derived from the notice or candidate, written with confidence",
    "S": "A heuristic suggestion; the reviewer sees it in the record preview and edits it before approving",
    "B": "BD judgement only: the exporter writes the placeholder the CRM already shows for an unfilled field, "
    "and never guesses",
}


def spec_markdown() -> str:
    """`docs/export_spec.md`, rendered from `config/record_defaults.yaml`.

    Generated rather than written because a hand-kept column list is a list that drifts:
    the header row is the contract with Zoho's import mapper, and a document that says the
    export has 73 columns while the config says 74 is worse than no document. The test in
    `tests/review/test_export.py` renders this and compares it with the file, so a column
    added to the config and not regenerated here is a failing test.

    **Nothing dated or hashed goes in the page.** A "generated on" line would differ from
    the file on disk the day after it was written and the test would fail for a reason that
    is not a drift, which would train someone to regenerate without reading the diff.
    """
    defaults = record_defaults()
    spec = defaults["columns"]

    unknown = sorted({column["category"] for column in spec} - set(CATEGORY_MEANINGS))
    if unknown:
        raise ExportRefused(
            f"config/record_defaults.yaml uses categor{'y' if len(unknown) == 1 else 'ies'} {unknown}, which "
            "appendix E does not define; add it to CATEGORY_MEANINGS in review/export.py with its meaning"
        )

    counts = {category: sum(1 for column in spec if column["category"] == category) for category in CATEGORY_MEANINGS}
    legend = "\n".join(
        f"| {category} | {meaning} | {counts[category]} |" for category, meaning in CATEGORY_MEANINGS.items()
    )
    table = "\n".join(
        f"| {position} | {column['name']} | {column['category']} |" for position, column in enumerate(spec, start=1)
    )

    return f"""# Export column specification

**Generated from `config/record_defaults.yaml`. Do not edit by hand.**
`review.export.spec_markdown` renders it, `scripts/generate_export_spec.py` writes it, and
`tests/review/test_export.py` fails when this file and the config disagree. That is the only reason
this document is generated: a column added to the config and not to this page would otherwise be a
column BD reads about nowhere, and the header row is the contract.

Regenerate with:

    uv run python scripts/generate_export_spec.py

## What this specifies

Architecture v0.4 appendix E. `monitor/stage/record.py` builds every approved record with exactly
these keys and `review/export.py` writes them as the CSV header row in exactly this order, because
Zoho's import mapper matches on headers: the order and the spelling are the contract, and nothing
else invents a field.

- One directory per batch under the export directory: `B0001/B0001.csv` beside
  `B0001/B0001.manifest.json`, published by a single rename so the pair appears together or not at
  all. The manifest carries the row count, the approval range, the reviewers who approved the
  records and the sha256 of the CSV as written.
- UTF-8 **with a byte order mark**. Without it Excel and Zoho's import mapper read the file as the
  local codepage and an accented buyer name — "Ministère de l'Économie" — arrives mangled.
- RFC 4180 as those two read it: quoted where needed, CRLF rows, one header row.
- Every column is present in every row. A record missing one is refused at export rather than
  exported with a blank, because a blank column is indistinguishable from a field nobody filled in.
- The export is one way and it is a file (D31, rules 15 to 18). Nothing here talks to a CRM, nothing
  is sent anywhere, there is no import path back into this database, and a named person imports the
  file by hand. Account resolution happens at import, using Zoho's own matching; the exporter
  proposes the buyer name as text and nothing more.

## Categories

| Category | Meaning | Columns |
| --- | --- | --- |
{legend}

{len(spec)} columns in total.

## Columns

| # | Column | Category |
| --- | --- | --- |
{table}
"""


def main() -> int:
    """`make export OPERATOR='Name' FROM=YYYY-MM-DD TO=YYYY-MM-DD`.

    All three are required. A default window would silently decide which approved records
    are not in this batch, and the range is on the batch row precisely because it is the
    operator's statement of what they meant to export.
    """
    parser = argparse.ArgumentParser(
        prog="review.export",
        description="Produce one CSV batch and its manifest from approved records not yet exported.",
    )
    parser.add_argument("--from", dest="range_from", required=True, help="first approval day, YYYY-MM-DD, inclusive")
    parser.add_argument("--to", dest="range_to", required=True, help="last approval day, YYYY-MM-DD, inclusive")
    parser.add_argument("--operator", required=True, help="the person producing the batch; it goes on the batch row")
    args = parser.parse_args()

    range_from, range_to = day_range(args.range_from, args.range_to)
    batch_id = export(range_from, range_to, args.operator)

    csv_path, manifest_path = batch_paths(export_dir(), batch_id)
    print(manifest_path.read_text(encoding="utf-8"))
    print(f"{batch_id}: {csv_path}")
    print("Import it into Zoho by hand. Nothing here sends it anywhere, and nothing reads a CRM back.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
