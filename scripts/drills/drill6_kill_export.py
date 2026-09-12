"""Drill 6: the export is killed halfway. Either a complete pair of files or none, and no
record left marked exported.

    uv run python scripts/drills/drill6_kill_export.py

**What it proves.** The ordering `review/export.py` chose and explains in its own docstring:
the files are written inside the transaction and the stamp and the commit come after, so a
kill can leave a complete CSV with a matching manifest that no `export_batches` row names -
an orphan, visible and verifiable against its own sha256 - but it can never leave a record
marked exported for a file nobody has. That second state would be unrecoverable, because
the record would never be selected again. Rule 16 as well: the file is the only thing that
leaves, and a half-left file is a half-imported batch in somebody's CRM.

**How the kill is aimed, rather than timed.** A drill that killed the process after a
guessed interval would hit the interesting window rarely and prove nothing on the other
runs. So the window is held open: the drill takes `select ... for update` on the two
approved records the batch will stamp and holds it. The export then runs its select, takes
its batch id, builds and hashes the CSV, inserts the `export_batches` row, writes both
files and renames them into place - and blocks on the very next statement, the stamp. The
drill waits until `pg_locks` shows that backend waiting for a lock, which is the evidence
that it is at the stamp and not somewhere else, then sends SIGKILL, then releases the lock.
Nothing is monkeypatched and no code is changed for the drill's benefit: the process is
killed at the point in the sequence the runbook makes a claim about.

`pg_locks` is readable by any role, while another role's query text is not (checked on this
database: `monitor_owner` sees `<insufficient privilege>` for a `monitor_review` query), so
"a monitor_review backend that appeared after we launched the export and is waiting for a
lock" is both the strongest evidence available and the evidence a person can reproduce with
one psql query.

**Then it runs the export again, to completion.** "No row left marked exported" matters
only because those records are still exportable afterwards, so the drill exports them for
real, checks the batch has both rows, and checks its batch id is not the killed one: the
sequence does not roll back, so the killed attempt's number is a permanent gap, which is
what `review/export.py:next_batch_id` says to expect.

**Why the fixture's approvals are dated 2000-01-01.** The export's range is on
`approved_records.created_at`, and a drill that exported "today" would sweep up any record
a reviewer had approved today and mark it exported in a batch the drill then deletes. The
two fixture records are moved to a day no pilot record can be on, so the batch can only
ever contain them, and the drill refuses to run if anything else is in that window.

**What it changes.** Two candidates, their two approved records, up to two
`export_batches` rows and a temporary directory. All of it is removed on the way out,
whether the drill passed or failed, and the drill prints what it removed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _drill import Drill, DrillCannotRun, connection, owner, run, staged_candidates

from review.decisions import approve
from review.export import EXPORT_DIR_ENV, batch_paths, day_range

REPO = Path(__file__).resolve().parents[2]

# The day the fixture's approvals are moved to, so the batch can only contain them.
ISOLATION_DAY = "2000-01-01"
OPERATOR = "Drill Six Operator"
REVIEWERS = ("Drill Six Reviewer", "Drill Six Second Reviewer")

BLOCK_TIMEOUT_SECONDS = 30.0
EXIT_TIMEOUT_SECONDS = 15.0
POLL_SECONDS = 0.2

REVIEW_BACKENDS = """
    select pid from pg_stat_activity
    where datname = current_database() and usename = 'monitor_review' and pid <> pg_backend_pid()
"""
BLOCKED_BACKENDS = """
    select l.pid, l.locktype, l.mode
    from pg_locks l
    join pg_stat_activity a on a.pid = l.pid
    where a.usename = 'monitor_review' and not l.granted
"""
HELD_RELATIONS = """
    select distinct l.relation::regclass::text, l.mode
    from pg_locks l
    where l.pid = %s and l.relation is not null and l.granted
    order by 1
"""
UNEXPORTED_IN_WINDOW = """
    select count(*) from approved_records
    where exported_at is null and created_at >= %s and created_at < %s
"""
STAMPS = """
    select id, export_batch, exported_at from approved_records
    where id = any(%s::text[]) order by id
