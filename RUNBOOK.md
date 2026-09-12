# Runbook

How to operate the Monitor, written for the person at the keyboard rather than for
the person who designed it. No architecture knowledge is assumed; where a procedure
depends on a rule from `CLAUDE.md`, the rule's number is beside it, because knowing
*why* a refusal happened is what stops somebody working around it.

Two halves. **Operating it** comes first: start, stop, read health, add a source,
change a keyword, roll back a prompt, where the logs are, where the cost line is, and
the six failure drills with the outcome each must produce. **Measurements** follows:
what each step's acceptance test recorded, kept as written and never edited, because
a number that moves gets a new row with its date rather than a correction in place.

`DEMO.md` is the ten-minute walkthrough for showing the system to somebody else.
`deploy/README.md` is the scheduler in full. `scripts/drills/README.md` is how each
drill breaks what it breaks. This file is the one to read first.

## Before any command

Every command that touches Postgres needs the three database URLs in the environment:

```bash
cd /opt/monitor            # or wherever the checkout is
set -a && . ./.env && set +a
```

**The URL is what chooses the database role, not an argument and not a code path**
(rule 11). `monitor/db.py` reads `DATABASE_URL_PIPELINE` for everything under
`monitor/` and `DATABASE_URL_REVIEW` for everything under `review/`, and it offers
no third option: reporting connects as `monitor_readonly` through `psql`, and
migrations connect as the owner through `monitor/migrate.py`, which is not a
runtime path. So when a command says `permission denied`, read the URL it was given
before reading the code.

Nothing in this runbook needs the owner connection except a migration. If a
procedure below seems to want one, it is a procedure that should be argued about
rather than run.

## Start

Starting the Monitor is three things, and only the first is required before you can
read anything: the database, the review app, and the clock.

### 1. The database and the registry

On a host with Docker, one command does all of it:

```bash
make up          # compose up postgres, wait for healthy, make migrate, make seed
```

`make up` is idempotent: run it again and it applies no migration and re-seeds the
same rows. Use it after every change to `sources/*.yaml` or `config/*.yaml`, which
is why the keyword procedure below ends with it.

**Where Postgres is not in Compose, `make up` is the wrong command and will fail at
`docker compose up`.** The pilot host runs Postgres natively (`localhost` in the
URLs rather than `postgres`), and so does this checkout — PostgreSQL 16.13, started
by the host, no Docker daemon. There the two halves of `make up` are run directly:

```bash
make migrate     # uv run python -m monitor.migrate, as DATABASE_URL_OWNER
make seed        # uv run python -m monitor.registry, as the pipeline role
```

Run on this checkout on 2026-09-12:

```
migrations: nothing to apply
migrations: passwords set for monitor_pipeline, monitor_readonly, monitor_review
registry: 6 sources, 33 functions, 13 config files version-hashed
```

"Nothing to apply" is the healthy answer on a database that is already current;
`migrations/` is applied once each, in order, and the runner records which. The
three role passwords are set from the environment on every run, which is why no
password appears in a `.sql` file (rule 20).

### 2. The review app

```bash
make review      # uvicorn review.app:app --host 127.0.0.1 --port 8080
```

Then <http://127.0.0.1:8080/>. It runs in the foreground; Ctrl-C stops it.

There is no login, and that is not an omission. **The binding is the access
control** (rule 17): the app listens on the loopback interface only, so a reviewer
on the host reaches it and nobody off the host can. A reviewer working remotely
reaches it through an SSM port-forward to the same loopback address. Changing the
host in the Makefile would change what rule 17 says about the system, so it is a
decision to take out loud rather than a flag to flip.

### 3. The clock

`deploy/README.md` is the authority here and this is the summary. Two commands on a
timer, `monitor run` and `monitor status`, each appending to a log file:

```bash
# systemd host: enable the TIMERS, not the services
sudo systemctl enable --now monitor-run.timer monitor-status.timer
systemctl list-timers 'monitor-*'

# workstation with Docker and no systemd
docker compose -f docker-compose.yml -f deploy/docker-compose.cron.yml \
    up -d pipeline-cron status-cron
```

Hourly is the weekend cadence, for a demo's sake. From Monday the cadence comes
from each source's `schedule` in `sources/*.yaml` and is installed as a generated
drop-in; `deploy/README.md` has the generator and the reason it is generated rather
than typed.

**The fetch stage now reads `Source.schedule`, and that changes what an hourly wake
means.** Until 2026-09-12 every wake read every enabled source, so a source asking
for `30 10 * * *` was fetched twenty-four times a day. `monitor fetch all` now asks
each enabled source whether it has been read since its schedule last fired, and only
reads the ones that have not; the rest are reported as `not due` and cost nothing. So
an hourly wake is now safe rather than merely tolerated, and the Monday drop-in is
less urgent than it was - the timer can stay hourly and let the registry decide.

Two things about it worth knowing before reading a log. It uses the last ATTEMPT, not
the last success: a source that failed at 10:30 has spent its pass for the day, and
coming back at 11:30 would be a retry (rule 2) against a host that just refused us.
And **naming a source bypasses the check** - `make fetch S=ted` is a person asking
now, and it fetches whether the schedule agrees or not.

Enabling a timer does not take a pass, and neither does starting the Compose loops.
To take one now:

```bash
make fx          # the day's exchange rates, once, before the first stage of the day
make run         # fetch all, filter, score, dedupe, stage
```

`make run` stops at the first stage that fails and keeps everything the stages
before it committed, so the next pass resumes there. A pass that stops at the
scorer has still stored and filtered the day's notices.

