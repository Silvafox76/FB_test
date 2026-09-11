# PFM Opportunity Monitor

Reads public procurement and donor notices from 47 countries, filters and scores them against
FreeBalance's PFM function map with Claude, deduplicates them into candidates, and stages them in a
review queue held in PostgreSQL. A named human reviewer approves a candidate; only that approval can
create an approved record. The pipeline can never create one. Approved records leave the system as a
one-way CSV export that a named person imports into Zoho CRM by hand.

There is no CRM integration in any step of this pilot. That is decision D31 in Architecture v0.4 and
it is enforced by review, not by memory: see `CLAUDE.md` scope rules 15 to 18.

## Start here

| Document | What it is for |
| --- | --- |
| `CLAUDE.md` | The engineering rules. When a request conflicts with it, it wins. |
| `BUILD_ORDER.md` | 31 steps, one per session, each with the acceptance tests that gate its commit. |
| `RUNBOOK.md` | Operate it: start, stop, add a source, read health, roll back a prompt. Arrives at step 11. |
| `docs/reference/` | Architecture v0.4, Weekend Build Plan v1.3 and the component map workbook, the documents the steps cite. |
| `docs/open_decisions.md` | Questions a step raised, open and closed, each with the step that settles it. |
| `docs/design_inputs.md` | What the workbook and the prototypes are each allowed to decide. |
| `.claude/agents/` | The seven subagent definitions and the ownership table, per `.claude/agents/SETUP.md`. |

## Layout

```
monitor/
  registry/      load and validate sources/*.yaml -> Source models
  connectors/    base.py (FeedConnector), ted.py, prozorro.py, fts.py, worldbank.py
  normalise/     RawNotice -> Notice; language and deadline rules
  translate/     haiku client, strict JSON (step 14)
  filter/        cpv.py, lexicon.py
  score/         prompt.py, client.py, schema.py, caps.py
  dedupe/        cluster.py
  stage/         stager.py, record.py (the record builder, appendix E)
  health/        source_health.py, metrics.py
  db.py          connection factory; role chosen by env var, never by code path
  cli.py         fetch, score, stage, status, golden
review/
  app.py         FastAPI, connects as monitor_review only
  decisions.py   the single write path: approve, edit_then_approve, reject
  export.py      the only way out: CSV plus manifest, one way, no import path
  templates/     queue, candidate, decided, sources, audit, export
sources/         one YAML per source
config/          function_map.yaml, lexicon_en.yaml, lexicon_fr.yaml, thresholds.yaml, record_defaults.yaml
migrations/      001_schema.sql, 002_roles.sql, ...
tests/           unit/, contract/ (fixtures/), golden/, roles/, review/
```

## Set up

Requires Docker with Compose, [uv](https://docs.astral.sh/uv/), and Python 3.12 (uv fetches it).

```bash
cp .env.example .env          # fill in the passwords and ANTHROPIC_API_KEY; .env is git-ignored
uv sync                       # create the virtualenv from pyproject.toml
uv run pre-commit install     # ruff and the gitleaks secret scan on every commit
make up                       # start Postgres, wait for healthy, apply migrations
make test                     # ruff check and the test suite
```

`make up` applies `migrations/*.sql` as `DATABASE_URL_OWNER`, sets the three role passwords from the
environment (which is why no password appears in a `.sql` file), then seeds the registry from
`sources/*.yaml` and `config/*.yaml`. The owner role needs `CREATEROLE`; the compose superuser has it.
Running `make up` again applies no migration and re-seeds idempotently.

`make test` needs the database up: `tests/roles/test_roles.py` connects as all three roles and fails
rather than skipping when they are not there.

## Commands

```
make up            # start Postgres; from step 2, apply migrations
make test          # ruff check . && pytest
make fetch S=ted   # run one connector once
make run           # fetch all, filter, score, dedupe, stage (one full pass)
make status        # source health, today's calls and cost, queue depth, export backlog
make golden        # precision, recall, schema validity per prompt_version
make review        # start the review app on 127.0.0.1:8080
make export        # produce a CSV batch and its manifest from approved, unexported records
```

Targets whose step has not landed yet fail loudly. That is deliberate: a command that exits 0 having
done nothing is worse than one that says which step builds it.

## Where the checkpoint lives

Two runtime roles and only two, plus `monitor_readonly` for reporting. `monitor_pipeline` holds no
privilege of any kind on `approved_records`. Exactly one code path inserts into that table,
`review/decisions.py`, inside the reviewer's decision transaction, under a named reviewer. The
property is a database grant with `tests/roles/test_roles.py` behind it, not a convention. That test
landed at step 2, before any code that might need it, and runs on every commit for all 31 steps.

## Build status

Step 3 of 31 (registry, models, config) complete. Step 4 is partly built and blocked: the
`FeedConnector` base, the normaliser's hash, deadline and CPV rules and the source-health transitions
are done and tested, but the TED parser waits on a recorded fixture, because a parser is never written
against documentation memory. Run `scripts/record_ted_fixture.py` from a host with outbound access to
`api.ted.europa.eu` to unblock it. The checkpoint test runs on every commit in CI. See
`BUILD_ORDER.md` for what is next and what gates it, and `docs/open_decisions.md` for what is open.

`config/function_map.yaml` is generated, not hand-written. Regenerate it with:

```bash
uv run python scripts/export_function_map.py
```

Everything else under `config/` and all of `sources/` is hand-edited and validated on load.
