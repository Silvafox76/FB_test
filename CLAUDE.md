# PFM Opportunity Monitor

Pipeline that reads public procurement and donor notices, filters and scores them against FreeBalance's PFM function map with Claude, deduplicates them into candidates, and stages them in a review queue held in PostgreSQL. A named human reviewer approves a candidate; only that approval can create an approved record. The pipeline can never create one. Approved records leave the system as a one-way CSV export shaped to FreeBalance's CRM Opportunity record, which BD imports by hand.

The Monitor is a standalone application with its own database, used by 5 to 10 named people who sign in through company SSO, and organised by sales region. Every country belongs to exactly one sales region (`config/regions.yaml` from step 22b; the draft sits in `docs/regions.yaml` until then). The pilot works one region, Europe & West Africa (Matthew), plus the eight francophone West African countries as a recorded pilot exception. The other nine regions exist in the data model, in the users table and in reporting from day one, with no sources and no reviewers.

This brief covers the full 14-week pilot, not only the opening weekend. BUILD_ORDER.md has 31 steps: 1 to 11 are the weekend slice on Postgres and the direct Anthropic API; 12 to 31 carry the same repository through Terraform, the Bedrock cutover, the remaining wave-1 feeds, translation, the export and its dry-run import, the West African portals, wave 2, shadow, live, and the week 14 gate. Every rule below holds for all 31 steps, not just the weekend ones.

**Scope, in one paragraph, because it has changed and older documents say otherwise.** The Monitor is a standalone application. Review, approval and the approved record live in its own Postgres database for the whole pilot, and the review app in `review/` is the permanent interface for every user. CRM integration is deferred and is not designed in this pilot: no CRM SDK or HTTP client, no CRM OAuth flow, no CRM-side script, no webhook, no CRM credential in Secrets Manager or config. Approved records leave as an export file shaped to the CRM Opportunity record and by no other route (D31, restated by D61). Decisions D61 to D64 in `docs/open_decisions.md` (19 September 2026) add the standalone framing, the regional model, SSO access for named users and the pilot geography; where Pilot Plan v0.4 or Architecture v0.4 disagree with them, `docs/change_record_v0_5.md` says which section is superseded. Any instruction in an older document to build a CRM-side component is superseded. Weekend Build Plan v1.3 is historical.

Read `BUILD_ORDER.md` for what to build next and in what order. Do one step per session. Do not start a step until the previous step's acceptance tests pass.

## Engineering rules

These are properties of the system, not preferences. If a change would break one, stop and say so instead of working around it. They are numbered to match `design-cop`, which reviews every commit against the same list. If you and design-cop disagree on a number, design-cop is right and the documents get fixed that evening.

1. **No fallbacks.** One correct path. No second selector, second client, second model, second translation service, second endpoint. Alternatives are decisions people take, not branches the code takes.
2. **No retries or backoff** except two documented cases: one retry on model schema failure, which then parks the notice, and connector retry within a single scheduled run where the source registry declares it.
3. **No `try/except` around connectors or the model client.** Failures raise and mark the source unhealthy. The one exception is re-raising with the source id attached.
4. **Fail fast and loudly.** Pydantic validation at every module boundary. Zero-yield on a source that normally yields is a failure state, not an empty success. Unknown fields, missing fields and wrong types raise.
5. **Separation of concerns.** Acquire, normalise, translate, filter, score, dedupe, stage, review, export. One job per module, typed contract on each side. A normaliser that also filters is a finding. A connector that translates is a finding.
6. **Config over code.** Sources, both lexicons, thresholds, geography weights, the 33 functions, the product mapping and the record defaults live in YAML under `sources/` and `config/`, validated on load, version-hashed in the database. A keyword, threshold, country weight or placeholder string hardcoded in a `.py` file is a finding.
7. **No ORM.** Migrations are SQL applied by the runner. Write SQL in `.sql` files and small typed query functions.
8. **Surgical changes.** A diff that refactors code it was not asked to change is a finding, even when the refactor is an improvement.
9. **Original text is the record.** Notices are stored as published, in their language. English renderings are derived fields with a model and prompt version stamp. Overwriting `notices.title` or `notices.body` with a translation is blocking.
10. **Deadlines are parsed by rules from the original**, never from a translation. A parse that reads `title_en` or `body_en` for a date is blocking.