**Staging needs a rate table no older than the tolerance in `config/fx.yaml`**
(seven days). It reads the newest day `make fx` has stored and stamps every
candidate it creates with that rate and its date; if nothing fresh enough is held
it refuses to stage anything, including candidates with no stated value, rather
than write a batch carrying last month's arithmetic. `make fx` is deliberately not
inside `make run`: `run` fires hourly and the rate publisher is read once a day
(rule 21), on its own timer at 09:00 UTC. The refusal reads
`StaleRatesError: newest nbu rates are from ..., N days old` and the fix is
`make fx`.

## Stop

| What | How | What it leaves behind |
| --- | --- | --- |
| The review app | Ctrl-C in its terminal | Nothing. Every request opens its own connection and closes it. |
| The clock, systemd | `sudo systemctl stop monitor-run.timer monitor-status.timer` | A pass already running finishes. `disable` as well if it should not come back at boot. |
| The clock, Compose | `docker compose -f docker-compose.yml -f deploy/docker-compose.cron.yml stop pipeline-cron status-cron` | Up to 60 seconds while a fetch in flight finishes politely. |
| A pass in flight | Ctrl-C, or let the machine go | Whatever the completed stages committed. Nothing half-written. |
| Postgres, Compose | `make down` | The data volume. `make down` never removes `monitor_pgdata`. |
| Postgres, native | `sudo systemctl stop postgresql` | Everything. This is a host decision, not a Monitor one; leaving it running is normal. |

Two things stopping does **not** do. It does not notify anybody, here or anywhere
(rule 18): a stopped pipeline is visible in `make status`, in the exit status and in
the log, and the reviewer works the queue on a schedule they set. And it does not
need a cleanup step: the export is the only operation with a file and a database row
to keep in step, and it commits last on purpose, so a kill leaves either a complete
pair of files with no batch row or nothing at all (drill 6).

## Read health

```bash
make status
```

Run on this checkout on 2026-09-12:

```
source       state     notices  passed  dropped  drop rate  needs tr.  unfiltered
fts          healthy        25       0       23       100%          0           0
prozorro     healthy       390       0      390       100%          0           0
ted          healthy       806      71      698        91%          0           0
worldbank    healthy        13       0        7       100%          0           0

considered 1189, passed 71, dropped 1118, drop rate 94.0%
```

`state` is the health ladder in `monitor/health/source_health.py`: a failed run
increments `consecutive_failures`, a run that *sees* nothing on a source that
normally sees something increments `zero_yield_runs`, either counter at 2 is
`watch`, and at the source's own `max_consecutive_failures` (3 for every wave-1
source) it is `unhealthy`. `unknown` means the source has never run. A source that
returns HTTP 200 and zero rows is a failure state, not a quiet day (rule 4) — which
is the single most useful thing on this line, because that is what a portal that
changed its markup looks like.

The last four columns are the free filter's arithmetic: what it decided about, what
it passed to the scorer, what it dropped for a CPV code or for no lexicon match, and
what it is holding because the notice is in a language with no lexicon. `unfiltered`
is the filter's own backlog.

**`passed` is a queue, not a total, and the rows do not add up to `notices` on
purpose.** Both `passed` and `dropped` are counted from the notice's *current*
status, so a notice the scorer has taken has left `filtered_in` and is in neither
column: in the reading above `fts` shows 25 notices and 23 decided because two have
been scored. The consequence to remember is that **the drop rate rises as the scorer
works through the day**, so it is a reading of the moment rather than a statistic
about a source. The measured drop rates in the second half of this file were each
taken at a stated time for that reason.

**Three of the four readings the Makefile promises are not in this output.** `make
status` says "source health, today's calls and cost, queue depth, export backlog";
`monitor/health/status.py` prints source health and the filter's arithmetic and
nothing else. Until that command grows, the other three are read where they
actually live:

| Reading | Where it is |
| --- | --- |
| Today's calls and cost | `model_calls`, one row per call — see "Where the cost line is" below |
| Queue depth | the queue page, <http://127.0.0.1:8080/>, which prints the count and the estimated minutes |
| Export backlog | the export page, <http://127.0.0.1:8080/export>, "waiting to leave" |

The reviewer's own view of health is <http://127.0.0.1:8080/sources>: every source
in the registry with its state, last success, consecutive failures, zero-yield runs,
median items per run and total notices stored. It is the same data, per source, with
the disabled sources shown rather than hidden.

For the run-by-run history, which neither page shows:

```bash
psql "$DATABASE_URL_READONLY" -c "
select source_id, started_at, status, items_seen, items_new, error
from fetch_runs order by started_at desc limit 20;"
```

Run on this checkout on 2026-09-12:

```
 source_id |          started_at           | status | items_seen | items_new | error
-----------+-------------------------------+--------+------------+-----------+-------
 worldbank | 2026-09-11 23:18:19.98506+00  | ok     |         13 |         0 |
 worldbank | 2026-09-11 23:18:13.855272+00 | ok     |         13 |        13 |
 ted       | 2026-09-11 22:35:17.030745+00 | ok     |        822 |       806 |
 ted       | 2026-09-11 22:34:45.461786+00 | ok     |        822 |         0 |
```

`items_seen` above `items_new` is the normal reading, not a fault: every source
re-reads a lookback window, so a second run in the same window sees the same notices
and takes none of them. Zero-yield health is counted on `items_seen` for exactly this
reason — a parser that has broken returns nothing at all, and a parser that works and
re-reads its window returns rows already stored.

## Add a source

A source is a YAML file, a fixture recorded from one real call, and a contract test
that reads the fixture. In that order, and the order is the point: **a parser is
never written against documentation memory.** The World Bank connector is the
standing argument for this: ten query parameters were probed against that API on
2026-09-11 and every one of them was accepted with an HTTP 200 and answered with the
whole unfiltered 418,561-row corpus. A connector written from the documentation would
have read a page of contract awards from anywhere on earth and reported a healthy
run. Nothing but a recorded response would have shown that.

