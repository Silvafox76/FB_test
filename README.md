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
make translate     # translate notices held for want of a lexicon, then re-filter them
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

**Steps 1 to 22 have been worked, and not all of their acceptance tests are met.** Code is not the
same thing as a passed gate, so the table below says which is which rather than reporting a step as
done because its files exist. 1,039 tests pass, and the checkpoint test that proves
`monitor_pipeline` holds no privilege on `approved_records` runs on every commit. The pipeline holds 2,083 notices from 8 enabled sources and 143 candidates, 12 of them in the
review queue. Nothing has been approved or exported: the pilot has not entered shadow mode.

| Steps | State |
| --- | --- |
| 1 to 15 | Met. Registry, connectors, normaliser, free filter, scorer, deduper, stager, the review app and the single write path; Terraform, the Bedrock route, the translation stage, the Europe and donor feeds. |
| 16, export | Built, **not met**. The dry-run import needs a person with a Zoho sandbox. It gates shadow entry. |
| 17 to 19, West Africa | Built, **partly met**. Ten of ten West African portals have no recorded fixture; five have no terms page to clear at all. |
| 20, escalation and cross-language dedupe | Met, against real data and real Sonnet calls. |
| 21, golden set and metrics | Metrics half met; golden half **not met** — see below. |
| 22, security review | Met. Eleven findings, two closed early, two deferred with an owner and a date. |
| 25, wave 2 | Out of order — its gates at steps 23 and 24 have not run. Both connectors ship `enabled: false`. |

Steps 23, 24 and 26 to 31 are untouched: shadow and live mode, the weekly tuning cadence, the week 14
gate. `docs/open_decisions.md` carries the per-step disclosure table, including why this branch built
several steps out of order, and 41 open and closed decisions with what each blocked source is blocked
on.

**Two things worth knowing before trusting any number this system produces.**

*The golden set has no labels.* It is 150 stratified notices across 8 sources and 19 languages,
straddling the free filter so it can measure what the filter drops as well as what the scorer keeps —
and every label is empty. `make golden` refuses to compute a figure until a person fills them in, by
design: a set labelled by the same family of model it measures would score well against its own
opinion, and a real regression would read as agreement. **So precision and recall are unmeasured, and
any claim about this system's accuracy today has no measurement behind it.** It needs roughly two
hours from a named labeller, and it is the single largest unmeasured thing in the pilot.

*The staging threshold cannot usefully be tuned.* Swept across all 143 candidates, every value from 43
to 68 stages the same 12, so the current 60 sits mid-plateau and moving it inside that range changes
nothing. The 146 scores take 18 distinct values and pile onto round ones — 58 notices at exactly 5, 38
at 15, 10 at 72 — so the scorer behaves as a five-or-six-way classifier wearing a 0-100 scale. The top
score in the corpus is 72, so any threshold at 73 or above empties the queue. The levers that would
move the queue are the scoring prompt and the upstream filter, not the number (decision 39).

Observability of a running pilot is a person reading `make status` and `make metrics`. The five
application alarms in `infra/terraform/observability.tf` still have no publisher and sit in
INSUFFICIENT_DATA on purpose rather than being quietly deleted (decision 41).

`config/function_map.yaml` is generated, not hand-written. Regenerate it with:

```bash
uv run python scripts/export_function_map.py
```

Everything else under `config/` and all of `sources/` is hand-edited and validated on load.
