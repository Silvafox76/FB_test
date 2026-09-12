# The six failure drills

BUILD_ORDER step 11 names six failures and what each must do. These are the six scripts
that make them happen, on the live system, by hand. They are not tests: `make test` proves
these properties inside a process pytest controls and tells a person nothing they can
watch. A drill breaks something on purpose, prints what the system did, and says PASS or
FAIL against the outcome the runbook claims.

Each drill

- says at the top what it proves and which rule or checkpoint it exercises,
- leaves the system as it found it, including when it fails halfway,
- prints one `PASS` or `FAIL` line and exits non-zero unless it passed,
- and never routes around the thing it is testing: nothing is monkeypatched, no code is
  changed for a drill's benefit, and every refusal comes from the real code path.

## Running them

```
set -a && . ./.env && set +a            # the database URLs and the caps
uv run python scripts/drills/drill1_invalid_api_key.py
uv run python scripts/drills/drill2_renamed_field.py
uv run python scripts/drills/drill3_call_cap.py
uv run python scripts/drills/drill4_blank_reviewer.py
uv run python scripts/drills/drill5_pipeline_insert.py
uv run python scripts/drills/drill6_kill_export.py
```

Exit codes are three, not two:

| code | meaning |
| --- | --- |
| 0 | PASS. The system did what the runbook says it does. |
| 1 | FAIL. It did not. That is a finding about the system, not about the drill. |
| 2 | The drill could not run: no database URLs, another pipeline run in flight, today's model budget spent, or state in the way that is not the drill's. Nothing was proved either way and nothing was changed. |

**Run them on a quiet system.** Drills 1, 2 and 3 touch state a scheduled `monitor run`
moves under them - model calls today, notices by status, one source's health row - so all
three refuse to start while another process holds a `monitor_pipeline` connection, and
drills 1 and 3 refuse to draw a conclusion if `model_calls` grew while they ran. Drills 4,
5 and 6 are safe alongside a running review app. Stop the hourly timer (`systemctl --user stop
monitor-run.timer`) or wait for the run to finish.

## What each drill proves, and the outcome to expect

### 1. An invalid API key mid-run

`drill1_invalid_api_key.py`. Rules 2, 3 and 4.

Seeds one notice at `filtered_in`, then runs `monitor score --limit 1` with a credential the
API refuses (`MODEL_ROUTE=direct`, an `ANTHROPIC_API_KEY` that is deliberately not
key-shaped so the secret scanner has nothing to match). The request is real: it reaches
api.anthropic.com and comes back HTTP 401 `authentication_error`.

**Expected outcome.** `monitor score` exits non-zero with `anthropic.AuthenticationError`
on the traceback. No `scores` row, no `model_calls` row, no notice whose status moved. The
notice is still `filtered_in`.

**It also needs a day with model budget left.** The cap is checked before the client, so on
a day whose call or dollar cap is spent the score stage stops at the cap and never reaches
the credential - drill 3's outcome, not this one's. The drill checks the budget first and
exits 2 with the numbers rather than reporting a failure it did not cause. This is measured,
not imagined: it is how the drill failed the first time it ran on a day another job had
spent the day's calls. Raising the cap to get past it is a decision to take in
`config/thresholds.yaml`, by a person, out loud - and not in `.env`, where a stale
`DAILY_CALL_CAP=600` shadowed the configured 2,000 for long enough to kill a
translation run 545 calls in.

Two honest notes:

- **It cannot lose a credential halfway through a working run**, because there is no valid
  credential in this environment; the failure lands on the run's first call. The invariant
  is the same one notice further along: the scorer commits per notice, so a run that failed
  at its fourth call would leave three committed scores and a fourth notice untouched.
- **"Notices parked" is the wrong word for this failure** and the drill says so. In this
  codebase `status = 'parked'` means the model answered twice and failed validation twice,
  which is a notice a person must look at. A refused credential is not the notice's fault,
  so the notice stays at `filtered_in` and the next run picks it up with no un-parking step
  for anyone to remember. The runbook should describe it that way.

### 2. A renamed field in a fixture

`drill2_renamed_field.py`. Rule 4, and the per-source isolation `monitor/fetch.py` is built
for.

