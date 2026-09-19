# PFM Opportunity Monitor

A standalone application with its own database. It reads public procurement and donor notices,
filters and scores them against FreeBalance's PFM function map with Claude, deduplicates them into
candidates, and stages them in a review queue held in PostgreSQL. A named reviewer approves a
candidate; only that approval can create an approved record. The pipeline can
never create one. Approved records leave as a one-way CSV export shaped to the CRM Opportunity record,
which BD imports by hand. CRM integration is deferred (D31, D61) and enforced by review: `CLAUDE.md`
scope rules 15 to 18.

The application is organised by FreeBalance's ten sales regions (`docs/regions.yaml`, moving to
`config/` at step 22b). The pilot works one of them, Europe & West Africa (41 countries), plus eight
francophone West African countries as a recorded exception: 49 countries. The other nine regions
exist in the data model and reporting with no sources and no reviewers. Decisions 61 to 64 in
`docs/open_decisions.md` (19 September 2026) set this out.

## Start here

| Document | What it is for |
| --- | --- |
| `CLAUDE.md` | The engineering rules. When a request conflicts with it, it wins. |
| `BUILD_ORDER.md` | 31 steps, one per session, each with the acceptance tests that gate its commit. |
| `RUNBOOK.md` | Operate it: start, stop, add a source, read health, roll back a prompt. Arrives at step 11. |
| `docs/reference/` | Architecture v0.4, Weekend Build Plan v1.3 and the component map workbook, the documents the steps cite. |
| `docs/open_decisions.md` | Questions a step raised, open and closed, each with the step that settles it. Decisions 61 to 64 (standalone, regions, SSO, geography) sit at the top. |
| `docs/change_record_v0_5.md` | Which sections of Pilot Plan v0.4 and Architecture v0.4 decisions 61 to 64 supersede. |
| `docs/regional_rollout.md` | How regions come online after the pilot: R0, then one region at a time through the same gates, in a recommended order. |
| `docs/regions.yaml` | The ten sales regions and every country's owner, draft; moves to `config/` at step 22b. |
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
done because its files exist. 1,191 tests pass and 1 is skipped, on a database with no test
fixtures left in it. The
checkpoint test that proves `monitor_pipeline` holds no privilege on `approved_records` runs on every
commit. The pipeline holds 2,659 notices from 15 enabled sources, 1,387 of
them carrying a published value in the currency it was published in, and 152 candidates, 39 of them
in the review queue — 7 West Africa, 2 Balkans, 30 Europe; of those 39, 19 show a converted USD
figure, 20 state no value, and none is stuck in a currency with no rate held for it. **Six West
African national sources are live**: Sierra Leone and Liberia since 2026-09-12, and Mali, Senegal,
Ghana and Burkina Faso since 2026-09-13, each after a recorded fixture, a passing contract test and
one clean live pass inside its expected band. Their yield is what the log warned it would be:
of 218 West African notices held, 5 have passed the free filter and 2 are candidates. Nothing has
been approved or exported: the pilot has not entered shadow mode. These counts move with every pass;
`make status` is the live reading and this line is a snapshot taken on 2026-09-13.

| Steps | State |
| --- | --- |
| 1 to 15 | Met. Registry, connectors, normaliser, free filter, scorer, deduper, stager, the review app and the single write path; Terraform, the Bedrock route, the translation stage, the Europe and donor feeds. |
| 16, export | Built, **not met**. The dry-run import needs a BD operator with a CRM sandbox. It gates shadow entry. |
| 22a and 22b, CRM-neutral wording, regional model | **Not started.** Added 2026-09-19 (decisions 61 to 64). They gate shadow entry. 22c, SSO access, is deferred to R0 after the gate (decision 67): the pilot has at most two users on localhost over the tunnel. |
| Operations | **No host runs the pipeline.** The `deploy/systemd` timers have never fired: the build environment has no init system and no pilot host is provisioned, so every pass to date was started by hand and nothing published between sessions has been seen. Five of six "missed" notices traced on 2026-09-18 come back to this (decision 59). One small host with the timers enabled, per the RUNBOOK, is the item above every other. |
| The register's class A wave | **Three more national sources live on 2026-09-19** (decision 72): Contracts Finder (GB below threshold), Bosnia and Herzegovina's EJN OpenAPI, Latvia's IUB daily files; Public Contracts Scotland built and held on a TLS trust-bundle decision. Fourteen onboarding runs from the 91-row Source Access and ToS Register; ten decision points recorded in the decisions artefact; the 40 class B rows carry one decision each in the register workbook (decision 73). |
| 17 to 19, West Africa | Built, **mostly met**. Six of ten West African portals are live with a recorded fixture, a passing contract test, a normaliser and a clean live pass (Burkina Faso, Ghana, Liberia, Mali, Senegal, Sierra Leone). Burkina Faso's daily bulletin needed the acquire stage to take one PDF holding thirty-odd notices (decision 51) and a mapper measured against six real issues; its 126 notices from four issues yielded 3 free-filter passes and no candidate, the yield the 284-notice sample predicted. Of the four remaining: Benin is cleared on terms and needs a person at a real browser to record its row selector; Nigeria is a retrospective register measured at zero live tenders, dropped from wave 1 pending a named person's decision; Gambia and Côte d'Ivoire have no terms page to clear and stay `pending`. The connector classes BUILD_ORDER assumed were wrong for three of the four in step 17 and are corrected there. |
| 20, escalation and cross-language dedupe | Met, against real data and real Sonnet calls. |
| 21, golden set and metrics | Metrics half met. Golden half measured on 2026-09-16 and **not met**: precision 50% on live scores (33% on a fresh re-score) against a gate of 60%, recall 69% (56%). See below. |
| 21b, published value and USD conversion | Met. See `BUILD_ORDER.md`. |
| 22, security review | Met. Eleven findings, two closed early, two deferred with an owner and a date. |
| 25, wave 2 | Out of order — its gates at steps 23 and 24 have not run. Both connectors ship `enabled: false`. |