The files are split across two lanes: `sources/*.yaml` is `source-onboarder`'s, and
the connector, the fixture and the contract test are `feed-connector-builder`'s
(CLAUDE.md, File ownership). One person doing all of it still does it in this order.

1. **`sources/<id>.yaml`, with `enabled: false`.** Copy `sources/worldbank.yaml` and
   read `Source` in `monitor/models.py` for what each key means; the model is strict,
   so an unknown key, a missing key or a wrong type fails the load rather than being
   ignored (rule 4). `id`, `name`, `country` (ISO alpha-2, or `EU`, or `multi` with
   `covers` listing the ISO codes), `admin_level`, `language`, `stream`, `access`,
   `connector`, `wave`, `schedule` (five-field cron, UTC, and the registry is
   authoritative for it), one of `list_url` or `api_url`, `tos_status`, `owner`, and a
   `health` block giving `expected_items_per_run` and `max_consecutive_failures`.
   `enabled: false` is the shipping state until the contract test passes; the comment
   on `worldbank.yaml`'s `enabled` line records the step that flipped it and why.
2. **Check the terms and robots.txt before writing any code** (rule 21). `tos_status`
   is a claim a named person makes: `reviewed_ok` means somebody read the terms. One
   polite pass per schedule, the identified user agent from `MONITOR_USER_AGENT`, no
   CAPTCHA solving, no rotating proxies, no third-party mirror of an official portal.
3. **Record the fixture from one real call.** `scripts/record_worldbank_fixture.py` is
   the pattern: a script per source, committed, that writes
   `tests/contract/fixtures/<id>.json` and prints the shape of what came back. Probe
   the parameters rather than trusting them, and write what you learned into the
   script's docstring — that docstring is the only record of what the API did on the
   day.

   ```bash
   MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
       uv run python scripts/record_<id>_fixture.py
   ```
4. **Write the connector and the mapper.** `monitor/connectors/<id>.py` subclasses
   `FeedConnector` and yields `RawNotice`; `monitor/normalise/<id>.py` maps one raw
   record to a `Notice`. The connector does not translate and the normaliser does not
   filter (rule 5). Deadlines are parsed by rule from the original text and never from
   a translation (rule 10).
5. **Register it** in `CONNECTORS` in `monitor/fetch.py`, which is the one place that
   maps a source id to its connector class and mapper.
6. **Write the contract test** as `tests/contract/test_<id>.py`, against the fixture
   and not against the network. `tests/contract/test_worldbank.py` shows what earns
   its place: not only that the parser reads the recorded fields, but that a corrupted
   row, a row from a country outside `covers` and an out-of-order page each raise.
7. **Run it, then enable it.**

   ```bash
   make test                        # ruff and the suite, including the new contract test
   # set enabled: true in sources/<id>.yaml, with a comment saying what made it true
   make seed                        # the registry row and a new config_versions row
   make fetch S=<id>                # one real pass
   make status                      # the new source's line, and its health state
   ```

A first pass that fetches nothing is a failure, not an empty success: check
`expected_items_per_run` against what the source really publishes before assuming
the connector is wrong.

## Change a keyword

The two lexicons are `config/lexicon_en.yaml` and `config/lexicon_fr.yaml`, phrases
grouped under the `function_id` they signal. They are config precisely so that this
is an edit and a re-seed rather than a deployment (rule 6).

```bash
# 1. edit the phrase list under the function it belongs to
$EDITOR config/lexicon_en.yaml

# 2. re-hash it into the database. make up does this too; make seed is the half of
#    make up that matters here.
make seed

# 3. note the new version
psql "$DATABASE_URL_READONLY" -c "
select version, applied_at from config_versions
where path like 'config/lexicon%' order by applied_at desc limit 4;"
```

Run on this checkout on 2026-09-12:

```
         version         |          applied_at
-------------------------+-------------------------------
 lexicon_fr-8422b89a82c1 | 2026-09-11 20:38:37.730427+00
 lexicon_en-63536d02c08f | 2026-09-11 19:26:35.225813+00
 lexicon_fr-835939ebe635 | 2026-09-11 19:26:35.225813+00
```

The version is the file stem and the first twelve hex characters of the sha256 of
its bytes, so it changes when the file changes and not when somebody remembers to
bump a number. Rows are never replaced: the French lexicon above has two rows and
the older one is the version the notices filtered before 20:38 were filtered under.
Write the new version into the change's commit message, because that is what makes a
drop rate measured on Friday comparable to one measured on Monday.

Three consequences to expect, all of them properties rather than surprises:

- **A keyword change also changes `prompt_version`.** The scoring system prompt is
  built from both lexicons, the function map, the system names and `thresholds.yaml`
  (`monitor/score/prompt.py`), and the version is a hash of the built text. So a
  one-word lexicon edit makes every score taken afterwards distinguishable from every
  score taken before it, which is the intended behaviour and the reason the golden
  set records a `prompt_version` beside every number.
- **It does not re-examine what was already dropped.** The filter reads notices at
  status `detected` with no `filter_result` (`monitor/filter/run.py`), so a new
  keyword applies to notices fetched afterwards. Re-filtering a dropped notice means
  resetting its status on the owner connection, which is a deliberate act with an
  argument behind it, not a routine step in this procedure.
- **A phrase only works in its own language.** The filter matches the notice's own
  text in the notice's own language (rule 9), so an English phrase does nothing for a
  French notice and vice versa. Add the phrase to both files or say why not.

## Roll back a prompt

`prompt_version` is a sha256 of the built prompt text, twelve hex characters. It is
not a number anyone bumps, so "rolling back" means restoring the text the version
was computed from. On this checkout the current version is `5cb6a4eb2de9`.