Copies `tests/contract/fixtures/worldbank.json` into a temporary directory, renames one
required field in the copy (`contact_organization` becomes `buyer_organization`), serves the
copy over loopback, and runs the real World Bank connector against it with the registry's
own source and `api_url` pointed at the copy. The committed fixture is never written to.

**Expected outcome.** Every run fails with `worldbank: ValueError: ... is missing
['contact_organization']`. `consecutive_failures` climbs 1, 2, 3 and the state walks
`healthy` -> `watch` -> `unhealthy`, `unhealthy` arriving at the source's own
`max_consecutive_failures`. No notice is stored. Every other source's `source_health` row is
byte-identical before and after, and no other source runs at all. On the way out the drill
deletes exactly the `fetch_runs` rows it created and restores the health row it read at the
start, then reads it back to check.

Two notes:

- **The loopback fixture server is not an inbound network path.** Rule 17 forbids a
  listener in the deployed system - no webhook receiver, no public endpoint. This binds to
  127.0.0.1 on a kernel-assigned port, serves one file to one client in the drill's own
  process, and is closed before the verdict prints. The alternative, monkeypatching the
  connector, would test the drill's plumbing instead of the connector's.
- **A parse failure is a failed run, not a zero-yield one.** `source_health.py` counts zero
  yield on `items_seen` and not on `items_new`, and this drill exercises the other counter.
  The two are separate on purpose; the docstring in that module says why.

### 3. `DAILY_CALL_CAP=2`

`drill3_call_cap.py`. Rule 22, and rule 6 because the number is config.

Tops today's `model_calls` count up to 2 with two rows inserted as `monitor_pipeline`,
seeds one notice at `filtered_in`, then runs `monitor score --limit 1` with
`DAILY_CALL_CAP` at 2.

**Expected outcome.** The run stops before the third call: `score_cap_exceeded` in the log,
`CapExceeded: score: 2 calls today, cap is 2` on the traceback, a non-zero exit, and
today's count still 2. No request was made, which the drill proves by putting a
deliberately invalid credential into the run's environment: if the cap check had let the
call through there would be a 401 in the output, and there is not.