"""


def published_batches(directory: Path) -> list[str]:
    """The batch directories on disk, as a person looking in the export folder sees them."""
    return sorted(path.name for path in directory.iterdir() if path.is_dir() and not path.name.startswith("."))


def staging_leftovers(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir() if path.name.startswith("."))


def pair_is_complete(directory: Path, batch_id: str) -> tuple[bool, str]:
    """Is the batch on disk a CSV and a manifest that agree with each other?

    The manifest's sha256 is checked against the bytes of the file beside it, which is the
    check a person would run on a batch they found and did not trust.
    """
    csv_path, manifest_path = batch_paths(directory, batch_id)
    if not csv_path.exists() or not manifest_path.exists():
        return False, f"csv {csv_path.exists()}, manifest {manifest_path.exists()}"

    data = csv_path.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    digest = hashlib.sha256(data).hexdigest()

    agrees = digest == manifest["sha256"] and manifest["row_count"] == len(rows) - 1
    return agrees, (
        f"{len(rows) - 1} data rows, manifest says {manifest['row_count']}, "
        f"sha256 {'matches' if digest == manifest['sha256'] else 'DOES NOT match'} the file"
    )


def wait_for_blocked_export(watcher, known: set[int]) -> tuple[int, str]:
    """Wait until a new monitor_review backend is waiting for a lock. Returns its pid."""
    deadline = time.monotonic() + BLOCK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        for pid, locktype, mode in watcher.execute(BLOCKED_BACKENDS).fetchall():
            if pid not in known:
                return pid, f"backend {pid} waiting for a {mode} on a {locktype}"
        time.sleep(POLL_SECONDS)
    return 0, f"no monitor_review backend blocked within {BLOCK_TIMEOUT_SECONDS:.0f}s"


def wait_for_backend_to_go(watcher, pid: int) -> bool:
    """Wait until the killed export's backend has gone, so its transaction is over."""
    deadline = time.monotonic() + EXIT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if pid not in {row[0] for row in watcher.execute(REVIEW_BACKENDS).fetchall()}:
            return True
        time.sleep(POLL_SECONDS)
    return False


def export_command() -> list[str]:
    """The command both halves of the drill run: the one that gets killed and the one that
    finishes. One definition, so the second run cannot differ from the first."""
    return [
        sys.executable,
        "-m",
        "review.export",
        "--from",
        ISOLATION_DAY,
        "--to",
        ISOLATION_DAY,
        "--operator",
        OPERATOR,
    ]


def export_environment(directory: Path) -> dict[str, str]:
    return os.environ | {EXPORT_DIR_ENV: str(directory)}


