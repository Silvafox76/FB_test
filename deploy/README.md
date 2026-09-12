# Scheduler

Three commands on a clock: `monitor run` (one full pass, hourly), `monitor status`
(the reading, hourly at :30) and `monitor metrics` (the week's numbers, Monday at
06:30 UTC), each appending to its own log file. Two ways to run them, because the
pilot host and the demo workstation are different machines:

| Machine | Mechanism | Files |
| --- | --- | --- |
| Pilot host (Ubuntu 24.04, systemd 255) | three service/timer pairs | `deploy/systemd/*.service`, `*.timer` |
| Workstation with Docker and no systemd | three loop containers | `deploy/docker-compose.cron.yml` |

They are the same commands at the same wall-clock times on purpose. When a pass on
one machine looks different from a pass on the other, the scheduler is not the
reason.

Nothing here opens a port, listens on anything or accepts a delivery (rule 17). A
timer runs a command; that is the entire mechanism. The review app is started by a
person with `make review` and is bound to 127.0.0.1 by the Makefile, not by
anything in this directory.

## Which schedule is authoritative

`sources/*.yaml` holds each source's `schedule` and **the registry is
authoritative for it** (`monitor/models.py`, `Source.schedule`; BUILD_ORDER step
11). The timer files do not contain those values and must not grow them: two
places holding a schedule means one of them is wrong by Wednesday, and it will be
the one nobody reads.

What the timer decides is only **how often the host wakes the pipeline**. Which
sources a wake should read belongs to the registry, and since 2026-09-12 that is
literally true rather than an intention: `monitor/fetch.py` asks each enabled source
whether it has been read since its own `schedule` last fired and skips the ones that
have. A wake that reads nothing prints `not due: ...` rather than `seen 0`, so an
idle pass and a broken one do not look alike in the log.

Read the registry rather than trusting a table in a document:

```bash
uv run python - <<'PY'
from monitor.registry.load import load_sources
for s in sorted(load_sources(), key=lambda s: s.id):
    print(f"{s.id:10} {s.schedule:12} enabled={s.enabled}")
PY
```

Run on 2026-09-11 it printed four daily windows, all UTC: `ted` 06:00, `prozorro`
06:30, `fts` 07:00, `worldbank` 08:00, all four `enabled: true` now that the World
Bank contract test has landed. Every window is UTC, which is why both timers pin
`OnCalendar` to UTC explicitly and why the container loops align to the container
clock (the image has no TZ set, so it is UTC too).

### Two regimes, and the gap between them

**This weekend: hourly.** BUILD_ORDER step 11 asks for hourly `monitor run` and
`monitor status` so the Sunday demo has notices arriving during it. That is what
the shipped timers do. An hour is a defensible reading of "one polite pass per
schedule" (rule 21) for the length of a demo and no longer.

**From Monday: the registry's windows.** Hourly stops being defensible and the
cadence comes from `sources/*.yaml`. Switch by generating a drop-in from the
registry — see below — not by editing the timer.

**The gap, stated plainly because it is easy to assume it is closed:** `monitor
run` fetches *every enabled source* on every wake (`monitor/cli.py` →
`monitor/fetch.py`). Nothing in the code reads `Source.schedule` yet. So N wakes a
day means N passes per source a day, whatever the registry says. Until the fetch
stage learns to skip a source outside its window, the Monday drop-in is **one wake
a day, at the latest window the registry declares**: every source is then read at
or after the time it asks for, and read exactly once. When per-source window
selection does land, regenerate the drop-in with one `OnCalendar` line per
distinct window (same command, without the `max`), because each wake will by then
read only the sources whose window it is.

## Install the timers on the pilot host

Conventions this file sets, to be matched by step 12's Terraform rather than
invented again there: checkout at `/opt/monitor`, service account `monitor`,
environment file at `/etc/monitor/monitor.env`, logs in `/var/log/monitor/`.