## Checkpoint rules, enforced in Postgres, all blocking

11. Two runtime roles and only two, plus `monitor_readonly` for reporting. `monitor_pipeline` has no privilege on `approved_records`. `monitor_review` has insert on `approved_records` and update on `candidates`. Code that connects the pipeline as `monitor_review`, or that uses a superuser or owner connection at runtime, is blocking.
12. Exactly one code path inserts into `approved_records`: `review/decisions.py`, inside the reviewer's decision transaction. Any other module referencing that table for write is blocking.
13. No candidate reaches `approved` status without an authenticated reviewer (the SSO identity, from step 22c) recorded on the decision and a matching `events` row. A status transition written by the pipeline is blocking.
14. Rejection reason is enforced server side. Client-side-only validation is a finding.

## Scope rules for this phase, all blocking

15. **No CRM integration exists.** The list in the scope paragraph above is exhaustive and none of it is built. A commit that adds any of it is out of scope, not ahead of schedule.
16. **The export is one way and it is a file.** No import path, no reconciliation job, no write-back from a CRM. Postgres is the system of record up to approval and never after.
17. **No public inbound path.** No webhook receiver, no public endpoint, no listener on the pipeline. The review app is reached only through the SSO access layer (D63) or, in development, on localhost. The pipeline has no inbound path of any kind.
18. **Nothing is notified.** Any notification call, email send or chat post is blocking in this phase. The reviewer works the queue on a schedule they set.

## Region and identity rules, all blocking

23. **Region is data.** A country's region comes from `config/regions.yaml` and nowhere else. A region name, country list or region check written into a `.py` file or a template is blocking. Every ISO 3166 code sits in exactly one region, in `excluded` or in `unassigned`; a test asserts it.
24. **Identity comes from the access layer only.** The review app trusts one signed identity assertion from the SSO access layer and verifies its signature on every request. No login form, no local password, no typed reviewer name, no identity taken from a query string or a cookie the app issued itself.
25. **Authority follows region, server side.** A user may approve, edit or reject only candidates in a region their role covers (`config/users.yaml`, loaded into `users` and `user_regions`). Anyone signed in may read every region. A decision check done only in the template is blocking.

## Data and secrets rules, all blocking

19. Prompts carry public notice text and metadata only. Any reviewer name, staff name, contact detail, business record or prior candidate reaching a model call is blocking. The pipeline holds no CRM data to send, which is a property of the architecture rather than a rule to remember.
20. No secret, token, credential or portal password in code, config, fixtures, logs or commits. Request bodies to the model are never logged. Credentials come from environment variables in the pilot and Secrets Manager on the host.
21. Acquisition ethics: respect robots.txt, identified user agent (`FreeBalance-OpportunityMonitor/0.1 (+contact email)`), one polite pass per schedule, no CAPTCHA solving, no residential or rotating proxies, no third-party mirror of an official portal, no user agent that disguises the client.
22. Every model call is capped and logged: 2,000 calls and USD 25 per day, checked before each call; model, purpose, prompt_version, tokens in and out, cost and latency recorded per call. The caps live in `config/thresholds.yaml` and that file is authoritative; `DAILY_CALL_CAP` and `DAILY_USD_CAP` override it and exist for drill 3 only, never as a permanent setting. The call cap was 600 until one day of TED alone needed 1,132 translate calls; the USD cap is the one that binds and has not been close.

## Stack

Python 3.12, `uv`, `ruff`, `pytest`, `pydantic` v2, `httpx`, `selectolax`, `rapidfuzz`, `anthropic`, `psycopg` v3, `structlog`, `pyyaml`, `fastapi` + `jinja2` for the review app. PostgreSQL 16 with pgvector installed (unused this phase). Docker Compose with three services: `pipeline`, `review`, `postgres`. Playwright arrives with the West African portals at step 17, in its own container, and not before.

Do not add dependencies beyond this list without saying why in the commit message.

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
  fx/            config.py, nbu.py (the one rate publisher), store.py, convert.py
  stage/         stager.py, record.py (the record builder, appendix E), region.py (country -> region, step 22b)
  health/        source_health.py, metrics.py
  db.py          connection factory; role chosen by env var, never by code path
  cli.py         `monitor fetch <source>|all`, `monitor score`, `monitor stage`, `monitor status`, `monitor golden`