```bash
# 1. what changed the prompt, and when
git log --oneline -- monitor/score/prompt.py config/thresholds.yaml \
    config/lexicon_en.yaml config/lexicon_fr.yaml config/system_names.yaml

# 2. undo the change. A whole commit:
git revert <sha>
#    or one file back to how it was, when that commit did more than the prompt.
#    <sha> is the commit that changed it, so <sha>~1 is the version before:
git checkout <sha>~1 -- monitor/score/prompt.py

# 3. confirm the version is the one you meant to return to
uv run python -c "from monitor.score.prompt import prompt_version; print(prompt_version())"

# 4. if config changed too, re-seed so config_versions records the restored files
make seed

# 5. measure the restored prompt against the labelled set
make golden
```

Step 3 is not ceremony. The version is a hash of the *built* prompt, so reverting
`prompt.py` alone does not restore an earlier version if a lexicon or a threshold has
moved since: the version comes back only when everything it is built from comes back.
The printed value is the check on that.

**Scores already in the database are not re-scored and must not be.** Each `scores`
row carries the `prompt_version` that produced it, which is what makes two runs
comparable at all. Re-scoring a notice under the restored prompt is a deliberate
action with a cost and a cap behind it, not part of a rollback.

**`make golden` cannot give you a number today, and it says so rather than guessing.**
The 30 rows in `tests/golden/golden.csv` are unlabelled. Run on this checkout on
2026-09-12:

```
golden set not ready: golden.csv: 30 of 30 rows have no label. A person labels them
relevant or not; the pipeline does not label its own golden set.
```

Exit code 2. A person labels the column `relevant` or `not`, by hand, and then `make
golden` reports precision, recall and schema validity and appends a line to
`tests/golden/history.csv` carrying the model and the `prompt_version` with every
number. Until that labelling happens, a prompt rollback can be verified as a version
change — step 3 — but not as a quality change. The pipeline will not label its own
golden set, because a set labelled by the same family of model it measures scores
well against its own opinion and a real regression reads as agreement.

## Where the logs are

| What | systemd host | Compose workstation | Run by hand |
| --- | --- | --- | --- |
| Pass output: counts, log lines, tracebacks | `/var/log/monitor/run.log` | `logs/run.log` in the checkout | the terminal, and nowhere else |
| Status readings | `/var/log/monitor/status.log` | `logs/status.log` | the terminal |
| Whether a pass fired at all, and its exit status | `journalctl -u monitor-run.service` | `docker logs monitor-pipeline-cron` | your shell's history |

```bash
tail -f /var/log/monitor/run.log
systemctl list-timers 'monitor-*'            # last fire, next fire
journalctl -u monitor-run.service --since today
```

`journalctl` carries the unit's start, stop and exit status and **not** the command's
output, which is redirected to the files above. Finding no traceback in the journal
does not mean there wasn't one.

Three things that are not in any log file:

- **The audit trail is in Postgres, not in a log.** `events` rows carry what happened
  to a candidate, to an approved record and to a source, read at
  <http://127.0.0.1:8080/audit> or with SQL. A log line is an operational fact about a
  run; an `events` row is the record of something that happened to an entity a person
  may be asked about later. Do not look for one in the other's place. A notice's own
  history is not in `events`: its `status` and the `filter_result` sentence on the
  `notices` row are what explain why a reviewer never saw it.
- **Raw payloads are files, under `storage/<source_id>/`,** one per fetched notice,
  with a `notices_raw` row pointing at each. That is the record of what a source
  actually said, which is what you need when a parser and a portal disagree.
- **Request bodies to the model are never logged** (rule 20). `monitor/caps.py` logs
  the purpose, model, prompt version, token counts, cost and latency of every call and
  nothing about its content.

A note on the format, because CLAUDE.md describes something the code does not do yet.
Logging is meant to be structlog JSON; nothing in the repository calls
`structlog.configure`, so lines land in structlog's default console format:

```
2026-09-12 00:44:19 [info     ] stage_run   below_threshold=79 created=88 joined=22 staged=9
```

Greps written against JSON keys find nothing today. `grep -F '[error'` works on this
format; `grep -c '"level":"error"'` is the version to use once that configuration
lands, and neither works on both. Also: **no log rotation ships with the units.** Over
14 weeks of hourly passes `run.log` needs a logrotate snippet or a person with
`truncate`, and that decision has not been taken.

## Where the cost line is

Three places, for three questions.

**What has today cost so far, and how close is it to the cap?** One row per model
call in `model_calls`, and this is the query:

```bash
psql "$DATABASE_URL_READONLY" -c "
select purpose, count(*) as calls, round(sum(cost_usd), 4) as usd
from model_calls
where at >= date_trunc('day', now())
group by purpose order by purpose;"
```

Run on this checkout on 2026-09-12:

```
  purpose  | calls |  usd
-----------+-------+--------
 score     |    85 | 0.3281
 translate |   568 | 1.0361
```

The caps are `daily_call_cap` and `daily_usd_cap` in `config/thresholds.yaml` —
2,000 calls and USD 25 as of 2026-09-12, the call cap having been raised from 600
when one day of TED needed 1,132 translate calls. `DAILY_CALL_CAP` and
`DAILY_USD_CAP` in the environment override the file, which is how drill 3 sets the
cap to 2; a permanent value belongs in the file, where it has a config version.

**Both caps are checked before each call, never after** (rule 22, `monitor/caps.py`).
A capped run therefore makes no request and bills nothing: it commits what it had,
logs `score_cap_exceeded` or `translate_cap_exceeded` with how many notices were done
and how many were left, and exits non-zero. There is no `events` row for a stopped
run and that is deliberate — `events` is the audit trail for a notice, a candidate and
an approved record, and a run that ran out of budget is an operational fact carried by
the log and by the query above.

