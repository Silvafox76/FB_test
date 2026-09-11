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
batch over the first. Re-exporting a named batch is a separate explicit call and it is
step 22's work, not this step's; until then the batch stays on disk and the manifest's
sha256 is how a re-read is checked.

**A batch with no rows is still a batch.** A second export of the same range writes a
header-only CSV, a manifest saying nought rows, and an `export_batches` row. One code path
rather than two (rule 1), and "we ran the export on Tuesday and there was nothing new"
becomes a recorded fact rather than something somebody remembers.

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


def export(range_from: datetime, range_to: datetime, operator: str) -> str:
    """Produce one batch from the approved records not yet exported. Returns its batch id.

    Connects as `monitor_review` through `db.connect` and as nothing else (rule 11). The
    range is on `approved_records.created_at`, the moment the reviewer approved, and it is
    half open: `range_from` inclusive, `range_to` exclusive.
    """
    operator = (operator or "").strip()
    if not operator:
        raise ExportRefused("an operator name is required: a batch records the person who produced it")
    if range_to <= range_from:
        raise ExportRefused(f"the range ends before it starts: {range_from.isoformat()} to {range_to.isoformat()}")

    directory = export_dir()

    with db.connect("review") as conn, conn.transaction():
        rows = conn.execute(SELECT_UNEXPORTED, (range_from, range_to)).fetchall()
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