review/
  app.py         FastAPI, connects as monitor_review only
  auth.py        verifies the access layer's signed identity; maps it to a user and their regions (step 22c)
  decisions.py   the single write path: approve, edit_then_approve, reject
  export.py      the only way out: CSV plus manifest, one way, no import path
  templates/     queue.html, candidate.html, decided.html, sources.html, audit.html, export.html
sources/         one YAML per source
config/          function_map.yaml, lexicon_en.yaml, lexicon_fr.yaml, thresholds.yaml, record_defaults.yaml, fx.yaml, regions.yaml, users.yaml
migrations/      001_schema.sql, 002_roles.sql, ...
tests/           unit/, contract/ (fixtures/), golden/ (golden.csv), roles/, review/
.claude/agents/  the seven subagent files
docker-compose.yml, Makefile, RUNBOOK.md, CLAUDE.md, BUILD_ORDER.md
```

## File ownership

An agent that believes it needs a file outside its lane stops and reports rather than editing it.

| Agent | Writes only |
| --- | --- |
| source-onboarder | `sources/*.yaml`, inventory rows |
| feed-connector-builder | `monitor/connectors/` feed modules, `tests/contract/fixtures/`, `tests/contract/` |
| portal-connector-builder | `monitor/connectors/page.py`, `browser.py`, `tests/contract/fixtures/`, `tests/contract/` |
| review-app-builder | `review/`, `monitor/stage/record.py`, `config/record_defaults.yaml` |
| eval-harness | `tests/golden/`, `tests/backtest/`, `reports/` |
| ops-analyst | nothing, read-only by tool allowlist |
| design-cop | nothing, read-only by tool allowlist |

The main session owns everything else: Terraform, migrations and the roles, normaliser, translator, filter, scorer, deduper, stager, `function_map.yaml`, both lexicons and `thresholds.yaml`.

## Rules for how you work

- Read the step in `BUILD_ORDER.md`, restate its acceptance tests in your first message, build to them, run them, then stop. Do not continue into the next step.
- Surgical changes. Touch only the files the step names. If you believe another file must change, say why before changing it.
- Write the test before or with the code, never after the fact as an afterthought. Contract tests run against recorded fixtures in `tests/contract/fixtures/`, one file per source, recorded from a real call and committed.
- No `Optional` sprinkled to make types pass. If a field can be absent, the model says so and the reason is in a comment.
- Logging is `structlog` JSON with `source_id`, `run_id`, `notice_id` or `candidate_id` bound where they exist. Log what happened, not what you hoped would happen.
- Commit messages: one line, imperative, naming the step (`step 3: TED connector with fixture and contract test`).
- If a fact is needed from a live API (field names, date formats), fetch one real response, save it as the fixture, and build against what is actually there. Do not build against documentation memory.
- Prefer boring code. A function that reads top to bottom beats a class hierarchy. No abstract base beyond `FeedConnector`.
- When something in this file conflicts with a request in a session, this file wins; say so and ask.

## Commands

```
make up            # docker compose up -d postgres; apply migrations
make test          # ruff check . && pytest -q
make fetch S=ted   # run one connector once
make fx            # fetch and store the day's exchange rates from the one rate publisher
make run           # fetch all, filter, score, dedupe, stage (one full pass)
make status        # source health, today's calls and cost, queue depth, export backlog
make metrics       # the week's numbers, stored as the row the review app's metrics page reads
make rescore       # second opinion on in-band scores from the larger model; sized by hand, not part of `run`
make golden        # precision, recall, schema validity per prompt_version
make review        # start the review app on 127.0.0.1:8080
make export        # produce a CSV batch and its manifest from approved, unexported records
```

## Domain notes

- PFM = public financial management. The function map has 33 functions across 8 pillars; scoring runs at function level. Type weights: Government Controls and Process Execution 1.2 to 1.5; Policy and Fiscal Transparency 0.6 to 0.9. The Type column in the component map workbook resolves these, so the weight is a lookup, not a judgement call.
- System names that add signal: IFMIS, GIFMIS, IPPIS, TSA, HRMIS, ITAS, e-procurement, SIGIF, SIGFiP, AGFIS, ISFU, SIGMAP, RACHAD, KFMIS.
- Pilot geography (D64): the 41 countries of the Europe & West Africa region plus the pilot exception BJ BF CI ML MR NE SN TG, 49 in all. The exception countries are owned by the MENA & Francophone Africa region and carry that region on every candidate; the exception decides only that the pilot works them. BG CZ HU RO SK (Central & Southeast Europe) and PT (Lusophone) left the pilot on 19 September; their notices are held, not staged. AD MC AM GE joined.
- Priority geography weights: West Africa 1.0 (BJ BF CI GM GH LR ML MR NE NG SN SL TG); Ukraine and Western Balkans 0.8 (UA AL BA XK ME MK); everything else in the pilot 0.6, including AM and GE until Matthew sets them. From `config/thresholds.yaml`.
- CPV top-level codes that pass the free filter: 48 (software), 72 (IT services), 79 (business and consultancy). Everything else with a CPV code is dropped before any model call.
- Staging threshold: 25. Per-source daily staging cap: 15. Lowered from 60 on 2026-09-12, deliberately, to put borderline work in front of a reviewer rather than discard it. 60 could not be nudged: every value from 43 to 68 staged the same 12 candidates, because the scores take only 18 distinct values and pile on round ones, so a threshold can only land between two buckets (decision 39). What made 25 safe was the lexicon change made the same day — most of what used to arrive scoring 5 to 28 was not marginal PFM, it was waste disposal and vehicle repair passing the free filter on a bare word (decision 44). `config/thresholds.yaml` is authoritative and carries the reasoning; this line is a summary and will go stale before that file does.
- Kosovo uses ISO code XK.

## Export target: the CRM Opportunity record, not a lead

A live CRM record (Ghana GIFMIS Modernisation and EU PFM Reform, Ministry of Finance Ghana, created by a BD person, owned by a regional BD lead) shows the account already runs these tenders through the Opportunities module, with roughly 60 fields across Opportunity Information, Deal Classification, Eligibility, Partner and Legal, Submission Information, Pricing Summary, Products Required and Implementation and Pricing. `approved_records.record` is shaped like that record, and the export writes those columns in that order and naming so the CRM's import mapper matches on headers. The field-by-field spec, with what is derived, what is a reviewer-editable suggestion and what stays a BD-only placeholder, is **Architecture v0.4 appendix E**. `monitor/stage/record.py` builds to it and nothing else invents a field. Four things this implies:

- Every BD-only field (pricing structure, warranty, number of users, legal support, bid bond, evaluation weighting) is set to the same placeholder the CRM already shows for an unfilled field: `TBD`, `Unknown`, a bare dash or `0`. Never a database `NULL` that renders as blank with no explanation, and never a plausible-looking invented value. An imported Monitor record should read like an early-stage record a person started, not a machine's guess dressed up as fact. It should just arrive a day after publication instead of whenever someone notices it.
- **FreeBalance Products Required is a set, not a string.** It comes from the component map workbook's New Marketecture sheet: 20 products across 578 mapping rows for 559 components, so some functions map to more than one product. Product Gaps is populated from the same sheet's status column, naming any matched product whose status is not Available. Pillar 8 has no product mapping in the workbook; write an empty set and say so.
- **A value is carried in the currency published, and the USD figure beside it says how it was derived.** `notices.estimated_value` and `value_currency` hold the amount exactly as the source stated it; `candidates.estimated_value_usd` is derived at staging and stored with `value_rate` and `value_rate_date`, so `estimated_value / value_rate` returns the figure on the record. One rate publisher, named in `config/fx.yaml`, plus the statutory CFA pegs. A currency no rate covers gets no USD figure and keeps its own amount rather than rendering blank. The scorer is NOT asked for a value and its schema has no field for one: it invented all eight it ever reported, none of which appeared in the notice text it was given.
- **Account resolution happens at import, not at approval.** The record builder proposes the buyer name as text and nothing more. The pipeline never creates, guesses or looks up an Account link, and it has no CRM scope with which to try.
- **There is no automated duplicate check and there will not be one in this pilot.** The candidate page's decision panel carries a standing reminder to check the CRM by hand for an existing Opportunity on the buyer. This is proven necessary, not theoretical: the Ghana tender the Monitor would surface already has a live Opportunity from December 2025. Under D31 the reviewer is the only duplicate control, every duplicate that reaches an export batch is logged as a finding, and the count is a week 14 gate number.