```bash
# 1. Service account and checkout. The account owns the checkout because `uv run`
#    syncs the environment in it and the fetch stage writes storage/.
sudo useradd --system --create-home --shell /usr/sbin/nologin monitor
sudo install -d -o monitor -g monitor /opt/monitor
sudo -u monitor git clone <repo> /opt/monitor

# 2. uv, system-wide, so the unit's absolute ExecStart path is real.
#    If `command -v uv` says anything other than /usr/local/bin/uv, edit
#    ExecStart in both .service files to match. systemd needs an absolute path
#    and will not search PATH.
sudo sh -c 'curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh'

# 3. The environment file (see the next section).
# 4. The units.
sudo install -m 0644 /opt/monitor/deploy/systemd/monitor-*.service /etc/systemd/system/
sudo install -m 0644 /opt/monitor/deploy/systemd/monitor-*.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now monitor-run.timer monitor-status.timer monitor-metrics.timer
```

Enable the **timers**, not the services. The `.service` units have no `[Install]`
section: enabling a timer-driven service would run it once at boot and never
again, which looks like a working scheduler for about a day.

Take one pass by hand before trusting the clock:

```bash
sudo systemctl start monitor-status.service   # fast, read-only, proves the env file
sudo systemctl start monitor-metrics.service # proves the SECOND URL in the env file
sudo systemctl start monitor-run.service     # the real thing
```

Start `monitor-metrics.service` by hand even though the timer will get to it on
Monday, and start it before you trust the installation. It is the only unit here
that needs **two** database URLs, so it is the only one that proves the env file
carries `DATABASE_URL_READONLY` as well as `DATABASE_URL_PIPELINE`. Rule 11 is
what makes it two: collection reads the export backlog out of `approved_records`,
which `monitor_pipeline` has no privilege on at all, so it reads as
`monitor_readonly` and writes its row as `monitor_pipeline`. A missing readonly URL
fails on the one number the week 14 gate most wants, and it fails on Monday
morning rather than on the day you installed it.

## The environment file

`/etc/monitor/monitor.env`, mode 0600, owned by `monitor`. **It is not in this
repository and never will be** (rule 20). It is written by hand on the host from
`.env.example`, which lists every key and says what each is for:

```bash
sudo install -d -m 0750 -o root -g monitor /etc/monitor
sudo install -m 0600 -o monitor -g monitor /dev/null /etc/monitor/monitor.env
sudo -e /etc/monitor/monitor.env    # paste the keys from .env.example and fill them in
```

It holds the three database URLs with their passwords, `MODEL_ROUTE` and the model
credential, the user agent (rule 21) and the daily caps (rule 22). At step 12 the
same keys come from Secrets Manager on the EC2 host and this file becomes the
thing the instance profile renders, not the thing a person edits.

Two parser details that cost an evening if you meet them by surprise:

- systemd's `EnvironmentFile` is **not a shell**. No `export`, no `$OTHER_VAR`
  expansion, no command substitution, no `#` comment on the end of a value line.
  Whole-line `#` comments and double-quoted values with spaces are fine, which is
  what `MONITOR_USER_AGENT` needs.
- The `DATABASE_URL_*` hosts differ between deployments: `localhost` where
  Postgres is native on the pilot host, `postgres` inside Compose. The URL is the
  only thing that chooses the database role (rule 11), so check it is the pipeline
  role's URL before wondering why a permission was denied.

`monitor-metrics.service` is the one unit that needs **two** of these URLs, and the
split is rule 11 rather than an oversight: the export backlog lives in
`approved_records`, which `monitor_pipeline` has no privilege on, so collection
reads as `monitor_readonly`; the row it writes goes to `metrics`, which the
reporting role has no insert on, so the write is `monitor_pipeline`. Both are in
the same file and `monitor/cli.py` opens them in sequence. Neither role is granted
the other's privilege to make one connection do.

No unit here references the review role's URL, and none should: the review app is
the only thing that connects as `monitor_review`.

## Did they fire, and where is the log

