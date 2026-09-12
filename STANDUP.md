# Standing it up

Two paths. They differ in one thing only — who runs Postgres — and the rest of the
pipeline is identical.

**Read this first, because it decides which path you want.** Path A is the one that has
actually been run end to end, on live sources, with a real model credential: 2,118
notices, 662 translated, 221 scored, 12 candidates on the review queue. Path B is the
intended deployment shape and **has never been executed**, because the environment this
was built in has no Docker daemon — Postgres ran natively. Path B is a careful reading,
not a demonstration. If you want it working in the next twenty minutes, take Path A.

Neither path needs AWS. Step 12's Terraform is roughed in and not required to run this.

---

## What you need either way

- **Python 3.12** and [`uv`](https://docs.astral.sh/uv/). `uv` installs its own Python if
  yours is a different version.
- **An Anthropic API key with credit on it.** Scoring and translation are model calls.
  A full pass over the current source set costs about **USD 2.20**; the pipeline refuses
  to start a call that would take the day over 2,000 calls or USD 25, checked before each
  call rather than after.
- **Outbound HTTPS**, plus port 80 — several West African portals still lack TLS.
- About **1 GB** of disk for the database and raw notice storage.
- **`poppler-utils`**, for `pdftotext`. Burkina Faso publishes its notices as a weekly
  PDF bulletin, and the pipeline measures a PDF's text layer before deciding whether
  the issue needs OCR at all. Without the binary every bulletin measures as a scan.
  `apt-get install poppler-utils`, or `brew install poppler`. The Docker image
  installs it itself.

You do **not** need a browser. Two or three portals render their notice rows with
JavaScript and those run in their own container (`browser` in `docker-compose.yml`),
which is the only image in the project carrying a Chromium. Everything else is httpx
and selectolax, and Path A below skips the browser sources rather than requiring one.

You do not need a CRM, a Zoho account, or any inbound network path. The review app binds
to localhost and nothing listens for anything.

---

## Path A — native Postgres (this is the proven one)

You need PostgreSQL 16 running locally and a superuser you can create roles with.

```bash
git clone https://github.com/Silvafox76/FB_test.git && cd FB_test
cp .env.example .env
```

Now edit `.env`. Four things:

1. Set `POSTGRES_PASSWORD` and the three `MONITOR_*_PASSWORD` values to anything you
   like. They are the passwords for the pipeline, review and reporting roles, and
   `monitor/migrate.py` applies them to the database on every run so the file and the
   database cannot drift apart.
2. Replace `CHANGE_ME` in each of the four `DATABASE_URL_*` lines with the matching
   password you just set.
3. Put your key in `ANTHROPIC_API_KEY` and leave `MODEL_ROUTE=direct`.
4. Put a real contact address in `MONITOR_USER_AGENT`. Every source is read with an
   identified user agent carrying a reachable address — that is an acquisition-ethics
   requirement, not a formality.

Leave `DAILY_CALL_CAP` and `DAILY_USD_CAP` commented out. The caps live in
`config/thresholds.yaml`; those two variables override it and exist for one failure
drill. A stale value left in `.env` is how the enforced cap silently stopped matching
the configured one, and killed a translation run 545 calls in.

Then create the database and run it:

```bash
createdb monitor                       # as a superuser
psql monitor -c "CREATE ROLE monitor_owner LOGIN SUPERUSER PASSWORD 'the-one-you-set'"

make migrate                           # 10 migrations, applied once each
make seed                              # loads sources/*.yaml and config/*.yaml
make test                              # 621 pass, 1 skipped; takes about 20 seconds
```

`make test` is worth running before anything else. It includes the checkpoint tests,
which assert that the database refuses the pipeline role the privileges this whole design
depends on it not having. If those fail, stop — nothing downstream is trustworthy.

Then a full pass. Run the stages separately the first time, because each one tells you
something and the costs differ sharply:

```bash
make fetch S=ted        # free. ~800 notices, one polite pass
make filter             # free. CPV and lexicon; drops roughly three quarters
make translate          # model calls. ~USD 0.0012 each
make score              # model calls. ~USD 0.004 each
make stage              # free. dedupes into candidates and stages what clears 60
make status             # what every source did, today's spend, queue depth
```

`make run` does all of it in one pass, which is what the scheduler calls.

Then open the queue:

```bash
make review             # http://127.0.0.1:8080
```

---

## Path B — Docker Compose (the deployment shape, unexecuted)

Four services here, not three: `postgres`, `pipeline`, `review` and `browser`. The
browser service exists so the other two do not carry a Chromium they never launch —
it is built from `Dockerfile.browser` and runs the browser sources only. It publishes
no port and listens for nothing.

Same `.env` as above, except the four `DATABASE_URL_*` lines are supplied to the
containers by `docker-compose.yml` and the ones in `.env` are used by commands you run on
the host. Both point at `localhost:5432`, which Compose publishes on the loopback
interface only.

```bash
cp .env.example .env    # then edit it exactly as in Path A
make up                 # starts postgres, waits for healthy, migrates, seeds
make run
make review
```

`make up` starts Postgres in a container and then runs `make migrate` and `make seed`
**on the host**, so Path B still needs `uv` and Python locally. The containers are for
running the pipeline and the review app, not for administering the database.

Two things were fixed by reading rather than by running, and they are the ones most
likely to have bitten you:

- `logs/`, `exports/` and `storage/` are now tracked as empty directories. Compose
  bind-mounts all three, and Docker creates a missing bind-mount target itself, as root —
  after which neither the container nor you can write to it, surfacing much later as a
  permission error from whichever stage first tries.
- The image now copies `uv.lock` and installs with `--frozen`. It previously resolved
  dependencies fresh at build time, so the image could carry different versions from the
  ones the test suite ran against.

**If Path B misbehaves, fall back to Path A and tell me what happened.** Path B has not
been demonstrated and I would rather hear the real error than guess at it.

---

## Where things are

| What | Where |
| --- | --- |
| The ten-minute walkthrough | `DEMO.md` |
| Start, stop, add a source, read health, roll back a prompt | `RUNBOOK.md` |
| The six failure drills and their expected outcomes | `scripts/drills/README.md` |
| Decisions still open, and the five needing a person | `docs/open_decisions.md` |
| The 22 engineering rules the design is held to | `CLAUDE.md` |
| What to build next, in order | `BUILD_ORDER.md` |

## What it will not do

The review app has **no authentication**. The loopback binding is the access control.
That is deliberate for the pilot and is why there is no inbound network path anywhere in
the design — on a deployed host a reviewer reaches it through an SSM port forward, never
over a network.

There is **no CRM integration**, and none is half-built. Approved records leave as a CSV
file with a manifest and by no other route. That is a decision, not a gap, and it returns
as its own question after the week 14 gate.

**Nothing is notified.** No email, no chat, no webhook. The reviewer works the queue on a
schedule they set.