**What did this run cost?** `monitor score` and `monitor translate` each end with their
own cost line (`monitor/cli.py`), so the figure is in front of whoever ran the command:

```
considered <n>, scored <n>, parked <n>
schema validity <pct>
cost USD <amount>, prompt_version <12 hex characters>
```

The `prompt_version` is on that line rather than in a footnote because a cost per
notice is only comparable to another cost measured under the same prompt.

**What did one call cost, and can I check the figure?** Every `model_calls` row
carries `tokens_in`, `tokens_out`, `cache_read_tokens`, `cache_write_tokens`,
`latency_ms`, the model and the `prompt_version` beside the cost, so the cost can be
recomputed from the rate card in `config/thresholds.yaml`. Those two cache columns
exist because they had to: the four token counts the API reports are disjoint, the
first version of `cost_usd` subtracted the cached reads from `tokens_in`, and 28 of
the first 29 calls ever made on this deployment recorded a negative cost — which made
the day's spend read lower than it was and the USD cap more permissive than it was
configured to be. A cost line that can go negative is not a cost line. If a figure
here looks wrong, recompute it from the token columns before believing it.

## The six drills, and what each must do

`scripts/drills/` holds six scripts that break something on purpose on the live
system and print `PASS` or `FAIL` against the outcome claimed here. They are not
tests: `make test` proves these properties inside a process pytest controls and shows
a person nothing they can watch. `scripts/drills/README.md` is the long version,
including how each injects its failure and what it restores; this is the outcome to
expect.

```bash
set -a && . ./.env && set +a
uv run python scripts/drills/drill1_invalid_api_key.py
uv run python scripts/drills/drill2_renamed_field.py
uv run python scripts/drills/drill3_call_cap.py
uv run python scripts/drills/drill4_blank_reviewer.py
uv run python scripts/drills/drill5_pipeline_insert.py
uv run python scripts/drills/drill6_kill_export.py
```