def export_process(directory: Path) -> subprocess.Popen:
    """`python -m review.export` for the isolation day, writing into the drill's directory."""
    return subprocess.Popen(
        export_command(),
        cwd=REPO,
        env=export_environment(directory),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    drill = Drill(
        6,
        "an export killed between its files and its commit leaves an orphan, never a stamped row",
        "the write order in review/export.py, and rule 16: the file is the only thing that leaves",
        "a complete CSV and manifest with no batch row, no stamped record, no .part left; then a clean re-export",
    )

    directory = Path(tempfile.mkdtemp(prefix="drill6-"))
    range_from, range_to = day_range(ISOLATION_DAY, ISOLATION_DAY)

    with owner() as owner_conn, staged_candidates(owner_conn, 2) as fixture:
        try:
            with connection("DATABASE_URL_REVIEW") as review:
                record_ids = [
                    approve(review, candidate_id, reviewer)
                    for candidate_id, reviewer in zip(fixture.candidate_ids, REVIEWERS, strict=True)
                ]

            owner_conn.execute(
                "update approved_records set created_at = %s where id = any(%s::text[])",
                (range_from, record_ids),
            )
            drill.note(f"approved {', '.join(record_ids)} and dated them {ISOLATION_DAY} so only they are in range")
            drill.note(f"batches will be written to {directory}")

            with connection("DATABASE_URL_READONLY") as reader:
                in_window = reader.execute(UNEXPORTED_IN_WINDOW, (range_from, range_to)).fetchone()[0]
            if in_window != len(record_ids):
                raise DrillCannotRun(
                    f"{in_window} unexported approved records are in the {ISOLATION_DAY} window and the drill "
                    f"made {len(record_ids)} of them. Something earlier left records in that window; clear them "
                    "before running this drill, or the batch it kills would contain records that are not its."
                )

            # The watcher reads pg_locks and pg_stat_activity; the holder holds the row lock
            # that the export will block on. Two connections because the holder's transaction
            # stays open while the watcher keeps answering questions.
            with (
                connection("DATABASE_URL_READONLY", autocommit=True) as watcher,
                connection("DATABASE_URL_OWNER") as holder,
            ):
                known = {row[0] for row in watcher.execute(REVIEW_BACKENDS).fetchall()}

                # Not autocommit: psycopg opens the transaction on this statement and the
                # row lock is held until the rollback below.
                holder.execute("select id from approved_records where id = any(%s::text[]) for update", (record_ids,))

                process = export_process(directory)
                pid, how = wait_for_blocked_export(watcher, known)
                blocked = drill.check("the export reached the stamp and blocked there", bool(pid), how)

                if blocked:
                    held = watcher.execute(HELD_RELATIONS, (pid,)).fetchall()
                    drill.note(
                        "locks that backend already holds: "
                        + ", ".join(f"{relation} {mode}" for relation, mode in held)
                    )
                    on_disk = published_batches(directory)
                    drill.check(
                        "its files are already on disk, so the writes happen before the stamp",
                        len(on_disk) == 1,
                        f"export directory holds {on_disk or 'nothing'}",
                    )

                    process.kill()
                    output = process.communicate(timeout=EXIT_TIMEOUT_SECONDS)[0]
                    drill.note(f"SIGKILL sent; the process left with status {process.returncode}")
                else:
                    process.kill()
                    output = process.communicate(timeout=EXIT_TIMEOUT_SECONDS)[0]

                holder.rollback()
                drill.check(
                    "the killed backend is gone, so its transaction is over",
                    wait_for_backend_to_go(watcher, pid) if pid else False,
                    f"backend {pid} no longer connected" if pid else "no backend to wait for",
                )

            if output.strip():
                print("\n  what the killed export had said before it died:")
                drill.show(output.strip())

            killed = published_batches(directory)
            killed_id = killed[0] if len(killed) == 1 else ""
            fixture.batch_ids.update(killed)

            with connection("DATABASE_URL_READONLY") as reader:
                batch_rows = reader.execute(
                    "select count(*) from export_batches where batch_id = any(%s::text[])", (killed or [""],)
                ).fetchone()[0]
                stamped = reader.execute(STAMPS, (record_ids,)).fetchall()

            complete, how_complete = (
                pair_is_complete(directory, killed_id) if killed_id else (False, "no batch on disk")
            )
            drill.check(
                "the documented outcome: a complete CSV with a matching manifest, or no file at all",
                complete or not killed_id,
                f"{killed_id or 'nothing'} on disk: {how_complete}",
            )
            drill.check(
                "no batch row: the transaction that would have named that file never committed",
                batch_rows == 0,
                f"export_batches rows for {killed_id or 'the killed batch'}: {batch_rows}",
            )
            drill.check(
                "no record is marked exported, which is the state that could not be recovered from",
                all(batch is None and at is None for _id, batch, at in stamped),
                "; ".join(f"{record} batch={batch} exported_at={at}" for record, batch, at in stamped),
            )
            drill.check(
                "no half-written staging directory was left behind",
                staging_leftovers(directory) == [],
                f"leftovers {staging_leftovers(directory) or 'none'}",
            )

            # The records are still exportable, which is the whole point of the claim above.
            finished = subprocess.run(
                export_command(),
                cwd=REPO,
                env=export_environment(directory),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            with connection("DATABASE_URL_READONLY") as reader:
                live = reader.execute(
                    "select batch_id, row_count from export_batches where operator = %s order by batch_id",
                    (OPERATOR,),
                ).fetchall()
                stamped_now = reader.execute(STAMPS, (record_ids,)).fetchall()
            fixture.batch_ids.update(batch for batch, _count in live)

            drill.check(
                "the re-export ran and produced exactly one batch",
                finished.returncode == 0 and len(live) == 1,
                f"exit {finished.returncode}, batches {live}",
            )
            if live:
                batch_id, row_count = live[0]
                fixture.batch_ids.add(batch_id)
                complete, how_complete = pair_is_complete(directory, batch_id)
                drill.check(
                    f"{batch_id} carries both records and its manifest matches the file",
                    complete and row_count == len(record_ids),
                    f"row_count {row_count}; {how_complete}",
                )
                drill.check(
                    "both records are stamped with it now",
                    all(batch == batch_id and at is not None for _id, batch, at in stamped_now),
                    "; ".join(f"{record} batch={batch}" for record, batch, _at in stamped_now),
                )
                drill.check(
                    "the killed attempt's batch id was not reused: a sequence does not roll back",
                    batch_id != killed_id,
                    f"killed {killed_id or 'none'}, committed {batch_id}",
                )
        finally:
            removed = sorted(fixture.batch_ids)
            shutil.rmtree(directory, ignore_errors=True)
            drill.note(f"removed {directory} and, with the fixture, batches {removed or 'none'}")

    with connection("DATABASE_URL_READONLY") as reader:
        left = reader.execute("select count(*) from export_batches where operator = %s", (OPERATOR,)).fetchone()[0]
    drill.check("the drill left no batch row and no file behind", left == 0 and not directory.exists(), "cleaned up")

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