```bash
systemctl list-timers 'monitor-*'            # last fire, next fire, and for which unit
systemctl status monitor-run.service         # exit status of the last pass
journalctl -u monitor-run.service --since today
```

`journalctl` shows the unit's start, stop and exit status **and nothing else**: the
commands' own output is redirected to files with `StandardOutput=append:`, so the
journal stays a record of whether a pass ran and the files are the record of what
it did. Looking for a traceback in `journalctl` and finding none does not mean
there wasn't one.

| What | systemd host | Compose workstation |
| --- | --- | --- |
| Pass output (structlog lines, counts, tracebacks) | `/var/log/monitor/run.log` | `logs/run.log` in the checkout |
| Status readings | `/var/log/monitor/status.log` | `logs/status.log` |
| Weekly metrics reports | `/var/log/monitor/metrics.log` | `logs/metrics.log` |
| Fired / did not fire, exit status | `journalctl -u monitor-run.service` | `docker logs monitor-pipeline-cron` |

One difference between the two mechanisms worth knowing before Monday.
`monitor-metrics.timer` sets `Persistent=true`, so a host that was down over Monday
06:30 takes the run when it comes back; the Compose loop has no equivalent and
simply waits for the next Monday. The two hourly schedules do not catch up on
either mechanism and should not: a status reading replayed at 09:00 would describe
09:00, which the 09:30 tick describes anyway. A week's numbers are work rather than
a reading, which is what makes the difference deliberate. Either way `make metrics`
takes a missed run on demand, and the window is relative to when the job runs, so a
run taken on Tuesday reports the seven days to Tuesday and the page says so.

```bash
tail -f /var/log/monitor/run.log
grep -F '[error' /var/log/monitor/run.log          # see the note below before trusting this
```

A note on the format, because it is not what CLAUDE.md describes yet. Logging is
meant to be structlog JSON, but nothing in the repository calls
`structlog.configure` today, so lines land in structlog's default console format -
`2026-09-11 23:19:58 [info     ] fetched   run_id=r1 source_id=ted` - with no
colour codes once stdout is a file (checked by redirecting one). Greps written
against JSON keys will find nothing until that configuration lands, at which point
this grep becomes `grep -c '"level":"error"'` and the line above stops working.
Whichever it is, one file per command holds the whole pass, and both streams are in
it in the order they happened.

`/var/log/monitor` is created by systemd (`LogsDirectory=monitor`) before the
first run; nothing needs to pre-create it. **No log rotation is shipped with these
units.** Over a 14-week pilot at hourly, `run.log` will need either a logrotate
snippet or a person with `truncate`; that decision is not made here.

## Switching to the Monday per-source windows

**Read this before generating anything: as of 2026-09-12 you probably do not need
to.** This section exists because the fetch stage ignored `Source.schedule` and read
every enabled source on every wake, so the only way to respect a source's window was
to make the host wake at that window. `monitor/schedule.py` now does it properly -
`monitor fetch all` reads only the sources that have not been read since their own
schedule last fired, and reports the rest as `not due` - so **an hourly timer plus
the registry already gives every source its declared cadence**, and the generated
drop-in below buys nothing except a later first pass each day.

Keep the hourly timer. Generate this only if a host has a reason to stay asleep,
such as a metered connection or an instance that is stopped overnight to save money.

Generate the drop-in from the registry. Do not type the times: the point of the
registry being authoritative is that changing a source's `schedule` and
regenerating is the whole procedure.

