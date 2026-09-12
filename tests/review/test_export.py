"""The export, which is the only way anything leaves this system.

Step 11's acceptance is here: three approved records produce a CSV whose header row is
appendix E exactly, a manifest whose sha256 is the file on disk, three rows stamped with
the batch id, and a second export of the same range producing zero rows.

Five things are tested beyond that list, each because it can be quietly wrong:

  - **the byte order mark**, asserted as the first three bytes and then as an accented
    buyer name read back through a BOM-aware decoder. Without it Excel and Zoho read the
    file as the local codepage and "Ministère de l'Économie" arrives as mush. A test that
    only checked the text would pass on a file no importer can read.
  - **the range**, by pushing one record's approval a year back and watching it stay in
    the backlog. A range that silently matches everything would pass every other test here.
  - **drill 6's property**, by breaking the last write in the transaction: the file and its
    manifest are complete on disk, no record is marked exported, and no batch row exists.
    That is the ordering `review/export.py` chose, asserted rather than asserted about.
  - **the stamp's count**, by taking one record from a second connection in the moment
    between the select and the update. Two overlapping batches would each claim the same
    record, and one of the two files would be imported as new business already in the CRM.
  - **the button**, because "one make target and one button" is the step, and a page that
    renders but posts nowhere would satisfy neither.

Step 16 adds four more, all of them about what happens after a batch exists:

  - **the generated column spec**, rendered from `config/record_defaults.yaml` and compared
    with `docs/export_spec.md`. A column added to the config without regenerating the page
    fails here, which is the only reason that document is generated rather than written.
  - **the backlog**, as the arithmetic `ExportBacklogAgeDays` is published from and as the
    rows the queue page and `monitor status` show.
  - **re-export**, which must reissue the file that left and must not issue a new batch id,
    touch a stamp, or overwrite a published directory. Each of those is asserted by breaking
    it: the file is deleted, the column list is shortened, the CSV on disk is corrupted.
  - **the two new controls on /export**: the batch listing and the re-export form.

It needs a live database with every migration applied and it does not skip when one is
absent, for the reason `tests/roles/test_roles.py` gives. Its three candidates are built
here rather than from `conftest.py`'s `staged`, which makes one: a batch of three is what
the step accepts against, and the fixture rows are committed and removed on an owner
connection because `monitor_review` holds no delete anywhere.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import psycopg
import pytest
from starlette.testclient import TestClient

from monitor.cli import run_status
from monitor.stage.stager import FIXTURE_ID_FLOOR
from review import export as export_module
from review.app import app
from review.decisions import approve, record_defaults
from review.export import (
    SPEC_PATH,
    Backlog,
    ExportRefused,
    Waiting,
    backlog,
    batch_paths,
    columns,
    day_range,
    export,
    list_batches,
    re_export,
    render_backlog,
    spec_markdown,
)

pytestmark = pytest.mark.roles

REVIEWERS = ("Ryan Dear", "Matthew Olivier")

# One buyer with accents on purpose: the BOM is there for this name.
BUYERS = (
    ("GH", "Ministry of Finance", REVIEWERS[0]),
    ("SN", "Ministère de l'Économie, du Plan et de la Coopération", REVIEWERS[1]),
    ("GH", "Ghana Revenue Authority", REVIEWERS[0]),
)


@dataclass(frozen=True)
class Batch:
    """What the fixture staged and approved, and the operator name its batches carry."""

    operator: str
    candidate_ids: tuple[str, ...]
    record_ids: tuple[str, ...]


@pytest.fixture
def exports_dir(tmp_path, monkeypatch) -> Path:
    """Batches are written to a temporary directory, never the repository's exports/."""
    monkeypatch.setenv(export_module.EXPORT_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def approved(owner, review):
    """Three approved records, approved by two reviewers through the real decision path."""
    marker = uuid.uuid4().hex[:8]
    operator = f"Export Operator {marker}"
    candidate_ids = []
    source_ids = []
    notice_ids = []

    for index, (country, buyer, _reviewer) in enumerate(BUYERS):
        candidate_id = f"C{FIXTURE_ID_FLOOR + (int(marker, 16) + index) % (1_000_000 - FIXTURE_ID_FLOOR):06d}"
        source_id = f"test-exp-{marker}-{index}"
        content_hash = f"sha256:{marker}{index}"

        owner.execute(
            """
            insert into sources (id, name, country, admin_level, language, stream, access_type,
                                 connector_class, wave, tos_status, enabled, expected_min,
                                 expected_max, max_consecutive_failures)
            values (%s, %s, %s, 'national', 'en', 'feed', 'api', 'FeedConnector', 1, 'cleared',
                    false, 1, 50, 3)
            """,
            (source_id, f"Procurement authority {index}", country),
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
            values (%s, %s, 'https://example.invalid/notice', 'IFMIS replacement', %s,
                    'national', 'en', 'scored')
            returning id
            """,
            (content_hash, source_id, country),
        ).fetchone()[0]
        owner.execute(
            """
            insert into candidates (id, primary_notice_id, score, status, region, language, title_en,
                                    buyer, country, admin_level, summary_en, matched_functions,
                                    system_names, procurement_type, estimated_value_usd,
                                    eligibility_flags, deadline_at)
            values (%s, %s, 78, 'pending_review', 'West Africa', 'en',
                    'Supply and implementation of an integrated financial management system',
                    %s, %s, 'national', 'The ministry is replacing its IFMIS.',
                    %s::jsonb, %s, 'system', 4200000, %s, '2026-11-30T17:00:00Z')
            """,
            (
                candidate_id,
                notice_id,
                buyer,
                country,
                json.dumps([{"function_id": "budget_execution", "evidence": "financial management system"}]),
                ["IFMIS"],
                [],
            ),
        )
        owner.execute(
            """
            insert into candidate_notices (candidate_id, notice_id, match_method, match_score)
            values (%s, %s, 'content_hash', 100)
            """,
            (candidate_id, notice_id),
        )

        candidate_ids.append(candidate_id)
        source_ids.append(source_id)
        notice_ids.append(notice_id)

    record_ids = tuple(
        approve(review, candidate_id, reviewer)
        for candidate_id, (_country, _buyer, reviewer) in zip(candidate_ids, BUYERS, strict=True)
    )

    yield Batch(operator=operator, candidate_ids=tuple(candidate_ids), record_ids=record_ids)

    review.rollback()
    owner.execute("delete from events where entity_id = any(%s::text[])", (list(candidate_ids) + list(record_ids),))
    owner.execute("update candidates set approved_record_id = null where id = any(%s::text[])", (candidate_ids,))
    owner.execute("delete from approved_records where id = any(%s::text[])", (list(record_ids),))
    batch_ids = [
        row[0] for row in owner.execute("select batch_id from export_batches where operator = %s", (operator,))
    ]
    # A re-export's event names the batch, so it is not caught by the record ids above.
    owner.execute("delete from events where entity_type = 'export_batch' and entity_id = any(%s::text[])", (batch_ids,))
    # Release EVERY record stamped with these batches, not only this fixture's.
    #
    # `export` takes every approved record with a null `exported_at`, which is what the
    # real export does and is correct. So a batch produced here can carry rows this
    # fixture never created - another test's, or a row left behind by an earlier run
    # that died before its own teardown. Those rows still reference the batch, and
    # `approved_records_export_batch_fk` then refuses the delete below, which surfaces
    # as an error in THIS test's teardown and is blamed on this test.
    #
    # The batch is ours to remove, so everything pointing at it has to be released
    # first. Clearing the stamp rather than deleting the row: a record this fixture did
    # not create is not this fixture's to delete, and putting it back in the backlog is
    # exactly where it was before the export ran.
    owner.execute(
        "update approved_records set export_batch = null, exported_at = null where export_batch = any(%s::text[])",
        (batch_ids,),
    )
    owner.execute("delete from export_batches where operator = %s", (operator,))
    owner.execute("delete from candidate_notices where candidate_id = any(%s::text[])", (candidate_ids,))
    owner.execute("delete from candidates where id = any(%s::text[])", (candidate_ids,))
    owner.execute("delete from notices where id = any(%s::uuid[])", (notice_ids,))
    owner.execute("delete from notices_raw where source_id = any(%s::text[])", (source_ids,))
    owner.execute("delete from sources where id = any(%s::text[])", (source_ids,))


@pytest.fixture
def exported(approved, exports_dir) -> str:
    """One batch, produced from the three approved records, its file on disk. Returns the batch id."""
    return export(*today_range(), approved.operator)


def today_range() -> tuple[datetime, datetime]:
    """The range an operator types on the day they approve and export: today at both ends."""
    today = datetime.now(UTC).date().isoformat()
    return day_range(today, today)


def csv_rows(directory: Path, batch_id: str) -> list[list[str]]:
    """The batch's CSV as a person's spreadsheet would read it: BOM-aware, header first."""
    csv_path, _ = batch_paths(directory, batch_id)
    return list(csv.reader(io.StringIO(csv_path.read_text(encoding="utf-8-sig"))))


def manifest_of(directory: Path, batch_id: str) -> dict:
    _, manifest_path = batch_paths(directory, batch_id)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def values_of(rows: list[list[str]], name: str) -> list[str]:
    """One column of the batch, by header name."""
    index = rows[0].index(name)
    return [row[index] for row in rows[1:]]


def stamps(conn, record_ids) -> list[tuple]:
    return conn.execute(
        "select id, export_batch, exported_at from approved_records where id = any(%s::text[]) order by id",
        (list(record_ids),),
    ).fetchall()


# --- the header row is the contract with the import mapper --------------------


def test_the_header_row_is_appendix_e_exactly_and_in_order(approved, exports_dir):
    batch_id = export(*today_range(), approved.operator)

    header = csv_rows(exports_dir, batch_id)[0]
    assert header == [column["name"] for column in record_defaults()["columns"]]
    assert len(header) == 73, "appendix E is 73 columns; a 74th would be a column Zoho cannot map"
    assert header[-2:] == ["monitor_candidate_id", "monitor_export_batch"]
    assert header.count("monitor_candidate_id") == 1, "the two monitor columns are already in appendix E"


def test_the_file_begins_with_a_byte_order_mark(approved, exports_dir):
    batch_id = export(*today_range(), approved.operator)

    csv_path, _ = batch_paths(exports_dir, batch_id)
    assert csv_path.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_an_accented_buyer_survives_the_round_trip(approved, exports_dir):
    """Without the BOM this file reads as the local codepage and this name arrives mangled."""
    batch_id = export(*today_range(), approved.operator)

    accounts = values_of(csv_rows(exports_dir, batch_id), "Account Name")
    assert any("Ministère de l'Économie" in account for account in accounts)


# --- the manifest is the receipt ----------------------------------------------


def test_the_manifest_sha256_is_the_file_as_written(approved, exports_dir):
    batch_id = export(*today_range(), approved.operator)

    csv_path, _ = batch_paths(exports_dir, batch_id)
    manifest = manifest_of(exports_dir, batch_id)

    assert manifest["sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert manifest["sha256"] != hashlib.sha256(csv_path.read_text(encoding="utf-8-sig").encode()).hexdigest(), (
        "the hash must be of the bytes on disk, BOM included, not of the decoded text"
    )


def test_the_manifest_carries_the_batch_the_operator_and_the_reviewers(approved, exports_dir):
    range_from, range_to = today_range()
    batch_id = export(range_from, range_to, approved.operator)

    manifest = manifest_of(exports_dir, batch_id)
    assert manifest["batch_id"] == batch_id
    assert manifest["operator"] == approved.operator
    assert manifest["row_count"] == 3
    assert manifest["reviewers"] == sorted(REVIEWERS), "the people who approved, not the one who exported"
    assert manifest["range_from"] == range_from.isoformat()
    assert manifest["range_to"] == range_to.isoformat()
    assert manifest["csv"] == f"{batch_id}.csv"


def test_the_batch_row_matches_the_manifest(approved, exports_dir, review):
    batch_id = export(*today_range(), approved.operator)

    row = review.execute(
        "select operator, row_count, file_path, manifest_path, sha256 from export_batches where batch_id = %s",
        (batch_id,),
    ).fetchone()
    csv_path, manifest_path = batch_paths(exports_dir, batch_id)
    manifest = manifest_of(exports_dir, batch_id)

    assert row == (approved.operator, 3, str(csv_path), str(manifest_path), manifest["sha256"])
    assert batch_id.startswith("B") and len(batch_id) == 5, "B0001 format, from export_batch_id_seq"


# --- three rows, stamped -------------------------------------------------------


def test_three_rows_are_stamped_with_the_batch_id(approved, exports_dir, review):
    batch_id = export(*today_range(), approved.operator)

    for record_id, batch, exported_at in stamps(review, approved.record_ids):
        assert batch == batch_id, f"{record_id} is not stamped with its batch"
        assert exported_at is not None

    rows = csv_rows(exports_dir, batch_id)
    assert len(rows) == 4, "a header and three records"
    assert set(values_of(rows, "monitor_export_batch")) == {batch_id}
    assert sorted(values_of(rows, "monitor_candidate_id")) == sorted(approved.candidate_ids)


def test_every_exported_record_gets_an_event(approved, exports_dir, review):
    batch_id = export(*today_range(), approved.operator)

    events = review.execute(
        "select entity_id, action, actor, after from events where entity_id = any(%s::text[]) and action = 'exported'",
        (list(approved.record_ids),),
    ).fetchall()
    assert len(events) == 3
    assert {event[2] for event in events} == {approved.operator}
    assert all(event[3].startswith(batch_id) for event in events)


# --- a record leaves once -------------------------------------------------------


def test_a_second_export_in_the_same_range_produces_zero_rows(approved, exports_dir, review):
    first = export(*today_range(), approved.operator)
    second = export(*today_range(), approved.operator)

    assert second != first
    assert csv_rows(exports_dir, second) == [csv_rows(exports_dir, first)[0]], "a header row and nothing else"
    assert manifest_of(exports_dir, second)["row_count"] == 0
    assert manifest_of(exports_dir, second)["reviewers"] == []
    assert review.execute("select row_count from export_batches where batch_id = %s", (second,)).fetchone()[0] == 0

    assert {row[1] for row in stamps(review, approved.record_ids)} == {first}, "the records keep their first batch"


def test_a_record_approved_outside_the_range_stays_in_the_backlog(approved, exports_dir, owner, review):
    """The range is on the approval moment, and it selects rather than decorates."""
    left_behind = approved.record_ids[0]
    owner.execute(
        "update approved_records set created_at = %s where id = %s",
        (datetime.now(UTC) - timedelta(days=365), left_behind),
    )

    batch_id = export(*today_range(), approved.operator)

    assert len(csv_rows(exports_dir, batch_id)) == 3, "a header and the two records approved today"
    exported = dict((row[0], row[1]) for row in stamps(review, approved.record_ids))
    assert exported[left_behind] is None
    assert set(exported.values()) == {None, batch_id}


def test_a_record_taken_between_the_select_and_the_stamp_stops_the_whole_batch(
    approved, exports_dir, review, monkeypatch
):
    """The counts have to agree, or the batch is not a batch.

    Simulated by stamping one record from a second connection in the moment between the
    select and the update — which is exactly what a second operator pressing the button
    would do. Two overlapping batches would each claim the same record and one of the two
    CSVs would be imported as new business that is already in the CRM.
    """
    real_write = export_module.write_batch_files

    def steal_one(*args, **kwargs):
        with psycopg.connect(os.environ["DATABASE_URL_REVIEW"], autocommit=True) as interloper:
            interloper.execute(
                "update approved_records set exported_at = now() where id = %s", (approved.record_ids[0],)
            )
        return real_write(*args, **kwargs)

    monkeypatch.setattr(export_module, "write_batch_files", steal_one)

    with pytest.raises(ExportRefused, match="3 records were selected and 2 could be stamped"):
        export(*today_range(), approved.operator)

    assert [row[1] for row in stamps(review, approved.record_ids)] == [None, None, None], "no batch was claimed"
    assert (
        review.execute("select count(*) from export_batches where operator = %s", (approved.operator,)).fetchone()[0]
        == 0
    )


# --- refusals write nothing -----------------------------------------------------


def test_a_blank_operator_is_refused_and_no_file_is_written(approved, exports_dir, review):
    before = review.execute("select count(*) from export_batches").fetchone()[0]

    with pytest.raises(ExportRefused, match="operator name is required"):
        export(*today_range(), "   ")

    assert review.execute("select count(*) from export_batches").fetchone()[0] == before
    assert list(exports_dir.iterdir()) == []


def test_a_range_that_ends_before_it_starts_is_refused(approved, exports_dir):
    range_from, range_to = today_range()

    with pytest.raises(ExportRefused, match="ends before it starts"):
        export(range_to, range_from, approved.operator)

    assert list(exports_dir.iterdir()) == []


def test_a_date_that_is_not_a_date_is_refused_by_name():
    with pytest.raises(ExportRefused, match="the to date is not a date"):
        day_range("2026-09-12", "12/09/2026")


# --- drill 6: killed halfway -----------------------------------------------------


def test_a_failure_after_the_file_is_written_leaves_a_whole_file_and_no_stamp(
    approved, exports_dir, review, monkeypatch
):
    """Drill 6's property, asserted: either a complete CSV with its manifest, or no file.

    The last write in the transaction is broken, which is the worst case for the ordering
    in `review/export.py`: the file has already been renamed into place. What must not
    survive is a record marked exported, or a batch row naming a file, for a transaction
    that did not commit.
    """

    def refuse(*args, **kwargs):
        raise psycopg.errors.InsufficientPrivilege("events insert failed")

    monkeypatch.setattr(export_module, "write_event", refuse)

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        export(*today_range(), approved.operator)

    assert (
        review.execute("select count(*) from export_batches where operator = %s", (approved.operator,)).fetchone()[0]
        == 0
    )
    assert [row[1] for row in stamps(review, approved.record_ids)] == [None, None, None]

    orphan = [path for path in exports_dir.iterdir() if path.is_dir()]
    assert len(orphan) == 1 and not orphan[0].name.endswith(".part"), "a published directory, not a half-written one"
    batch_id = orphan[0].name
    csv_path, manifest_path = batch_paths(exports_dir, batch_id)
    assert csv_path.exists() and manifest_path.exists(), "the pair is published by one rename or not at all"
    assert manifest_of(exports_dir, batch_id)["sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()


# --- one button ------------------------------------------------------------------


def test_the_export_page_shows_the_backlog_and_the_form(approved, exports_dir, client):
    page = client.get("/export").text

    assert "approved record" in page
    assert 'name="operator"' in page
    assert 'action="/export"' in page
    assert "no CRM connection here" in page, "the page says what it does not do"


def test_the_button_produces_a_batch_and_shows_it(approved, exports_dir, client, review):
    range_from, range_to = (datetime.now(UTC).date().isoformat(),) * 2

    posted = client.post(
        "/export",
        data={"range_from": range_from, "range_to": range_to, "operator": approved.operator},
        follow_redirects=False,
    )
    assert posted.status_code == 303

    batch_id = posted.headers["location"].removeprefix("/export?batch=")
    assert csv_rows(exports_dir, batch_id)[0] == [column["name"] for column in record_defaults()["columns"]]
    assert {row[1] for row in stamps(review, approved.record_ids)} == {batch_id}

    page = client.get(f"/export?batch={batch_id}").text
    assert batch_id in page
    assert manifest_of(exports_dir, batch_id)["sha256"] in page


def test_the_button_refuses_a_blank_operator_and_says_so(approved, exports_dir, client):
    today = datetime.now(UTC).date().isoformat()

    posted = client.post(
        "/export",
        data={"range_from": today, "range_to": today, "operator": ""},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    assert "error=" in posted.headers["location"]
    assert list(exports_dir.iterdir()) == []


# --- the backlog ------------------------------------------------------------------


def test_the_backlog_age_is_how_long_the_oldest_record_has_waited():
    """`ExportBacklogAgeDays`, the number the export-backlog-age alarm is declared against.

    Pure arithmetic on a constructed backlog rather than on the database, because the value
    that matters is the one a seven-day threshold is compared with, and a test that built
    its own nine-day-old record would be asserting that `now()` is now.
    """
    as_of = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    waiting = Backlog(
        records=(
            Waiting("R000001", "C000001", REVIEWERS[0], as_of - timedelta(days=9, hours=12)),
            Waiting("R000002", "C000002", REVIEWERS[1], as_of - timedelta(days=1)),
        ),
        as_of=as_of,
    )

    assert waiting.count == 2
    assert waiting.oldest.record_id == "R000001", "oldest first, which is the order the alarm cares about"
    assert waiting.age_days == pytest.approx(9.5)

    empty = Backlog(records=(), as_of=as_of)
    assert empty.count == 0
    assert empty.oldest is None
    assert empty.age_days == 0.0, "nothing waiting is nought days, not no reading"


def test_the_status_line_names_the_metric_and_the_oldest_record():
    as_of = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    waiting = Backlog(
        records=(Waiting("R000001", "C000001", REVIEWERS[0], as_of - timedelta(days=9, hours=12)),),
        as_of=as_of,
    )

    line = render_backlog(waiting)
    assert "export backlog: 1 approved record waiting" in line
    assert "oldest R000001 approved 9.5 days ago (2026-09-03" in line
    assert "ExportBacklogAgeDays 9.5" in line

    assert "nothing waiting" in render_backlog(Backlog(records=(), as_of=as_of))


def test_the_backlog_is_every_approved_record_not_yet_exported_oldest_first(approved, review):
    mine = [record for record in backlog(review).records if record.record_id in approved.record_ids]

    assert [record.record_id for record in mine] == list(approved.record_ids), "oldest approval first"
    assert [record.candidate_id for record in mine] == list(approved.candidate_ids)
    assert [record.approved_by for record in mine] == [reviewer for _country, _buyer, reviewer in BUYERS]


def test_exporting_takes_the_records_out_of_the_backlog(approved, exports_dir, review):
    export(*today_range(), approved.operator)

    waiting = {record.record_id for record in backlog(review).records}
    assert not waiting & set(approved.record_ids), "a record that has left is not waiting to leave"


def test_monitor_status_prints_the_export_backlog(approved, capsys):
    """`make status` is `monitor status`, and step 16's acceptance is that the backlog is in it.

    It is read there on a `monitor_readonly` connection: the command's own connection is the
    pipeline's, and `migrations/002_roles.sql` revokes all on `approved_records` from that
    role, so this passing at all is the proof the second connection is the right one.
    """
    assert run_status() == 0

    printed = capsys.readouterr().out
    assert "export backlog:" in printed
    assert "ExportBacklogAgeDays" in printed
    # Three records are waiting, so the empty wording would mean the number is not being read.
    assert "nothing waiting" not in printed


def test_the_queue_page_shows_what_is_waiting_to_be_exported(approved, client):
    page = client.get("/").text

    assert "waiting to be exported" in page
    assert "/export" in page


# --- re-exporting a named batch ---------------------------------------------------


def test_re_export_writes_the_same_two_files_again_after_they_are_deleted(approved, exports_dir, exported, review):
    """The case this exists for: the file is gone and BD needs the batch they were promised."""
    csv_path, manifest_path = batch_paths(exports_dir, exported)
    original_csv = csv_path.read_bytes()
    original_manifest = manifest_path.read_bytes()
    shutil.rmtree(csv_path.parent)

    reissued = re_export(exported, "Ops Person")

    assert reissued.rewritten
    assert reissued.batch_id == exported, "the same batch id: the records already name it"
    assert csv_path.read_bytes() == original_csv, "byte for byte the file that left"
    assert manifest_path.read_bytes() == original_manifest
    assert manifest_of(exports_dir, exported)["operator"] == approved.operator, (
        "the manifest names the person who produced the batch, not the person who reissued it"
    )
    assert reissued.sha256 == manifest_of(exports_dir, exported)["sha256"]


def test_re_export_issues_no_new_batch_and_touches_no_stamp(approved, exports_dir, exported, review):
    """What re-export must not do: a new batch id for the same records, or a cleared stamp.

    Either would lose the audit trail. A second batch row would leave two batches claiming
    the same records; a cleared `exported_at` would put records BD has already imported back
    in the backlog as if they were new.
    """
    before = stamps(review, approved.record_ids)
    csv_path, _ = batch_paths(exports_dir, exported)
    shutil.rmtree(csv_path.parent)

    re_export(exported, "Ops Person")

    assert stamps(review, approved.record_ids) == before
    assert (
        review.execute("select count(*) from export_batches where operator = %s", (approved.operator,)).fetchone()[0]
        == 1
    )
    assert {record.record_id for record in backlog(review).records} & set(approved.record_ids) == set()


def test_re_export_records_who_asked_for_it(approved, exports_dir, exported, review):
    re_export(exported, "Ops Person")

    events = review.execute(
        "select actor, after from events where entity_type = 'export_batch' and entity_id = %s",
        (exported,),
    ).fetchall()
    assert len(events) == 1
    actor, after = events[0]
    assert actor == "Ops Person", "the person reissuing is in the audit log, not in the file"
    assert after.startswith(f"{exported}: verified in place")


def test_re_export_of_a_batch_still_on_disk_rewrites_nothing(approved, exports_dir, exported):
    """A published batch is checked and left alone. Rewriting it would be a new file under an old name."""
    csv_path, _ = batch_paths(exports_dir, exported)
    written_at = csv_path.stat().st_mtime_ns

    reissued = re_export(exported, "Ops Person")

    assert not reissued.rewritten
    assert csv_path.stat().st_mtime_ns == written_at


def test_re_export_refuses_when_the_column_list_has_changed_since_the_batch(
    approved, exports_dir, exported, monkeypatch
):
    """The reason the rebuilt bytes are hashed at all.

    A column removed from `config/record_defaults.yaml` after a batch was produced renders a
    different file. Reissuing it under the batch's own id would hand BD a header row that is
    not the one they mapped, and the manifest's sha256 would no longer describe the file
    beside it.
    """
    shortened = record_defaults()
    shortened["columns"] = shortened["columns"][:-1]
    monkeypatch.setattr(export_module, "record_defaults", lambda: shortened)

    with pytest.raises(ExportRefused, match="rebuilds to a different file"):
        re_export(exported, "Ops Person")


def test_re_export_never_overwrites_a_published_directory(approved, exports_dir, exported):
    """Bytes on disk that are not the batch are wreckage, and a person looks at wreckage."""
    csv_path, _ = batch_paths(exports_dir, exported)
    csv_path.write_bytes(b"not the batch that left")

    with pytest.raises(ExportRefused, match="already exists and is not"):
        re_export(exported, "Ops Person")

    assert csv_path.read_bytes() == b"not the batch that left"


def test_re_export_refuses_a_batch_that_does_not_exist(approved, exports_dir):
    with pytest.raises(ExportRefused, match="there is no batch"):
        re_export("B9999", "Ops Person")


def test_re_export_refuses_a_blank_operator(approved, exports_dir, exported):
    with pytest.raises(ExportRefused, match="operator name is required"):
        re_export(exported, "   ")


# --- the listing and the second button --------------------------------------------


def test_the_batch_listing_says_whether_each_file_is_still_there(approved, exports_dir, exported, review):
    listed = [batch for batch in list_batches(review) if batch.batch_id == exported]
    assert len(listed) == 1
    assert listed[0].on_disk

    csv_path, _ = batch_paths(exports_dir, exported)
    shutil.rmtree(csv_path.parent)
    assert not [batch for batch in list_batches(review) if batch.batch_id == exported][0].on_disk


def test_the_export_page_lists_the_batch_and_offers_the_re_export(approved, exports_dir, exported, client):
    page = client.get("/export").text

    assert exported in page
    assert "on disk" in page
    assert 'action="/export/re-export"' in page


def test_the_re_export_button_puts_a_missing_file_back(approved, exports_dir, exported, client):
    csv_path, _ = batch_paths(exports_dir, exported)
    original = csv_path.read_bytes()
    shutil.rmtree(csv_path.parent)

    posted = client.post(
        "/export/re-export",
        data={"batch": exported, "operator": approved.operator},
        follow_redirects=False,
    )

    assert posted.status_code == 303
    assert f"batch={exported}" in posted.headers["location"]
    assert csv_path.read_bytes() == original

    # The outcome comes back as a note and not as an error: nothing failed.
    page = client.get(posted.headers["location"]).text
    assert f"{exported} written again" in page


def test_the_re_export_button_refuses_a_batch_that_does_not_exist(approved, exports_dir, client):
    posted = client.post(
        "/export/re-export",
        data={"batch": "B9999", "operator": approved.operator},
        follow_redirects=False,
    )

    assert posted.status_code == 303
    # Unquoted, because the refusal travels as a percent-encoded query parameter.
    assert "error=" in posted.headers["location"]
    assert "there is no batch 'B9999'" in unquote(posted.headers["location"])


# --- the generated column spec ----------------------------------------------------


def test_the_spec_on_disk_is_the_one_the_config_generates():
    """Step 16's acceptance: the document cannot drift from the column list it describes."""
    assert SPEC_PATH.read_text(encoding="utf-8") == spec_markdown(), (
        "docs/export_spec.md and config/record_defaults.yaml disagree. Run "
        "`uv run python scripts/generate_export_spec.py` and commit the result with the config change."
    )


def test_a_column_added_to_the_config_fails_that_check(monkeypatch):
    """The failure the step asks for, provoked rather than described.

    A 74th column is added to the loaded config and the spec is rendered again: it now names
    the new column and no longer matches the file on disk, which is exactly the assertion
    above going red.
    """
    defaults = record_defaults()
    defaults["columns"] = [*defaults["columns"], {"name": "Reseller Margin", "category": "B"}]
    monkeypatch.setattr(export_module, "record_defaults", lambda: defaults)

    generated = spec_markdown()
    assert "| 74 | Reseller Margin | B |" in generated
    assert generated != SPEC_PATH.read_text(encoding="utf-8")


def test_the_spec_table_is_the_csv_header_row():
    """Not only that the text matches: that the table is the header row, in order."""
    listed = [
        line.split("|")[2].strip()
        for line in SPEC_PATH.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\| \d+ \|", line)
    ]
    assert listed == columns()
    assert len(listed) == 73


def test_a_category_appendix_e_does_not_define_is_refused(monkeypatch):
    """A new category is a document change, not a silent omission from the legend (rule 4)."""
    defaults = record_defaults()
    defaults["columns"] = [*defaults["columns"], {"name": "Reseller Margin", "category": "X"}]
    monkeypatch.setattr(export_module, "record_defaults", lambda: defaults)

    with pytest.raises(ExportRefused, match="appendix E does not define"):
        spec_markdown()