Exit codes are three, not two: `0` PASS, `1` FAIL — a finding about the system, not
about the drill — and `2` the drill could not run and proved nothing either way (no
database URLs, another pipeline run in flight, today's model budget spent, or state in
the way that is not the drill's).

All six were run against this host's database over the night of 2026-09-11 into
2026-09-12 and all six passed; the table at the end of `scripts/drills/README.md`
records what each one showed, with the check counts. Drill 5 was re-run on 2026-09-12
while this section was written, and its output is quoted below. Re-run them after any
change to the roles, the grants, the caps, the export's write order or a connector's
required fields — those five are the things the six drills are about.

**Run 1, 2 and 3 on a quiet system.** They touch state a scheduled `monitor run`
moves under them — today's model calls, notices by status, one source's health row —
so all three refuse to start while another process holds a `monitor_pipeline`
connection. Stop the timer or wait for the pass to finish. Drills 4, 5 and 6 are safe
alongside a running review app.

### 1. An invalid API key mid-run

Seeds one notice at `filtered_in` and runs `monitor score --limit 1` with a
credential the API refuses. The request is real and comes back HTTP 401.

**Expect:** a non-zero exit with `anthropic.AuthenticationError` on the traceback, no
`scores` row, no `model_calls` row, and the notice still at `filtered_in`. No retry
and no `try/except` turned the refusal into a quiet skip (rules 2 and 3).

**The wording BUILD_ORDER uses for this drill is wrong and the system is right.**
Step 11 says "notices parked". In this codebase `status = 'parked'` means the model
answered twice and failed validation twice, which is a notice a person must look at. A
refused credential is not the notice's fault, so the notice stays at `filtered_in` and
the next run picks it up with no un-parking step for anyone to remember.

It needs a day with model budget left. The cap is checked before the client, so on a
spent day the stage stops at the cap and never reaches the credential — drill 3's
outcome, not this one's. The drill checks the budget first and exits 2 with the
numbers rather than reporting a failure it did not cause.

### 2. A renamed field in a fixture

Copies `tests/contract/fixtures/worldbank.json`, renames one required field in the
copy, serves the copy over loopback and runs the real connector against it. The
committed fixture is never written to.

**Expect:** three failed runs, each `worldbank: ValueError: ... is missing
['contact_organization']`; `consecutive_failures` 1, 2, 3 and the state walking
`healthy` → `watch` → `unhealthy`, `unhealthy` arriving at the source's own
`max_consecutive_failures`; no notice stored; **every other source's `source_health`
row byte-identical before and after**, and no other source run at all. The drill
restores the health row it read and deletes only the `fetch_runs` rows it created.

Three failed runs are three requests, not three requests with retries behind them
(rule 2). And a parse failure is a failed run rather than a zero-yield one: the two
counters are separate on purpose.

### 3. `DAILY_CALL_CAP=2`

Tops today's `model_calls` count up to the cap, seeds one notice at `filtered_in`,
runs `monitor score --limit 1`.

**Expect:** the run stops before the next call. `score_cap_exceeded` in the log,
`CapExceeded: score: 2 calls today, cap is 2` on the traceback, a non-zero exit, and
today's call count unchanged. **No 401 anywhere in the output** — the run carries a
deliberately invalid credential, so a 401 would prove the cap had let the call
through. No request was made, so nothing was billed.

**There is no `cap_exceeded` row in `events` and the drill checks that there is not.**
Step 11 calls this "a `cap_exceeded` event" and the event is a structlog line. If a
row in `events` is wanted, that is a decision to take about what `events` is for, not
a change to make to this drill.

### 4. Approve with a blank reviewer, through the form

Stages a candidate and posts the real decision form with the reviewer field set to
three spaces — whitespace rather than empty, because whitespace is what gets through a
check that does not strip.

**Expect:** HTTP 303 back to `/candidate/<id>?error=a reviewer name is required: no
candidate is approved by nobody`; the candidate still `pending_review`; no
`approved_records` row; no `approved` event. Then the same transition attempted
directly on a `monitor_review` connection raises `check_violation` from
`refuse_pipeline_decision` — which is what makes the form's message a message and
Postgres the enforcement (checkpoint 13, rule 14). Then the control: the identical
form with a name in it approves and writes the record, so the refusal was about the
name and not about a form that refuses everything.

### 5. Insert into `approved_records` as the pipeline

Connects through `monitor/db.py` as `pipeline` — the same way every pipeline module
gets a connection — and tries to insert an approved record, then to read the table.

**Expect:** both raise `InsufficientPrivilege: permission denied for table
approved_records`. The exception class matters: a `ForeignKeyViolation` would mean the
insert had got past the grant and been stopped by the table's shape instead, so the
drill uses a candidate id that does not exist. The control is `monitor_review` reading
the same table in the same statement, successfully.

Run on this checkout on 2026-09-12: `PASS drill 5: the pipeline cannot write, or even
read, an approved record (5 checks)`.

### 6. Kill the export halfway

Approves two records, holds a row lock so the export blocks at the stamp, waits until
`pg_locks` shows that backend waiting, sends SIGKILL, releases the lock.

**Expect, and it is a disjunction because the guarantee is one:** either a complete
CSV with a manifest whose sha256 matches the file, or no file at all — and in both
arms no `export_batches` row and no record with `exported_at` or `export_batch` set.
Aimed at the window after the rename, the arm that shows up is the orphan: a complete
`B00nn/` directory named by no batch row.
**That orphan is the residue this design accepts**, and it is the right way round: an
orphan file is visible, verifiable against its own sha256 and discardable, while a
record stamped as exported for a file nobody has would be invisible and
unrecoverable, because that record would never be selected again.

**What this drill does not cover, stated because a check that cannot fail reads as
coverage.** The drill aims its kill by holding a row lock so the export blocks on its
next SQL statement, which is the stamp — and the rename that publishes the batch has
already happened by then. So at that point no `.part` staging directory *could* be
left behind, and the drill reports that rather than asserting it.

The window where a `.part` can be orphaned is between the two staging writes in
`review/export.py:write_files`, and there is no SQL statement between them to aim at.
A timed kill would hit it rarely and prove nothing on the runs where it missed, which
is exactly what this drill's aiming design rejects. **So an orphaned `.part` directory
is a known uncovered case.** If one appears in the export directory, it is a crash
between those two writes; it is safe to delete, because no batch row or stamp can
exist for it, and `mkdir` without `exist_ok` means the next export of that same batch
id would refuse rather than write into it.

The drill then exports the same range again to completion, and the new batch id is one
higher than the killed attempt's. A sequence does not roll back, so **a gap in the
B-series is an export that did not finish** — including this drill's own two runs,
which is why the B-series on a database the drills have been run against is not a
clean count of real exports.

## Refusals you will meet, and what each one means

Every line below is the system working. None of them is worked around; each has one
correct response.

| Message | What it means | What to do |
| --- | --- | --- |
| `DATABASE_URL_PIPELINE is not set` | The environment was not loaded, or was loaded in another shell | `set -a && . ./.env && set +a` |
| `unknown role 'readonly'` | Something asked `db.connect` for a reporting connection | Reporting is `psql "$DATABASE_URL_READONLY"`; there are two runtime roles (rule 11) |
| `permission denied for table approved_records` | Pipeline code reached the reviewer's table | Check which URL the process was given. This refusal is checkpoint 11 doing its job |
| `no model credential on the direct route` | `ANTHROPIC_API_KEY` is absent and `MODEL_ROUTE=direct` | Put the key in the environment file, or set `MODEL_ROUTE=proxy` where the platform holds it |
| `CapExceeded: score: N calls today, cap is N` | Today's budget is spent; nothing was billed | Wait for tomorrow, or change the cap in `config/thresholds.yaml` deliberately and out loud |
| `a reviewer name is required` | An approval arrived with no name | Type the name. No candidate is approved by nobody (checkpoint 13) |
| `a rejection reason is required` | A rejection arrived with no reason | Pick one from the list. It is enforced server-side and in Postgres (checkpoint 14) |
| `the range ends before it starts` | The export's two dates are the wrong way round | Both dates are inclusive approval days |
| `N records were selected and M could be stamped` | Two exports raced for the same records | The batch rolled back; its file names a batch that does not exist. Discard the orphan and run it once |
| `golden set not ready` | The 30 golden rows are unlabelled | A person labels them; the pipeline will not label its own set |
| `worldbank: ValueError: ... is missing [...]` | A source changed its fields | Re-record the fixture, read the diff, fix the parser. Do not add a second selector (rule 1) |

## Measurements

Recorded as each step's acceptance test produced them. Never edited afterwards:
a number that moves gets a new row with its date, so the trend is visible.

### Free filter drop rate

| Date | Source | Matched | Fetched | Considered | Passed | Dropped | Drop rate | Held for translation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-11 | ted | 1,449 | 50 | 9 | 0 | 9 | 100% | 41 |
| 2026-09-11 | ted (paged) | 1,449 | 1,449 | 270 | 33 | 237 | 88% | 1,132 |
| 2026-09-12 | fts | 25 | 25 | 25 | 2 | 23 | 92% | 0 |
| 2026-09-12 | prozorro | ~2,000/day | 400 | 380 | 0 | 380 | 100% | 10 |
| 2026-09-12 | ted (awards excluded) | 822 | 822 | 176 | 21 | 155 | 88% | 630 |

The last TED row is after two decisions taken on 2026-09-12: award and post-award
notice types are excluded at the query, and the daily call cap moved from 600 to
2,000. Together they turned an unreachable backlog into a reachable one.

| | before | after |
| --- | --- | --- |
| TED notices matched per two-day window | 1,449 | 822 |
| held for translation | 1,132 | 630 |
| daily call cap | 600 | 2,000 |
| over or under the cap | 532 over | 1,370 under |

The stored TED notices were cleared and re-fetched under the new query, because the
old ones included about 600 award notices that would each have cost a translate
call to discover we did not want them. Nothing downstream had consumed them: no
scores, no candidates, no model translations.

A side effect worth knowing: 198 of 250 recorded notices now carry a deadline,
against 16 of 50 before. Contract notices have deadlines and award notices do not,
so excluding awards directly improves clustering, because the deduper needs two
known deadlines to join on a title.

Three sources, 96% dropped overall for the price of zero model calls.

**Prozorro is what the CPV stage is for.** 380 of its 390 notices were dropped on
their classification alone: tyres, fuel, food, vehicle parts, the ordinary goods
procurement of a country at war. None of it reached a model and none of it cost
anything. It is also why the Prozorro connector filters the change feed on status
before fetching detail: about 2,000 tenders are modified a day, only 9% are open to
a bidder, and a detail record is 110 KB.

**Prozorro's run hit its ceiling.** 400 details is `expected_max` in
`sources/prozorro.yaml`, so this is a sample of the day rather than the day. Unlike
TED, the ceiling is a real limit rather than a safety net, because the only way to
see a tender's subject is to fetch it in full. Raising it raises the request count
one for one.

The second row is the same query after paging was fixed. Read both carefully,
because the headline number is the least useful part of either.

**Paging changed the answer from nothing to something.** Reading one page of 50
produced zero candidates. Reading all 1,449 produced 33. Those 33 are the first
real material the scorer will ever see, and the first run would have reported a
clean day and found none of them.

On the 50-notice first row:

**41 of 50 never reached the lexicon at all.** They are in German, Polish, Finnish,
Croatian, Spanish, Dutch and Italian, and there are only two lexicons, English and
French. Those notices are held at `needs translation` with status `detected`, not
dropped, and step 14 is what releases them. Until then the free filter can only
decide about the English and French share of TED, which on this day was 9 notices
out of 50.

**Of the 9 it could decide about, it dropped all 9, and all 9 were right.** Data
centre co-location, office furniture, advertising and marketing, security
guarding, event organisation, corporate hospitality. No false negative in the
sample: nothing dropped looks like a PFM opportunity.

**Zero passed, and zero would have passed even with TED's free English titles.**
TED supplies an English title for 47 of the 50 (see `docs/open_decisions.md` item
8), so the obvious idea is to match the English lexicon against those and rescue
the 41. Measured: it rescues none. This day's 50 TED notices contain no public
financial management opportunity at all. That is a plausible result rather than a
broken lexicon, and it is why the drop rate is not yet evidence about precision.

**The sample was 3.5% of what matched.** Fixed: the connector now pages to the end
of the result set, six requests at the API's maximum page size of 250.

## What the full read showed

**1,132 notices are held for translation against a 600-call daily cap.** One
translate call each, so a single day's TED exceeds the cap by 532 calls. The cap
stops the run with an event, which is correct behaviour and not a bug, but it
means TED alone consumes the whole day's model budget and the backlog grows. Cost
is not the constraint: 1,132 calls is about USD 4.75 against a USD 25 cap. The
call count is. See `docs/open_decisions.md` item 11.

**The free filter's precision is poor, and now there is evidence.** The phrases
doing the passing are dominated by generic terms: `establishment` (6 notices),
`recruitment` (4), `recrutement` (3), `gouvernance` (2), `compliance` (2),
`conformité` (2). What passes includes 360-degree feedback consultancy, job
grading methodology, temporary staffing and graphic design. Genuinely PFM
notices are in there too - a payroll tool, an audit mandate, grant management -
but they are the minority. This is the free filter doing its job, which is to cut
cost rather than to be precise, and the scorer is what judges relevance. It is
also exactly the evidence step 19's tuning pass needs, recorded now while it is
cheap to gather. See `docs/open_decisions.md` item 12.

**42% of what we page is already decided.** Of the 1,449, `can-standard` 541,
`can-modif` 51, `can-social` 17 and `veat` 18 are award and post-award notices for
tenders that are finished. `cn-standard` 741 and the `pin-*` 44 are the
opportunities. Narrowing the query would nearly halve the volume and the
translation backlog with it, but whether an award notice is worthless is a BD
question - it names the winner, which is competitor intelligence. See
`docs/open_decisions.md` item 13.

### Scoring

| Date | Notices scored | Schema validity | Cost | prompt_version |
| --- | --- | --- | --- | --- |
| not yet run | - | - | - | `5cb6a4eb2de9` |

Superseded on 2026-09-12; see Corrections below.

Step 6's acceptance asks for a real scoring run above 95% schema validity. It has
not been run: there is no `ANTHROPIC_API_KEY` in the environment the pipeline was
built in, so no model call has been made at any point. See
`docs/open_decisions.md` item 10.

Everything else is ready for it. 33 notices sit at `filtered_in`, the system
prompt is about 3,600 tokens at version `5cb6a4eb2de9`, and the run is costed at
USD 0.006 for the first call and USD 0.003 for each one after it, since the prompt
block is marked for caching and is identical for every notice. The whole run is
about **USD 0.09 and 33 of the 600 daily calls**. Cost is not what is stopping
this.

To run it: put the key in `.env`, then `make score`. Record the four numbers
above, and keep the `prompt_version`, because a score is only comparable to
another score made under the same prompt.

### Golden set

| Date | Labelled | Scored | Precision at 60 | Recall | Schema validity | Mean cost | prompt_version |
| --- | --- | --- | --- | --- | --- | --- | --- |
| not yet run | 0 of 30 | - | - | - | - | - | `5cb6a4eb2de9` |

Still true on 2026-09-12; see Corrections below.

`tests/golden/golden.csv` holds 30 real TED notices with an empty label column.
Two things have to happen before there is a number, and they are independent:

1. **A person labels the set.** `relevant` or `not`, one per row, by hand. The
   pipeline will not do it and `make golden` refuses to run until it is done. A
   golden set labelled by the same family of model it measures scores well against
   its own opinion, and a real regression reads as agreement.
2. **A model credential exists**, as for steps 6 and 14.

The 30 are what the free filter passed, which is the population the scorer is
actually given. Reading them is a fair warning about what the filter currently
lets through: alongside asset management and payroll systems there is temporary
staffing, 360-degree feedback consultancy and graphic design. Expect a low first
precision, and expect that to be the honest starting point rather than a problem
with the harness. `docs/open_decisions.md` item 12 has the evidence.

To run it: `make golden-export` writes the file, a person labels it, `make golden`
reports and appends a line to `tests/golden/history.csv`. The line carries the
`prompt_version` and the model with every number, because a score is only
comparable to another made under the same prompt.

### Candidates

None yet. `make stage` has nothing to do until notices reach `scored`, which is the
scorer's output and therefore waits on the model credential. The dedupe and staging
logic is tested against real database rows (`tests/unit/test_stager.py`), including
the cap: the sixteenth candidate from one source on one day stays at `scored` and
keeps its score.

`make run` chains the whole pass and currently stops at the scorer, after fetching
1,449 notices and filtering them, which is the intended behaviour rather than a
failure: each stage commits its own work, so a pass that stops keeps everything
before the stopping point and the next pass resumes there.

### Corrections, 2026-09-12

The two "not yet run" rows above were true when they were written and are not true
now. They stay as written, because a measurement is not edited after the fact; this
is what changed.

**A model credential arrived, and real calls have been made.** `MODEL_ROUTE=direct`
with a key in `.env` on this host. Read on 2026-09-12 with the cost query in "Where
the cost line is": 85 score calls for USD 0.33 and 568 translate calls for USD 1.04
on that day alone, against a 2,000-call, USD 25 cap. So "there is no
`ANTHROPIC_API_KEY` in the environment the pipeline was built in" no longer describes
this deployment, and `docs/open_decisions.md` item 10 is closed by events rather than
by a decision. What has still not happened is the acceptance run itself: nobody has
recorded the four numbers step 6 asks for — notices scored, schema validity, cost and
`prompt_version` — as one measured pass. The calls in `model_calls` are the by-product
of building, not that measurement.

**Candidates exist.** `monitor stage` was run on 2026-09-12 and reported 110 scored
notices, 88 candidates created, 22 joined, 9 staged, 0 held by the per-source cap and
79 below the 60 threshold. Those 9 are the first candidates ever to reach the review
queue; all 9 are TED, all in the Europe region, scoring 68 to 72. The "None yet" note
above is superseded.

**The golden set is still unlabelled**, which is the one thing in this section that has
not moved. `make golden` exits 2 with `30 of 30 rows have no label`, verified on
2026-09-12. Nothing in the pilot has a precision or recall number yet, for any prompt
version.

## Known gaps

Everything here is a real gap, not a rough edge. Each one is stated because an
operator who assumes otherwise will be wrong at a bad moment.

- **`monitor status` prints two of the four readings it advertises.** Source health and
  the filter's arithmetic, yes; today's calls and cost, queue depth and export backlog,
  no. "Read health" above says where those three actually live.
- ~~**Nothing in the fetch stage reads a source's `schedule`.**~~ FIXED 2026-09-12.
  `monitor/schedule.py` reads it and `fetch all` skips any source already read since
  its schedule last fired. The registry's windows are now what the pipeline obeys
  rather than a statement of intent. What remains: the supported cron subset is a
  fixed minute and hour with an optional day-of-week, and anything else - a step like
  `*/4`, a list of hours, a day-of-month - raises rather than being approximated, so a
  future source needing one of those needs that module extended first.
- **Logging is not JSON yet.** `structlog.configure` is never called, so lines land in
  the console format shown above and greps written against JSON keys find nothing.
- **No precision or recall number exists for any prompt version**, because the golden
  set is unlabelled. A prompt change can be verified as a version change and not as a
  quality change until a person labels those 30 rows.
- **No log rotation ships with the timers.** Fourteen weeks of hourly passes will need a
  logrotate snippet or a person with `truncate`.
- **`make up` assumes Postgres is in Compose.** The pilot host and this checkout run it
  natively, where `make migrate && make seed` is the equivalent and `make up` fails at
  `docker compose up`.
- **Re-exporting a named batch is not implemented** and is step 22's work. A record is
  exported once; until then, a batch that needs reading again is read from disk and
  checked against the sha256 in its manifest.
- **`monitor stage`'s `joined` count is not yet a count of cross-source duplicates.**
  Measured on 2026-09-12: the run reported 22 joined, and `candidate_notices` gained one
  link, not 22. Exactly 22 notices carry two `scores` rows each, and `SELECT_SCORED` in
  `monitor/stage/stager.py` joins `notices` to `scores` without picking one score per
  notice — so a twice-scored notice is processed twice, matches the candidate it created
  on its first pass, and is recorded as a duplicate join of itself. The staged nine are
  unaffected and no candidate is wrong; the number in the run line is. Read cross-source
  clustering off `candidate_notices` or the `/audit` page rather than off that count
  until this is settled, and treat the second `scores` row as the thing to explain
  first — something wrote it.