The control is in the drill: `caps.check` is called against the live database at one above
the cap (it returns today's spend without raising) and at the cap (it raises), so a guard
that refused everything would not pass.

**One thing to be honest about.** The `cap_exceeded` event is a structlog line named
`score_cap_exceeded`, carrying how many notices were scored and how many were left. There
is no row in the `events` table and the drill checks that there is not: `events` is the
audit trail for a notice, a candidate and an approved record, and a run that stopped on its
budget is an operational fact about the run - the log and `make status` carry it. If the
runbook is read as promising a database row, that is a decision about `events` to take
deliberately, not a change to make to this drill.

If the drill is killed before it finishes, its two seeded rows are the ones whose `purpose`
is `drill`: `delete from model_calls where purpose = 'drill'` on the owner connection.

### 4. Approve with a blank reviewer, through the form

`drill4_blank_reviewer.py`. Checkpoint 13, and rule 14's shape.

Stages a candidate, then posts the real decision form with the reviewer field set to three
spaces - whitespace rather than empty, because whitespace is what gets through a check that
does not strip. Starlette's `TestClient` posts to the real `app` object: the real route, the
real handler, the real `db.connect("review")`.

**Expected outcome.** HTTP 303 back to `/candidate/<id>?error=a reviewer name is required:
no candidate is approved by nobody`. The candidate is still `pending_review`, there is no
`approved_records` row and no `approved` event. The same transition attempted directly on a
`monitor_review` connection raises `check_violation` from `refuse_pipeline_decision`, which
is what makes the form's message a message rather than the enforcement (rule 14). Then the
control: the identical form with a name in it approves and writes the record, so the
refusal was about the name and not about a form that refuses everything.

### 5. Insert into `approved_records` as the pipeline

`drill5_pipeline_insert.py`. Checkpoints 11 and 12.

Connects through `monitor/db.py` as `pipeline` - the same way every pipeline module gets a
connection, so the drill cannot prove something about a connection the pipeline would never
have - and tries to insert an approved record, then to read the table.

**Expected outcome.** Both raise `InsufficientPrivilege`: `permission denied for table
approved_records`. The exception class is checked, not just that something raised: a
`ForeignKeyViolation` would mean the insert had got past the grant and been stopped by the
table's shape instead, which is why the drill uses a candidate id that does not exist. The
control is `monitor_review` reading the same table in the same statement, successfully.

`tests/roles/test_roles.py` asserts the same properties on every commit. This is the version
a person runs on the host, against the grants that are actually deployed there.

### 6. Kill the export halfway

`drill6_kill_export.py`. The write order in `review/export.py`, and rule 16.

Approves two records through the real decision path, dates them 2000-01-01 so no real
record can be in the batch's range, and holds `select ... for update` on those two rows. The
export then runs: select, batch id, CSV bytes, sha256, `export_batches` insert, both files
written and renamed into place - and blocks on the very next statement, the stamp. The drill
waits until `pg_locks` shows that backend waiting for a lock, which is how it knows the
process is at the stamp and not somewhere else, sends SIGKILL, then releases the lock.

**Expected outcome, and it is a disjunction because the runbook's is.** Either a complete
CSV with a manifest whose sha256 matches the file, or no file at all - and in both arms no
`export_batches` row, no record with `exported_at` or `export_batch` set, and no `.part`
staging directory left behind. Aimed at the window after the rename, the arm that shows up
is the orphan: `B00nn/B00nn.csv` and `B00nn.manifest.json`, complete, agreeing with each
other, named by no batch row. That orphan is the residue this design accepts, and it is the
right way round: it is visible, verifiable against its own sha256 and discardable, while a
stamped row whose file was never written would be invisible and unrecoverable.

The drill then exports the same range again, to completion: exit 0, one batch, both records
in it, both stamped, and a batch id one higher than the killed attempt's - a sequence does
not roll back, so every gap in the B-series is an export that did not finish.

## How the drills avoid leaving damage

- Fixture rows are created and removed on the owner connection, because neither runtime
  role holds delete on any table: the audit log and the approved records are append only by
  grant (`migrations/002_roles.sql`). The owner is not a runtime path - nothing under
  `monitor/` or `review/` can reach `DATABASE_URL_OWNER` - and `tests/roles/test_roles.py`
  and `tests/review/conftest.py` use it for their fixtures for the same reason.
- Teardown is in `finally`, so it runs when the drill fails, and each drill checks its own
  cleanup and prints what it removed. `try/except` is never wrapped around the thing under
  test (rule 3); the only `except` in the harness turns a missing precondition into exit 2.
- Drill 2 restores the health row it read and deletes only the `fetch_runs` rows it created.
  Drill 3 deletes the `model_calls` rows it seeded. Drill 6 writes its batches into a
  temporary directory, never `exports/`, and removes its batch rows with its records.
- `_drill.py` holds what all six share: the connections, the two fixtures, the PASS/FAIL
  printing and the two preconditions. It is not a drill and does not run on its own.

## Last run

All six against the live database on this host, over the night of 2026-09-11 into
2026-09-12, with the full outputs in the step 11 session log:

| drill | verdict | checks | what it showed |
| --- | --- | --- | --- |
| 1 | PASS | 5 | HTTP 401 `invalid x-api-key`, exit 1, nothing written, notice still `filtered_in`; today's billed call count unmoved. First attempt refused to run at all, because a scoring run still had a connection open - the right answer, and the drill said so rather than measuring the concurrency. |
| 2 | PASS | 7 | `worldbank: ValueError: World Bank notice 0 (OP00468043) is missing ['contact_organization']` three times; healthy -> watch -> unhealthy; `fts`, `prozorro`, `ted` untouched; health row and `fetch_runs` restored. |
| 3 | PASS | 11 | `score_cap_exceeded`, `CapExceeded: score: 759 calls today, cap is 759`, exit 1, no 401 anywhere, nothing billed. The cap is set to today's own count plus the seeds, not to a literal 2, so the drill is correct on any day rather than only on one with an empty `model_calls`. |
| 4 | PASS | 8 | 303 to `/candidate/<id>?error=a reviewer name is required`; nothing written; `candidate ... cannot be set to approved without a named reviewer` from Postgres; the control approved. |
| 5 | PASS | 5 | `InsufficientPrivilege: permission denied for table approved_records` on both the insert and the select. |
| 6 | PASS | 12 | Killed while waiting for a ShareLock with `approved_records` and `export_batches` RowExclusiveLocks already held: `B0097` complete on disk with a matching sha256, no batch row, no stamped record, no `.part`; then `B0098` exported both records cleanly. |