Steps 23, 24 and 26 to 31 are untouched: shadow and live mode, the weekly tuning cadence, the week 14
gate. `docs/open_decisions.md` carries the per-step disclosure table, including why this branch built
several steps out of order, and 41 open and closed decisions with what each blocked source is blocked
on.

**Three things worth knowing before trusting any number this system produces.**

*The golden set is labelled and the scorer misses the gate.* 150 stratified notices across 8 sources and
19 languages were labelled by hand on 2026-09-16: 16 relevant, 134 not, every row with a reasoned note
(`tests/golden/golden.csv`; the labels are human by design, since a set labelled by the model it measures
would score well against its own opinion). Measured against the staging threshold of 25: **50% precision
and 69% recall on the pipeline's live scores; 33% and 56% on the harness's fresh re-score of the same
notices with the same prompt** (`tests/golden/history.csv`). The gate is 60% precision. The free filter
dropped none of the 16 relevant, so every error is the scorer's, and the errors have a shape: nine of the
eleven false positives are services about finance (audits, outsourced accounting, consultancy) that the
labeller excludes and the scorer does not yet tell apart from finance software. No threshold reaches 60 on
both numbers, so the lever is the prompt, and by the harness's own rule the numbers are reported and a
person decides. Decision 57 in `docs/open_decisions.md` has the rest: two labels that contradict their own
notes, five "worth monitoring" positives the metric counts as bids, and six scope questions the notes turn
on.

*The staging threshold is 25, not 60.* `config/thresholds.yaml` is authoritative and carries the
reasoning: 60 could not be tuned because the scorer's scores cluster on a small number of round
values, so every threshold from 43 to 68 staged the identical 12 candidates and moving it inside that
band changed nothing. 25 is the edge taken instead, made safe by the same-day lexicon fix that removed
the generic words letting waste-disposal and vehicle-repair notices reach the free filter. The rescore
band follows the same floor, `[25, 70]`, and is reachable only via `monitor rescore` / `make rescore`
— deliberately not a stage of `monitor run`, because each row is a paid Sonnet call sized by a person.

*Contract values are no longer the model's invention.* The scorer used to report
`estimated_value_usd` itself; every one of the eight values it ever produced was checked against the
full notice text it was given, and none of the eight appeared in that text in any form — migration
`012_published_value_and_currency.sql`'s header carries the evidence, including four TED notices
where the true structured figure sat in the payload the model was never shown. Values now come from
the source's own structured field, stored in the currency it was published in (rule 9), and a USD
figure is derived at staging from a rate stored beside it: `estimated_value / value_rate` reproduces
`estimated_value_usd` exactly on every stamped row (checked today: 60 of 60). Two things a reviewer
will not see on that figure. Prozorro mixes VAT-inclusive and VAT-exclusive amounts in the same field
— of 743 stored payloads, 245 state `valueAddedTaxIncluded: true` and 498 state `false`, and the
pipeline carries the number as published, saying nothing about which. And 24 BOAMP eForms notices
state a value only on their lots and none at the procedure level; summing the lots would be this
module's own arithmetic rather than the publisher's, and on 12 of those the publisher's own total
disagrees with the lot sum anyway, so those 24 show "not stated" rather than a computed figure.

Observability of a running pilot is a person reading `make status` and `make metrics`. The five
application alarms in `infra/terraform/observability.tf` still have no publisher and sit in
INSUFFICIENT_DATA on purpose rather than being quietly deleted (decision 41).

`config/function_map.yaml` is generated, not hand-written. Regenerate it with:

```bash
uv run python scripts/export_function_map.py
```

Everything else under `config/` and all of `sources/` is hand-edited and validated on load.

*Test fixtures can outlive an interrupted run.* `tests/review/test_export.py` creates sources,
notices and candidates on an owner connection and deletes them at teardown; a run killed mid-test
skips the teardown. On 2026-09-12 a day of concurrent agent runs left 617 `test-exp-*` sources
and three `pending_review` candidates in the dev database, which made two unrelated tests fail
until they were removed as owner (the runtime roles hold no delete). Their ids are reserved above
`C900000` and their source ids start `test-`, so they are easy to find and safe to remove.