```bash
sudo install -d /etc/systemd/system/monitor-run.timer.d
cd /opt/monitor
uv run python - <<'PY' | sudo tee /etc/systemd/system/monitor-run.timer.d/10-registry-windows.conf
from monitor.registry.load import load_sources

windows = set()
for s in load_sources():
    if not s.enabled:
        continue
    minute, hour, dom, month, dow = s.schedule.split()
    if (dom, month, dow) != ("*", "*", "*") or not (minute.isdigit() and hour.isdigit()):
        raise SystemExit(f"{s.id}: schedule {s.schedule!r} is not a plain daily window; read it by hand")
    windows.add((int(hour), int(minute)))

# One wake, at the latest window any enabled source declares, so every source is
# read at or after the time it asks for and read once (rule 21). When the fetch
# stage learns to skip a source outside its window, emit one line per window here
# instead of the max.
hour, minute = max(windows)
print("# Generated from sources/*.yaml on the host. Regenerate after changing a schedule.")
print("[Timer]")
print("OnCalendar=")                                    # clears the hourly weekend value
print(f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00 UTC")
PY

sudo systemctl daemon-reload
systemctl list-timers 'monitor-run*'          # NEXT should now be tomorrow, not the next hour
systemd-analyze calendar "$(awk -F= '/^OnCalendar=./{print $2}' /etc/systemd/system/monitor-run.timer.d/10-registry-windows.conf)"
```

The empty `OnCalendar=` line matters: without it the drop-in *adds* a window to
the hourly one instead of replacing it, and you get both.

Run on 2026-09-11 this yields `08:00 UTC`, `worldbank` being the latest enabled
window. It yielded `07:00 UTC` earlier the same afternoon, when `worldbank` was
still `enabled: false` and `fts` was the latest — the answer changed under this
document in the hours it took to write it, which is the whole argument for
generating the line instead of typing it.

`monitor-status.timer` does not change. It reports on the database rather than on
any source, so nothing in `sources/*.yaml` has an opinion about it, and an hourly
reading is most wanted on exactly the morning the daily pass produced nothing.

To go back to hourly for the next demo, delete the drop-in — the shipped timer was
never edited:

```bash
sudo rm -r /etc/systemd/system/monitor-run.timer.d
sudo systemctl daemon-reload
```

## The workstation alternative, without systemd

`deploy/docker-compose.cron.yml` is an overlay on `docker-compose.yml`, not a
standalone file. Run it from the repository root, in this file order — Compose
resolves relative paths against the first `-f` file's directory, so `./logs` is the
checkout's `logs/` and not `deploy/logs/`:

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.cron.yml \
    up -d pipeline-cron status-cron metrics-cron

tail -f logs/run.log

# Stop the two loops and leave Postgres and the review app running, which is what
# you want mid-demo. `stop` takes service names on every Compose version; `down`
# without them takes the whole project with it.
docker compose -f docker-compose.yml -f deploy/docker-compose.cron.yml \
    stop pipeline-cron status-cron
```

Each service is a `sleep`-to-the-next-boundary loop: `pipeline-cron` runs a pass on
the hour, `status-cron` takes a reading at :30, both appending to the same log file
names the systemd host uses. There is no cron daemon in the image, because a second
way to schedule the same command is a second place to look when a pass did not
happen (rule 1).

Three things to expect:

- **Starting it does not take a pass.** The loop sleeps to the next boundary first,
  the same way enabling a timer schedules a pass rather than taking one. `make run`
  takes one now.
- **It does not come back after a reboot.** `restart: "no"` is deliberate: Docker's
  restart backoff is a retry loop wearing a different hat (rule 2), and a scheduler
  that cannot start is a failure a person must see. Start it again by hand.
- **`stop` can take up to a minute.** `pipeline-cron` has a 60-second grace period
  so a fetch in flight can finish politely. Being killed mid-pass costs nothing that
  matters: each stage commits its own work and the next pass resumes from there.

## What this scheduler deliberately does not do

- **It does not retry** (rule 2). No `Restart=`, no `RestartSec=`, no Docker restart
  policy. A failed pass exits non-zero, marks the source unhealthy and waits for the
  next tick.
- **It does not notify anyone** (rule 18). No mail, no webhook, no `OnFailure=` unit
  that pages someone. The failure is in the log file, the exit status and `monitor
  status`; the reviewer works the queue on a schedule they set.
- **It does not listen** (rule 17). No socket unit, no published port, no inbound
  path of any kind.
- **It holds no secret** (rule 20). Every credential arrives through a file outside
  the repository, and no command here logs a request body.
