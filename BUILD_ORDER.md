# Build order

One step per Claude Code session. Each step lists the files it may touch and the acceptance tests that must pass before the step is committed. Do not skip ahead. Do not "improve" earlier steps while doing a later one; open a separate session for that and say what changed.

Times are the weekend plan's targets (ET). They are targets, not permission to cut tests.

---

## Step 1. Skeleton (Friday 19:00)

Touch: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `.env.example`, `.gitignore`, `.pre-commit-config.yaml`, `monitor/__init__.py`, `monitor/cli.py`, `README.md`.

Build:
- `uv` project, Python 3.12, dependencies from `CLAUDE.md`. `ruff` config with line length 120, `E`, `F`, `I`, `B` rules.
- Compose: `postgres:16` with pgvector image, data on a named volume, healthcheck; `pipeline` and `review` services built from one Dockerfile with two commands; `.env` git-ignored; `.env.example` lists `DATABASE_URL_PIPELINE`, `DATABASE_URL_REVIEW`, `ANTHROPIC_API_KEY`, `MONITOR_USER_AGENT`, `DAILY_CALL_CAP=600`, `DAILY_USD_CAP=25`.
- `monitor/cli.py` with `typer` or `argparse` (pick argparse; fewer dependencies) and stub commands: `fetch`, `score`, `stage`, `status`, `golden`. Each prints "not implemented" and exits 2.
- Pre-commit: ruff, and a secret scan (`detect-secrets` or `gitleaks`; pick one).

Accept:
- `make up` starts Postgres and reports healthy.
- `make test` runs ruff and an empty pytest suite green.
- `git commit` is blocked when a string looking like an API key is staged (test it with a fake `sk-ant-` string, then remove it).

---

## Step 2. Schema and roles (Friday 20:15)

Touch: `migrations/001_schema.sql`, `migrations/002_roles.sql`, `monitor/db.py`, `monitor/migrate.py`, `tests/roles/test_roles.py`.

Build `001_schema.sql` exactly as follows (types abbreviated; use `timestamptz`, `text`, `jsonb`, `uuid` with `gen_random_uuid()`):

- `sources(id text pk, name, country, admin_level, language, stream, access_type, connector_class, wave int, tos_status, enabled bool, expected_min int, expected_max int, max_consecutive_failures int)`
- `fetch_runs(id uuid pk, source_id fk, started_at, finished_at, status, items_seen int, items_new int, error text)`
- `notices_raw(content_hash text pk, source_id fk, url, fetched_at, storage_path, mime)`
- `notices(id uuid pk, content_hash fk unique, source_id fk, external_id, url, title, buyer, country, admin_level, published_at, deadline_at, language, language_confidence real, cpv_codes text[], estimated_value_usd bigint, body text, filter_result text, status text, fetched_at)`
- `translations(notice_id fk, title_en, body_en, model, prompt_version, latency_ms int, cost_usd numeric, created_at, pk(notice_id, prompt_version))`
- `scores(id uuid pk, notice_id fk, model, prompt_version, relevance int, title_en, matched_functions jsonb, system_names text[], procurement_type, estimated_value_usd bigint, eligibility_flags text[], deadline_at, summary_en, confidence, raw_json jsonb, tokens_in int, tokens_out int, latency_ms int, cost_usd numeric, created_at)`
- `candidates(id text pk like 'C000123', primary_notice_id fk, score int, status text check in (detected, filtered_out, scored, staged, pending_review, approved, rejected), region, language, title_en, buyer, country, admin_level, summary_en, matched_functions jsonb, system_names text[], procurement_type, estimated_value_usd bigint, eligibility_flags text[], deadline_at, reviewer text, rejection_reason text, approved_record_id text, detected_run uuid, created_at, updated_at)`
- `candidate_notices(candidate_id fk, notice_id fk, match_method, match_score int, pk(candidate_id, notice_id))`
- `events(id bigserial pk, entity_type, entity_id, action, actor, before text, after text, at timestamptz default now())`
- `source_health(source_id pk fk, last_success_at, consecutive_failures int, median_items real, last_zero_yield_at, zero_yield_runs int, state text)`
- `config_versions(version text pk, path, kind, language, content_hash, applied_at, unique(path, content_hash))` — one row per config file per content hash, covering `sources/*.yaml` and every file under `config/`. Created as `lexicon_versions` in 001 and generalised by `migrations/004_config_versions.sql`, because rule 6 versions the thresholds and the record defaults too and from step 5 the thresholds decide what is dropped before any model call.
- `function_map(function_id text pk, name, pillar, type_weight real, keywords_en text[], keywords_fr text[])`
- `approved_records(id text pk like 'R000001', candidate_id fk unique, record jsonb, approved_by text not null, edited bool not null default false, created_at, exported_at timestamptz, export_batch text fk null)`
- `export_batches(batch_id text pk like 'B0001', created_at, operator text not null, row_count int, range_from, range_to, file_path, manifest_path, sha256 text)`
- `model_calls(id bigserial pk, purpose text, model, prompt_version, tokens_in, tokens_out, cost_usd numeric, latency_ms, at)`

Build `002_roles.sql`:
- `monitor_pipeline`: select, insert, update on every table except `approved_records` and `export_batches`; no privilege of any kind on either; update on `candidates` allowed (it sets pending_review) but a trigger refuses any update that sets status to approved or rejected unless `current_user = 'monitor_review'`.
- `monitor_review`: select on everything; update on `candidates`; insert on `approved_records`, `export_batches` and `events`; update on `approved_records` restricted to `exported_at` and `export_batch`; nothing else.
- `monitor_readonly`: select only, on everything. Used by reporting and by the ops-analyst agent. No write of any kind.
- Passwords from environment at migration time; never in the SQL file.

`monitor/db.py`: `connect(role: Literal["pipeline","review"])` reads the matching `DATABASE_URL_*`. No other way to get a connection. `monitor/migrate.py` applies `migrations/*.sql` in order, records them in a `schema_migrations` table, refuses to run twice.

Accept:
- `make up` applies both migrations; running it again is a no-op.
- `tests/roles/test_roles.py`: connecting as pipeline and inserting into `approved_records` raises `InsufficientPrivilege`; inserting into `export_batches` as pipeline raises; updating a candidate to `approved` as pipeline raises; the same three operations as review succeed and are rolled back by the test. This is the checkpoint. It exists before any code that might need it, and it runs in CI on every commit for all 31 steps.

---

## Step 3. Registry, models, config (Friday 21:00)

Touch: `monitor/registry/*.py`, `monitor/models.py`, `sources/ted.yaml`, `sources/prozorro.yaml`, `sources/fts.yaml`, `sources/worldbank.yaml`, `config/function_map.yaml`, `config/lexicon_en.yaml`, `config/lexicon_fr.yaml`, `config/thresholds.yaml`, `scripts/export_function_map.py`, `tests/unit/test_registry.py`, `tests/unit/test_models.py`.

Build:
- `monitor/models.py`: `Source`, `RawNotice`, `Notice`, `Translation`, `Score`, `Candidate` as pydantic v2 models matching the schema in step 2 field for field, with one exception. `Score` is the model-call tool schema, so it holds Architecture v0.4 appendix C's ten output fields and nothing else; the `scores` table's call metadata (model, prompt_version, tokens, cost, latency) is written by `monitor/score/run.py` alongside the validated `Score`, because a tool schema that asks the model to report its own token count is nonsense. Export `Score.model_json_schema()` for step 6.
- `sources/*.yaml`: id, name, country (`EU` for TED, `multi` for World Bank), covers (list of ISO codes for multi-country sources), language, admin_level, stream, wave, access, connector (`FeedConnector`), schedule (cron string), list_url or api_url, tos_status, health (expected_items_per_run [min, max], max_consecutive_failures), owner.
- `scripts/export_function_map.py`: reads the component map workbook (`PFM Component Map 4.2.xlsx`, sheet and columns named in the script header after opening the file) and writes `config/function_map.yaml` with 33 functions: function_id (slug), name, pillar, type_weight from the Type column (Government Controls 1.3, Process Execution 1.2, Policy 0.8, Fiscal Transparency 0.6, default 1.0), keywords_en starting from the function name and its component names. Hand-edit afterwards is expected; the script is run once.
- `config/lexicon_en.yaml` and `lexicon_fr.yaml`: function_id -> list of phrases. Seed from the prototype's keyword lists (in the pilot documents) and the system-name list in `CLAUDE.md`.
- `config/thresholds.yaml`: `stage_threshold: 60`, `per_source_daily_cap: 15`, `rescore_band: [40, 70]`, `geography: {BJ: 1.0, ..., UA: 0.8, ..., default: 0.6}`, `cpv_pass_prefixes: ["48","72","79"]`.
  **These are the values step 3 created, not the values in force.** Kept as written so the step still records what it built. On 2026-09-12 `stage_threshold` moved to **25** and `rescore_band` to **[25, 70]** (decisions 39, 44 and 45 in `docs/open_decisions.md`); `config/thresholds.yaml` is authoritative for all of them and carries the measurement behind each. Read the file, not this line.
- Each entry in `config/function_map.yaml` gains `product` (a **list**, not a string) and `product_status` keys, read from the component map workbook's own **New Marketecture** sheet rather than hand-written: 20 FreeBalance products across 578 mapping rows for 559 components, each with a status of Available, High Priority, Medium Priority or Low Priority. Some functions map to more than one product (4.2 Debt and Investment to both Public Debt and Guarantee Management and Public Financial Investments; 6.1 Revenue Management to both Revenue and Receipts Mobilization and Tax Administration Mobilization; 7.3 to both Payroll and Wage Bills and Public Service Talent and Capital Human Capital), which is why the field is a set. Pillar 8 (functions 8.1 to 8.5, Government Service Delivery) has no product mapping in the workbook: write `product: []` and `product_note: "no product mapping in component map 4.2"`. `scripts/export_function_map.py` reads this sheet in the same pass. This feeds the export's FreeBalance Products Required and Product Gaps fields in step 10; it is not used by the scorer. Full table in Architecture v0.4 appendix E.
- `config/record_defaults.yaml`: the record field defaults from Architecture v0.4 appendix E — `currency: USD`, `probability_pct: 10`, `pipeline: "Standard (Sales Opportunity)"`, `standard_or_custom: "Standard (COTS)"`, `delivery_model_default: "Direct"`, `customer_type_default: "New Customer"`, `industry_by_region: {West Africa: "North & West Africa", ...}`, `funding_source_by_stream: {worldbank: "World Bank (IDA/IBRD)", afdb: "AfDB", eu: "EU", mcc: "MCC", isdb: "IsDB", boad: "BOAD", default: "Government budget"}`, and the literal placeholder strings for BD-only fields (`"TBD"`, `"Unknown"`, `"-"`). One file, so a BD ops change to a default is a config edit, not a code change.
- Registry loader: reads all YAML, validates into `Source`, upserts `sources` and `function_map`, records lexicon version hashes. Fails on any validation error with the file name and field.

Accept:
- `tests/unit/test_registry.py`: all four sources load; a YAML with a misspelled field fails with the field name; a duplicate id fails.
- `tests/unit/test_models.py`: `Score` rejects relevance 101, an unknown procurement_type, a summary over 140 words, a missing title_en.
- `make up` seeds `sources`, `function_map` and `config_versions`.

---

## Step 4. TED connector, change detection, normaliser (Saturday 08:00)

Touch: `monitor/connectors/base.py`, `monitor/connectors/ted.py`, `monitor/normalise/*.py`, `monitor/health/source_health.py`, `monitor/cli.py` (fetch command), `tests/contract/fixtures/ted.json`, `tests/contract/test_ted.py`, `tests/unit/test_normalise.py`.

Build:
- `FeedConnector` base: `fetch() -> list[RawNotice]`; `httpx.Client` with the user agent from env, 30 s timeout, one attempt. Any exception is re-raised as `ConnectorError(source_id, cause)`. No retry.
- `ted.py`: TED Search API v3. First action of the session: perform one real request (expert query: CPV in the pass prefixes, publication date within the last 2 days, English fields requested where the API supports it), save the raw response as `tests/contract/fixtures/ted.json`, then write the parser against the fields that are actually present. Record in a comment at the top of `ted.py` the exact query and the date the fixture was recorded.
- Change detection: `content_hash = sha256(normalised title + body)`; insert-if-absent into `notices_raw` and `notices`; count seen and new per run in `fetch_runs`.
- Normaliser: TED fields -> `Notice`: country from the buyer country field, language from the notice language field, deadline parsed from the API's ISO date, CPV list, admin_level `national` unless the buyer type says regional or local, estimated value where stated. Deadline parsing is rule-based; if it cannot be parsed the field is null and a warning is logged with the raw string.
- Source health: after each run update `source_health`: success resets failures; failure increments; zero new items on a source with expected_min > 0 increments `zero_yield_runs`, state `watch` at 2, `unhealthy` at `max_consecutive_failures`.
- `monitor fetch ted` runs the connector once, prints seen, new, failed.

Accept:
- `tests/contract/test_ted.py`: parser on the fixture yields between expected_min and expected_max items, every item has title, url, country, external_id; a fixture with a renamed field makes the parser raise (not yield zero).
- `tests/unit/test_normalise.py`: deadline parsing, CPV extraction, hash stability (same input, same hash; whitespace change, same hash).
- Running `make fetch S=ted` twice: second run reports 0 new.

---

## Step 5. Filter (Saturday 11:30)

Touch: `monitor/filter/cpv.py`, `monitor/filter/lexicon.py`, `monitor/filter/run.py`, `tests/unit/test_filter.py`, `tests/unit/fixtures/filter_cases.yaml`.

Build:
- CPV stage: if the notice has CPV codes and none starts with a pass prefix, `filter_result = "cpv <code> outside 48/72/79"`, status `filtered_out`.
- Lexicon stage: if the notice language has a lexicon (en, fr), match phrases case-insensitively at function level on title + body; no match -> `filter_result = "no lexicon match (<lang>)"`, status `filtered_out`. Match -> `filter_result = "cpv <prefix>, lexicon <lang>: <first three phrases>"`, status `scored` pending (set to `scored` only after step 6 succeeds).
- If the language has no lexicon and the notice passed CPV (or had no CPV), mark `filter_result = "needs translation"` and leave status `detected`. Translation is not built this weekend; these notices stop here and are counted.
- Per-source filter statistics printed by `monitor status`.

Accept:
- `filter_cases.yaml` with 12 hand-written cases (4 CPV drops, 4 lexicon drops, 4 passes, at least one French pass); `tests/unit/test_filter.py` runs them all.
- After `make fetch S=ted` and `monitor filter`, `monitor status` shows a drop rate; record the number in `RUNBOOK.md`.

---

## Step 6. Scorer (Saturday 13:15)

Touch: `monitor/score/prompt.py`, `monitor/score/client.py`, `monitor/score/schema.py`, `monitor/score/run.py`, `monitor/cli.py` (score command), `tests/unit/test_score_schema.py`.

**The cap guard already exists and this step consumes it rather than building it.** Step 14 was taken before this one and needed the same guard, so it lives at `monitor/caps.py` with its tests at `tests/unit/test_caps.py`: one guard shared by translate, score and rescore rather than one per caller. Do not create `monitor/score/caps.py`. The rate card and both daily caps are already in `config/thresholds.yaml`, and `caps.check()` and `caps.record()` are the two functions to call. The same applies to the system-name list: `config/system_names.yaml` is the one copy, read through `monitor.registry.load_system_names`, so `prompt.py` reads it rather than restating it.

Build:
- `prompt.py`: builds the system prompt from `function_map.yaml`, both lexicons, the system-name list, geography weights and the rubric text in Architecture v0.4 section 8. Marks the system prompt block for prompt caching. `prompt_version = sha256(system prompt)[:12]`.
- `schema.py`: the tool definition `record_score` whose input schema is `Score.model_json_schema()`; the call uses `tool_choice` forcing that tool.
- `client.py`: one function `score_notice(notice) -> Score`. Uses `anthropic.Anthropic()` from env. Model `claude-haiku-4-5` for scoring (exact model string checked at session start against the SDK's model list; record it in a constant with a comment). Validates the tool input with `Score`; on validation error, exactly one retry with the validation message appended to the user turn; on second failure raises `SchemaError` and the caller parks the notice (status `parked`, event written). Logs a `model_calls` row for every call including failures.
- `monitor/caps.py` (already built at step 14): before each call it counts today's `model_calls` and sums cost, and raises `CapExceeded` if either cap would be exceeded, before any request is made. Cost is computed from tokens at the rate card in `config/thresholds.yaml`. Nothing to build here; call it.
- The user message: language, title, buyer, country, admin level, published, deadline, CPV, stated value, source URL, and the first 3,000 tokens of body. Nothing else.
- `run.py`: scores every notice in status `scored`-pending from step 5, writes `scores`, sets notice status `scored`.

Accept:
- `test_score_schema.py`: a valid tool input round-trips; each invalid variant raises with a message naming the field.
- `tests/unit/test_caps.py` already covers this and passes: with the cap set to 3 via env and three fake `model_calls` rows, the next call raises `CapExceeded` and the test asserts no HTTP request was made, mocking at the transport layer rather than patching the function. Re-run it; do not rewrite it.
- One real scoring run on today's TED survivors completes with schema validity above 95 percent; the number goes in `RUNBOOK.md`.

---

## Step 7. Mini golden set (Saturday 15:30)

Touch: `tests/golden/golden.csv`, `tests/golden/test_golden.py`, `monitor/cli.py` (golden command).

Build:
- Export 30 of today's scored TED notices to `golden.csv` with columns `notice_id, title, label` and stop. The human labels them (relevant / not) by hand. Do not label them yourself.
- `monitor golden`: re-scores the 30 (cached prompt), prints precision at threshold 60, recall, schema validity, mean cost per notice, and the prompt_version. Appends a line to `tests/golden/history.csv`.

Accept:
- `make golden` runs against the labelled file and prints the four numbers; the line is in `history.csv`; `RUNBOOK.md` records the first result.

---

## Step 8. Dedupe, candidates, stager (Saturday 16:30)

Touch: `monitor/dedupe/cluster.py`, `monitor/stage/stager.py`, `monitor/cli.py` (stage command), `tests/unit/test_dedupe.py`, `tests/unit/test_stager.py`.

Build:
- Candidate ids `C000001` from a sequence. For each newly scored notice, look for an existing candidate in the same country: exact content hash; else rapidfuzz `token_set_ratio(title_en, candidate.title_en) >= 85` with deadlines within 7 days; else a shared system name with ratio >= 40 and deadlines within 7 days. Join with `match_method` and `match_score`, take the max score, keep the earliest notice as primary. Otherwise create a candidate.
- Stager: candidates with score >= `stage_threshold`, status `scored` -> `pending_review`, subject to `per_source_daily_cap` counted on the primary notice's source; the rest stay `scored`. Events for `scored`, `staged`, `duplicate_joined`.
- `monitor run` chains fetch all, filter, score, dedupe, stage.

Accept:
- `test_dedupe.py`: the same notice text from two sources joins; two different tenders from the same buyer with the same deadline do not; a French title and its English rendering join through `title_en`.
- `test_stager.py`: the 16th candidate from one source on one day stays `scored`.
- `make run` end to end produces candidates in `pending_review`.

---

## Step 9. Prozorro and Find a Tender (Saturday 18:00)

Touch: `monitor/connectors/prozorro.py`, `monitor/connectors/fts.py`, `tests/contract/fixtures/prozorro.json`, `tests/contract/fixtures/fts.json`, `tests/contract/test_prozorro.py`, `tests/contract/test_fts.py`, `monitor/normalise/` (source-specific mappers only).

Build:
- Prozorro: public API, tenders modified in the last day, DK021 classification mapped to CPV by top-level prefix only (comment says so), titles in Ukrainian left as is with `language = uk`; these will stop at "needs translation" unless they have a passing CPV prefix, in which case they go to the scorer in Ukrainian (the prompt says reason in the original and return title_en).
- Find a Tender: OCDS release packages, CPV filter, English.
- Same rule as step 4: record the fixture from a real call first, build to what is there.

Accept:
- Both contract tests pass on their fixtures; a renamed field raises.
- `make run` over three sources completes; `monitor status` shows three healthy sources and today's cost.

---

## Step 10. Review app and the single write path (Sunday 09:00)

Touch: `review/app.py`, `review/decisions.py`, `review/templates/*.html`, `review/static/style.css` (one file, no framework), `monitor/stage/record.py`, `config/record_defaults.yaml`, `tests/unit/test_record.py`, `tests/review/test_decisions.py`, `Makefile` (review target).

This is the permanent reviewer interface for the whole pilot, not a stand-in for anything built later. There is no later surface that absorbs the features you leave out here, which is a reason to build these five pages properly and still a reason not to build a sixth.

Build:
- FastAPI app bound to `127.0.0.1:8080`, connects only via `db.connect("review")`. No auth this weekend; the binding is the control. Server-rendered Jinja templates; a single CSS file; no JavaScript except a confirm dialog on Reject.
- Since decision 71 (19 September 2026) `/` is a dashboard of the selected region's figures, or all regions, and the queue below is served at `/queue`; the rest of this list is unchanged.
- Pages: `/` queue (pending_review sorted by score, region filter Europe / West Africa, count and estimated reviewer minutes); `/candidate/{id}` (score, title_en, buyer, country, language, original title and body when language != en, summary, matched functions with evidence, system names, flags, deadline, value, source links, duplicate cluster with match method, proposed payload JSON, and the decision form with reviewer name required, Approve / Edit then approve / Reject with a reason from a fixed list plus free text); `/decided`; `/sources` (health table); `/audit` (events, newest first, filter by entity).
- `monitor/stage/record.py`: `build_record(candidate, sources, function_map, record_defaults) -> dict`, a pure function, no database access, no model call. Builds the Opportunity-shaped record field by field per Architecture v0.4 appendix E, which is the export column spec: derived fields (Opportunity Name, Level of Government, Industry, Shipping Country, Funding Source, Partners Involved, Eligibility Requirements Comments, Partner Required Comments, Pricing Notes, FreeBalance Products Required, Next Steps) computed from the candidate and its source cluster; suggested fields (Stage, Proposal Type, Lead Source, Deal Tier, Delivery Model, Customer Type, Eligibility Requirements Met?, Partner Required?, Do we need to register our interest?, Total Opportunity Amount, Probability, Standard or Custom Product Required?, Account Name as a proposed-text field for the reviewer to resolve) computed by the heuristics in appendix E and left fully editable by the reviewer; every BD-only field (Closing Date, Sales Forecasting, Original Closing Date, Legal Support, submission logistics, the whole Pricing Summary except Total Opportunity Amount, everything under Products Required except FreeBalance Products Required, everything under Implementation and Pricing) set to the placeholder from `record_defaults.yaml`. Also sets Product Gaps: any matched function whose `product_status` is not Available, named with its status; empty when every matched product is Available. Round-trips through the same `Score`-derived fields the scorer already produces; no new model call.
- `review/decisions.py`: `approve(candidate_id, reviewer, edits: dict | None)` in one transaction: refuse if reviewer is blank; apply edits over the built payload (any field, not just title/summary/value) with an `edited` event recording before and after for each changed key; insert the (possibly edited) record into `approved_records` with `edited` set accordingly; set candidate status `approved` and `approved_record_id`; events `approved` (actor reviewer) and `created` on `approved_record` (actor `system (post-approval, as <reviewer>)`). `reject(candidate_id, reviewer, reason)`: refuse blank reviewer or blank reason; status `rejected`; event.
- The candidate page's decision panel carries a fixed reminder line above the buttons, in the same panel, not a separate banner: "Before approving, check the CRM for an existing Opportunity on this buyer. The Monitor never searches the CRM; this check is the only duplicate control." Under D31 this is permanent for the pilot, not a placeholder for an automated check arriving in week 5, so word it as a standing instruction. It is worth the one line of template given the Ghana record, and every duplicate that reaches an export batch is logged as a finding and counted at the week 14 gate.
- Orange is used for the decision panel and nothing else.

Accept:
- `tests/unit/test_record.py`: a candidate with a matched IFMIS function and a World Bank source in its cluster produces a record with Funding Source "World Bank (IDA/IBRD)", FreeBalance Products Required including "Core Financial Execution and Reporting", Product Gaps empty because that product is Available, Partners Involved naming the donor source, and every BD-only field equal to the configured placeholder, not blank Python `None` and not an invented value.
- `test_decisions.py`: approve with blank reviewer raises and writes nothing; approve inserts exactly one `approved_records` row containing every column named in appendix E (test asserts key presence, not just a handful); edit-then-approve stores the edited field(s) in the record, sets `edited`, and writes an `edited` event per changed field; reject without reason raises; the whole approve is one transaction (simulate a failure on the events insert and assert no `approved_records` row).
- `make review` serves the five pages; a hand-staged candidate can be approved from the browser and appears in `/decided` and `/audit`.

---

## Step 11. World Bank connector, the export, scheduler, drills, runbook (Sunday 14:15)

Touch: `monitor/connectors/worldbank.py`, `tests/contract/fixtures/worldbank.json`, `tests/contract/test_worldbank.py`, `review/export.py`, `review/templates/export.html`, `tests/review/test_export.py`, `deploy/systemd/*.timer` and `*.service` (or `docker-compose.cron.yml` for the workstation), `Makefile` (export target), `RUNBOOK.md`, `DEMO.md`.

Build:
- World Bank procurement notices API filtered to the pilot countries and the last 2 days; `admin_level = donor`; `covers` from the registry.
- `review/export.py`: `export(range_from, range_to, operator) -> batch_id`, connecting as `monitor_review`. Writes a UTF-8 CSV **with a BOM**, one row per approved record not already exported, columns exactly in the order and naming of Architecture v0.4 appendix E plus `monitor_candidate_id` and `monitor_export_batch`, so the CRM's import mapper matches on headers. Writes a sibling `.manifest.json`: batch id, created_at, operator, row count, date range, reviewer names, and the sha256 of the CSV. Inserts the `export_batches` row and stamps `exported_at` and `export_batch` on each row, all in one transaction. Refuses to export a record twice; re-exporting a named batch is a separate explicit call. One `make export` target and one button on `/export`. No import path, no write-back, no CRM client of any kind: the file is the only thing that leaves the system.
- Scheduler: hourly `monitor run` and `monitor status` to a log file this weekend; daily windows on Monday (`sources/*.yaml` schedule field is authoritative).
- Drills, each written as a short script under `scripts/drills/` and its expected outcome in `RUNBOOK.md`: (1) invalid API key mid-run -> notices parked, no partial writes, run stops; (2) renamed field in a fixture -> that source unhealthy, other sources unaffected; (3) `DAILY_CALL_CAP=2` -> run stops at the third call with a `cap_exceeded` event; (4) approve with blank reviewer via the form -> refused; (5) insert into `approved_records` as pipeline -> permission denied; (6) kill the export halfway -> either a complete CSV with a matching manifest or no file at all, and no row left marked exported.
- `RUNBOOK.md`: start, stop, add a source (YAML plus fixture plus contract test), read health, change a keyword (edit YAML, `make up`, note the new lexicon version), roll back a prompt (git revert of `prompt.py`, `make golden`), where logs are, where the cost line is.
- `DEMO.md`: the ten-minute path, in order, with the URL for each page.

Accept:
- World Bank contract test passes; a WB notice that duplicates a TED or national notice joins its cluster in a real run (find one; if none exists today, construct the case in `test_dedupe.py` and say so).
- `test_export.py`: a batch of three approved records produces a CSV whose header row matches appendix E exactly, a manifest whose sha256 matches the file, three rows stamped with the batch id, and a second export in the same range producing zero rows.
- All six drills produce the documented outcome.
- `git tag weekend-slice`.

---

## The rest of the pilot (steps 12 to 31)

Steps 1 to 11 are the weekend slice. This is not a separate build; it is the same repository, the same acceptance-test discipline, continuing week by week through the week 14 gate. Full Build Plan v1.0 is the companion document that explains why each step sits where it does; this section is what to build. Do not start a step before its week; do not skip a step because a later one looks more interesting.

---

### Step 12. Terraform and the Bedrock cutover (week 3)

Touch: `infra/terraform/*.tf`, `monitor/score/client.py` (client class only), `tests/unit/test_score_schema.py` (re-run, no changes expected).

Build:
- One Terraform workspace: the member account's VPC (default, public subnet, security group with no ingress, egress 443 and 80 only), the EC2 instance (t3.large, Elastic IP, Ubuntu 24.04, instance profile per appendix D), the S3 bucket with versioning and a 180-day lifecycle rule, Secrets Manager entries, CloudWatch alarms from architecture §6. Rebuild-in-under-an-hour is the acceptance bar, not a slogan.
- `client.py`: swap `Anthropic()` for `AnthropicBedrock(region="ca-central-1")`, guarded by an env var (`MODEL_ROUTE=bedrock|direct`) so the direct route stays available for two weeks as the recorded fallback, per Weekend Build Plan v1.2 §8.
- Repository transfer to the FreeBalance GitHub organisation; history moves with it.

Accept: Terraform apply from a clean account reaches a running host in under an hour. `make golden` on Bedrock reproduces the same precision, recall and schema validity as the last direct-API run at the same `prompt_version` (this is the cutover's actual acceptance test, not a visual check).

---

### Step 13. Five more Europe feeds (weeks 3 to 4)

Touch: `monitor/connectors/fts.py` (already built in step 9; extend if the weekend fixture was partial), `monitor/connectors/simap.py`, `monitor/connectors/doe.py`, `monitor/connectors/place.py`, `monitor/connectors/boamp.py`, matching fixtures and contract tests, `sources/*.yaml` for each.

Build: same `FeedConnector` pattern as TED and Prozorro. `doe.py` and `place.py` each carry `admin_level` through from the Land or region in the source field, not as a separate connector per sub-national unit — this is what lets Germany and Spain's sub-national coverage exist without 16 or 17 connectors of their own.

Accept: seven feeds (TED, Find a Tender, Prozorro, simap, DÖE, PLACE, BOAMP) run five consecutive unattended days; `monitor status` shows all seven healthy.

---

### Step 14. Translation stage (weeks 3 to 4)

Touch: `monitor/translate/client.py`, `monitor/translate/run.py`, `config/lexicon_fr.yaml` (fill in from the week 1-2 sign-off), `monitor/filter/run.py` (wire the translate-then-filter order), `tests/unit/test_translate.py`, `tests/contract/fixtures/translate_*.json`.

Build: `translate_notice(notice) -> Translation` — same shape as `score_notice` in step 6: Haiku 4.5, strict JSON `{title_en, body_en}`, system names and acronyms kept as written, one retry on schema failure then parked, logged to `model_calls` with purpose `translate`. Runs only when `stageFilter` (step 5) returns `pass: None` (no lexicon for the language, no passing CPV). The system-name preservation check: every acronym in `SYSTEM_NAMES` (CLAUDE.md) that appears in the original must appear in `title_en` or `body_en`, or the translation is flagged, not silently accepted.

Accept: a French, a German and one other-language fixture translate with schema validity above 98 percent; the acronym check catches a deliberately broken fixture where an acronym was dropped.

---

### Step 15. Donor feeds (week 5)

Touch: `monitor/connectors/worldbank_pipeline.py` (the World Bank pipeline API, separate from the notices API built in step 11), `monitor/connectors/mcc.py`, `monitor/connectors/euft.py`, `monitor/connectors/undp.py`, `monitor/connectors/ungm.py`, `monitor/connectors/undb_mail.py`, `monitor/connectors/ebrd.py`, a `MailConnector` base for the two mailbox sources.

Build: `MailConnector` reads the shared intake mailbox (D6) over IMAP or the Workspace API, filters by sender/subject pattern per source, and produces the same `RawNotice` shape as a feed. `worldbank_pipeline.py` covers project appraisal-stage entries (`admin_level: donor`, `procurement_type: other`); do not conflate it with the notices API from step 11.

Accept: all wave-1 sources (feeds and donor, 16 of the planned 26) run on schedule; first back-test recall computed against the known-misses set from week 1, reported for the feed-only subset.

---

### Step 16. Export hardening and the dry-run import (weeks 3 to 8, parallel with steps 13 to 15)

Touch: `review/export.py`, `review/templates/export.html`, `tests/review/test_export.py`, `docs/export_spec.md`, `docs/import_mapping.md`.

Under D31 and D61 there is no CRM integration in any step of this pilot: no CRM SDK or HTTP client, no CRM OAuth flow, no CRM-side script, no webhook, no CRM credential in Secrets Manager or config. Approved records leave as an export file and by no other route. A commit that adds any of the above is out of scope, not ahead of schedule; design-cop rules 15 to 18 treat it as blocking. CRM integration is deferred and has no design in this pilot.

Build:
- Harden the step 11 export: batch listing and re-export of a named batch on `/export`; export backlog (approved and not yet exported, oldest first) surfaced on the queue page and in `make status`; a CloudWatch alarm when the oldest unexported record is more than seven days old.
- `docs/export_spec.md`: the column list generated from `record_defaults.yaml` and the appendix E order, so the spec cannot drift from the code. Regenerated by a test that fails if the two disagree.
- **The dry-run import (D33) is BD's, done by a person with a file.** Five hand-approved records exported to CSV, imported by the named import operator (D32) into a CRM sandbox using the CRM's standard Opportunities import. The operator maps the columns once and saves the mapping. Every column the mapper rejects is a defect fixed in the exporter, not a field someone hand-keys. Record the saved mapping and any BD amendments in `docs/import_mapping.md`.
- Confirm at the same session the Lead Source value and entry Stage BD wants on an imported record, and that the operator resolves the Account at import using the CRM's own matching rather than anything the exporter proposes.

Accept: the dry-run import completes with every appendix E column mapped and nothing hand-keyed; `docs/import_mapping.md` exists and names the operator and the cadence; the export backlog metric appears in `make status`; `test_export.py` fails if a column is added to `record_defaults.yaml` without appearing in `docs/export_spec.md`. This gates shadow-mode entry alongside step 22: do not enter shadow without a proven import path for the records shadow will produce.

---

### Step 17. West Africa, first four (week 6)

Touch: `monitor/connectors/benin.py`, `senegal.py`, `nigeria.py`, `ghana.py`, `monitor/connectors/browser_base.py` (Playwright container, separate service in `docker-compose.yml`), fixtures and contract tests for each.

Build: Benin and Senegal as `PageConnector` on structured listings. Ghana (GHANEPS) and any Côte d'Ivoire work that lands this week as `BrowserConnector`, running Playwright in its own container so the cheap fetchers never carry a browser dependency.

**Corrected 2026-09-13, from the onboarding measurements rather than the plan.** Three of the four classes above were wrong. Senegal publishes a public JSON API (`/anon/tdo`) and is a `FeedConnector`, live since 2026-09-13. Ghana's "Current Tenders" listing is server-rendered HTML that answers an anonymous GET, walked two pages deep with httpx: a `PageConnector`, not a browser, live since 2026-09-13; the OCDS endpoint the plan would have preferred returns HTTP 500 on every payload shape tried. Benin is the one that does need a browser, and it needs a person at a real one first to record the row selector the sandbox cannot capture (decision 20). Nigeria's federal portal is a retrospective register measured at zero live tenders and is dropped from wave 1 pending a named person's decision (open decisions, West African wave). Côte d'Ivoire is a readable HTML table under no terms page, still `pending`.

Accept: all four have a fixture and contract test; a deliberately stale Playwright selector fails loudly rather than returning zero silently. Met for Senegal and Ghana; Benin waits on the selector; Nigeria is dropped.

---

### Step 18. West Africa, the hard ones (weeks 6 to 7)

Touch: `monitor/connectors/burkina_faso.py`, `mali.py`, `gambia.py`, `liberia.py`, `monitor/normalise/ocr.py` (Textract wrapper), fixtures, contract tests.

Build: Burkina Faso's weekly PDF bulletin: text-layer detection first (`pdftotext` output length as the signal), Textract only when the layer is absent. Mali, The Gambia, Liberia are simpler Page connectors, added here rather than week 6 only because sequencing them after the OCR work means a Textract problem surfaces with runway left before week 8's dedupe deadline.

Accept: the Burkina bulletin fixture (a scanned week) OCRs to text with the expected item count in range; Mali, Gambia, Liberia contract tests pass on their fixtures.

**Corrected 2026-09-13.** No Burkina issue sampled across five years is a scan (decision 19), so the OCR half of the acceptance test has no fixture and the text-layer route is the one that runs. What the bulletin actually needed was an acquire-stage contract, not OCR: one PDF payload maps to thirty-odd notices, so `monitor/fetch.py` now decodes a payload by its declared mime and a mapper may return a list (decision 51). Mali is a JSON feed the portal's own app calls, not a page, and is live since 2026-09-13 with a normaliser. Liberia was live on 2026-09-12. Gambia is a browser source under no terms page and stays `pending`.

---

### Step 19. Côte d'Ivoire and Sierra Leone; French lexicon tuning (weeks 7 to 8)

Touch: `monitor/connectors/cote_divoire.py` (if not finished in step 17), `monitor/connectors/sierra_leone.py`, `config/lexicon_fr.yaml` (edits only, no code), `tests/unit/test_filter.py` (new French cases from real fetched notices).

Build: the tenth and final wave-1 West African connector. Then: run the filter against a week of real West African French notices, add every real phrase the lexicon missed as a new case in `filter_cases.yaml` before adding it to the lexicon, so the miss is provably fixed, not just patched.

Accept: ten of ten wave-1 West African portals have fixtures and contract tests; the French lexicon's miss rate on this week's real notices is re-measured and improved from its week-1 baseline.

---

### Step 20. Cross-language dedupe and Sonnet escalation (week 8)

Touch: `monitor/dedupe/cluster.py` (extend the weekend's version), `monitor/score/run.py` (rescore path), `tests/unit/test_dedupe.py` (cross-language cases), `tests/unit/test_rescore.py`.

Build: dedupe now runs on the English rendering (`title_en`, whether native or translated) so a French SIGMAP notice and its World Bank English mirror can join; shared system name plus country plus deadline within seven days is checked after the fuzzy title match, not instead of it. Rescore: any `scores.relevance` between 40 and 70 gets a second call on Sonnet 5 with the same prompt; the higher-confidence result (by the `confidence` field, tie-break to Sonnet) is what stages.

**Two corrections to the paragraph above, both dated 2026-09-12.** The band is now **[25, 70]**, following `stage_threshold` down so it covers everything a reviewer can see rather than only the top of it. And "is what stages" describes what the code does when it runs, not what the pipeline does: this escalation had **no caller anywhere outside `tests/unit/test_rescore.py`** until 2026-09-12 - no CLI verb, and `run_full_pass()` is fetch, filter, score, stage - so no candidate that has ever reached the review queue has had a second opinion on it. It is reachable now as `monitor rescore` / `make rescore` and is deliberately not a stage of `monitor run`, because each row is a paid Sonnet call and `rescore()` is explicit that sizing a run is a person's job. Whether it becomes automatic is decision 47, and it should be run once by hand against real data first: its replace-or-hold logic is proven only against its tests.

Accept: `test_dedupe.py` includes a French-original and English-translation pair that joins; a rescore test confirms a 55-scoring notice gets a Sonnet call and a 90-scoring notice does not.

---

### Step 21. Golden set to 150; weekly metrics job; metrics page (week 8)

Touch: `tests/golden/golden.csv` (expand from 30 to 150), `monitor/health/metrics.py`, `review/templates/metrics.html` (a sixth page, and the only one added after step 10).

Build: 150 notices (60 relevant, 90 not) drawn from TED, Prozorro, World Bank, Senegal, Ghana and one Balkan portal (via EBRD/World Bank donor feeds, since Balkan connectors themselves are wave 2), at least 50 non-English, labelled by Matthew and me, not by the agent. `metrics.py` runs weekly (or on demand via `make metrics`): precision, recall, translation rejection rate, connector break rate, written to a `metrics` table the review app's metrics page reads. Export backlog and the duplicate count from D31 are on the same page, since both are week 14 gate numbers.

Accept: `make golden` on the 150-notice set reports precision above 60 percent, schema validity above 99 percent; `make metrics` produces a report with no manual steps.

**Measured 2026-09-16, not met.** The 150 were labelled by one BD reviewer (16 relevant, 134 not, not the 60/90 split planned, which is what the corpus actually contains). `make golden` reports precision 33.3% and recall 56.2% at 25 on a fresh re-score, schema validity 100%; the pipeline's live scores on the same notices give 50% and 69%. The free filter drops no relevant notice. The scorer's false positives are mostly finance *services* rather than finance software, and five of the sixteen positives are "worth monitoring" rather than a bid, so the gate as written measures a boundary nobody has drawn. Decision 57 lists what a person decides before the prompt is touched. The set was drawn before any West African portal was live and holds none; a second draw follows two weeks of those sources.

---

### Step 21b. Published value and USD conversion (2026-09-12)

Touch: not a step BUILD_ORDER had scheduled — design-cop's finding was that it landed with no step and therefore no acceptance tests, so this entry is written against what actually shipped rather than a plan made in advance. `migrations/012_published_value_and_currency.sql` through `015_fx_rates_key_includes_the_publisher.sql`; `monitor/fx/` (new: `config.py`, `convert.py`, `nbu.py`, `store.py`) and `config/fx.yaml` (new); `monitor/normalise/value.py` (new) and the value-reading change to `boamp.py`, `doe.py`, `euft.py`, `fts.py`, `liberia.py`, `prozorro.py`, `ted.py`; `monitor/score/schema.py`, `prompt.py`, `client.py`, `run.py`; `monitor/stage/stager.py`, `record.py`; `config/record_defaults.yaml`; `monitor/registry/load.py`; `review/app.py`, `decisions.py`, `templates/queue.html`, `templates/candidate.html`; `scripts/backfill_values.py` (new, run once); `monitor/fetch.py`; `deploy/systemd/monitor-fx.service` and `.timer`, `deploy/docker-compose.cron.yml`; `RUNBOOK.md`.

Build: the scorer stops being asked for a contract value — checked against the full notice text on every one of the eight it ever produced, none appeared in that text in any form, and migration 012's header carries the check. The normalisers read each source's own structured value field instead and store the published amount in its own currency (rule 9). One rate publisher, the National Bank of Ukraine (chosen because the ECB does not quote UAH, and UAH is most of the corpus), supplies a daily rate through its dated endpoint on its own schedule, 09:00 UTC, outside `monitor run`. Staging derives and stamps `estimated_value_usd` at that day's rate and refuses to stage anything, including a candidate with no stated value, when no rate is fresh enough. The review app and the record builder render the published figure, its currency and the USD derivation, driven by a `value_basis` key in `record_defaults.yaml`, instead of a hardcoded `USD {:,}`. `scripts/backfill_values.py` ran once against every notice and candidate already held.

Accept: `tests/unit/test_score_schema.py::test_the_model_can_no_longer_report_a_value_at_all` — `Score` has no value field. `tests/contract/test_fx.py` — the dated endpoint returns the day asked for, a weekend day carries Friday's rate under its own date, an undated response is refused for a day it does not match. `tests/unit/test_stager.py::test_a_published_value_is_carried_and_converted_at_a_rate_that_is_stored` and `::test_a_notice_stating_no_value_stages_with_nothing_rather_than_a_zero`. `tests/unit/test_backfill_values.py::test_pass_two_converts_a_candidate_at_the_stored_rate`. `tests/review/test_app.py::test_candidate_page_shows_a_value_with_its_usd_derivation_and_rate` and `::test_candidate_page_shows_a_value_with_no_usd_rate_held`. `tests/unit/test_registry.py::test_a_bad_value_basis_fails_on_load_not_on_a_reviewers_page`. Measured directly against the database: `estimated_value / value_rate` reproduces `estimated_value_usd` on all 60 candidates that carry one.

Did not: re-score the 151 notices already scored before this landed — their old `estimated_value_usd` was dropped (migration 012), not recomputed, because it was never read from the text correctly to begin with; decided 2026-09-13 that they stay under their old prompt version (decision 53). The export basis was decided the same day: `value_basis: published` — the amount and currency as the notice stated them, with the USD derivation in Pricing Notes (decision 52); the `usd` branch stays built and tested as one YAML flip. Did not resolve BOAMP's 24 lot-only notices or Prozorro's VAT-inclusive/exclusive split; both are carried as published and read nowhere else.

---

### Step 22. Security review (week 9, before shadow entry)

Touch: nothing new is built; this step is an audit against what exists. Output: `docs/security_review_2026-09.md`.

Build: secret scan across the whole repository history (not just the current tree); IAM review against appendix D of Architecture v0.4, confirming that no identity anywhere in the system holds a credential to any business system, because under D31 none exists; confirming `monitor_pipeline` still has no privilege on `approved_records` in the deployed database, not only in the migration file; dependency audit; confirm no inbound network path exists anywhere; confirm Session Manager logging is on for the one instance.

Accept: the review document exists, is dated, and names me as the reviewer under the Acting CSO mandate, with every finding either closed or explicitly deferred with an owner and date. This gates shadow-mode entry; do not proceed to step 23 without it.

### Step 22a. CRM-neutral wording in code, config and templates (week 9, before 22b)

Touch: `review/templates/candidate.html`, `export.html`, `metrics.html`, `review/export.py`, `review/app.py`, `review/decisions.py`, `monitor/stage/record.py`, `monitor/registry/record_defaults.py`, `monitor/health/metrics.py`, `monitor/models.py`, `monitor/normalise/*.py` comments, `config/record_defaults.yaml`, `config/thresholds.yaml`, `config/fx.yaml` comments, `scripts/generate_export_spec.py` and the regenerated `docs/export_spec.md`, `infra/terraform/identity.tf` and `secrets.tf` comments, the tests that assert the reminder text.

Build: replace every reference to a named CRM vendor with "the CRM" or "CRM import", in strings, comments, docstrings and generated docs (D61). The decision-panel reminder reads: "Before approving, check the CRM for an existing Opportunity on this buyer. The Monitor never searches the CRM; this check is the only duplicate control." No behavioural change: export columns, their order and naming, the BOM, the manifest and the rejection-reason list are untouched. Applied migrations are not edited; `001_schema.sql` keeps its comment. `docs/reference/` and `prototype/` are historical and keep theirs. Contract fixtures are recorded data and are never edited.

Accept: a tracked-file search for the vendor name outside `docs/reference/`, `prototype/`, `migrations/` and `tests/contract/fixtures/` returns nothing; `make test` passes; an export of the same three approved test records is byte-identical in its header row before and after.

---

### Step 22b. Regional model (week 9, gates step 23)

Touch: `docs/regions.yaml` moved to `config/regions.yaml`, `monitor/registry/load.py` (`CONFIG_KINDS` gains `regions` and `users`, and the `config_versions` kind vocabulary with it), `config/thresholds.yaml` (the `regions:` block leaves; `geography` stays), `config/record_defaults.yaml` (`industry_by_region` keyed by region id), `migrations/018_regions.sql`, `monitor/registry/load.py`, `monitor/stage/region.py`, `monitor/stage/stager.py`, `monitor/filter/run.py`, `sources/ted.yaml`, `sources/worldbank.yaml`, `sources/worldbank_pipeline.yaml`, `sources/euft.yaml`, `sources/undp.yaml`, `sources/ungm.yaml` (`covers`), `review/app.py` and `queue.html` (region filter), `monitor/health/metrics.py`, `tests/unit/test_regions.py`, `tests/unit/test_stager.py`, `tests/unit/test_filter.py`.

Build:
- `config/regions.yaml` is the single statement of region membership (D62): ten regions from the sales-regions workbook, each with id, name, lead, team and `status: inactive | onboarding | build | shadow | live` (decision 65; Europe & West Africa starts at `build`); `pilot_exceptions` naming BJ BF CI ML MR NE SN TG with the date and the decision (D64); `excluded` and `unassigned`. Validated on load with Pydantic, version-hashed into `config_versions` like the other config files. Codes quoted.
- `018_regions.sql`: `regions(id pk, name, lead, status)`, `region_countries(country pk, region_id fk)`, `pilot_exceptions(country pk, reason, decided_on)`, seeded from the YAML by `make up`. `candidates.region` becomes a foreign key to `regions.id`; existing rows are backfilled from their country, and a row whose country resolves to no region fails the migration rather than being guessed.
- `monitor/stage/region.py`: `region_for(country) -> RegionId` and `region_status(country) -> Status` (an exception country takes the status of the pilot region), both reading the loaded config. Nothing else decides a region.
- Free filter gains a geography stage ahead of CPV and lexicon: a notice whose country is outside pilot geography is `filtered_out` with reason `outside pilot geography (<region id>)`, or `country unassigned` / `country excluded`, before any model call. Kept, not deleted, so activating a region is a config change and a re-filter.
- Every source's `covers` must be a subset of pilot geography, asserted at registry load. TED drops BG CZ HU PT RO SK. The World Bank, EU Funding and Tenders, UNDP and UNGM lists add AM and GE where the source publishes for them.
- Queue region filter lists regions from the table, active regions and the exception first. Metrics break down by region.
- Validation rejects a file with more than one region in `build` or `shadow` (decision 65).

Accept:
- `test_regions.py`: every ISO 3166 alpha-2 code appears exactly once across regions, `excluded` and `unassigned`; every key parses as a string (the `NO` trap); every `pilot_exceptions` code belongs to an inactive region; the workbook's 41 Europe & West Africa codes are exactly the pilot region's list.
- A BG notice is `filtered_out` with the geography reason and `model_calls` is unchanged by it. An SN notice stages with `region = mena_francophone_africa` and appears in the pilot queue.
- After migration, no candidate has a null region and the pre-existing 153 carry the region their country resolves to (19 West Africa, 131 Europe, 3 Balkans and Ukraine on 2026-09-19; eleven of them, in BG CZ RO SK, resolve to a region outside the pilot and are not guessed into it).

---

### Step 22c. SSO access and named users (deferred to R0 by decision 67; does not gate step 23)

Decision 67 (19 September 2026): the pilot runs with at most two users, Matthew and Sara, on localhost over the tunnel with a typed reviewer name, exactly as steps 3 to 22 built it. Nothing below is built before the week 14 gate; it is the first item of R0 in `docs/regional_rollout.md`. Kept here unchanged so R0 does not have to rediscover it.

Touch: `infra/terraform/verified_access.tf`, `infra/terraform/network.tf`, `config/users.yaml`, `migrations/019_users.sql`, `review/auth.py`, `review/app.py`, `review/decisions.py`, `review/templates/*.html` (the reviewer-name field goes), `scripts/drills/drill4_blank_reviewer.py`, `tests/review/test_auth.py`, `tests/review/test_decisions.py`, `RUNBOOK.md` (add a user, remove a user), `pyproject.toml` (`pyjwt[crypto]`, named in the commit message: the access layer's assertion is an ES256 JWT and verifying it by hand is the wrong kind of boring).

Build:
- AWS Verified Access in ca-central-1 fronts the review app (D63): an OIDC trust provider on the company identity provider, a group policy admitting only the users in `users.yaml`, an endpoint to the review container on the private instance. No public IP on the instance, no internet-facing load balancer, no port open in the security group except from the Verified Access endpoint. The pipeline keeps no inbound path. Confirm the service's availability and price in ca-central-1 before writing the Terraform, and record both in the commit.
- `config/users.yaml`: 5 to 10 entries, each with email, display name, `role: admin | reviewer | viewer` and `regions` (region ids, or `all` for admin). Loaded into `users` and `user_regions` by `make up`, version-hashed. Adding a person is a config change.
- `review/auth.py`: verifies the signed user-context header on every request against the access layer's published key, maps the email to a `users` row; unknown email is 403 and an `events` row. One verification path: tests and local development mint assertions with a local key pair (`scripts/dev_identity.py`); nothing skips verification.
- Decisions record `reviewer_user_id` and the email as the event actor. `decisions.py` refuses a decision on a candidate outside the user's regions, server side (rule 25). Viewers read everything and decide nothing.

Accept: a request with no assertion is 401; a forged or expired assertion is 401; a valid assertion for an email not in `users.yaml` is 403 and logged; a reviewer for `europe_west_africa` can decide a GH candidate and an SN exception candidate (the exception grants pilot reviewers authority over the eight codes; `users.yaml` says so explicitly) and is refused on a candidate from any inactive region; drill 4 is rewritten to these cases and passes; the step 22 checks for inbound paths are re-run against the deployed host and the result appended to the security review.

---

### Step 23. Shadow mode entry (week 9)

Touch: `config/regions.yaml` (`europe_west_africa: status: shadow`), `review/export.py` (a batch includes only regions in `live`; shadow exports are dry runs), `monitor/health/status.py` (each region's status), scheduler config (daily windows replace the weekend's hourly), `RUNBOOK.md` (shadow-mode section). There is no global `monitor mode` command: mode is per region and lives in config (decision 65), so every later region uses the mechanism the pilot builds here.

Steps 22a and 22b are met before this step starts; 22c is deferred to R0 (decision 67).

Build: the region status gates live operation while still staging candidates to the review queue in Postgres (nothing is notified in any mode this phase, per D31 and design-cop rule 18, so what the status gates is real export batches, not notifications); the reviewer and backup are trained this week (five sessions, per architecture §9.2's user-acceptance test) on real shadow-mode data, including French and translated candidates.

Accept: daily runs across all 26 wave-1 sources for five consecutive days with no export batch released to BD (dry-run exports only, discarded); reviewer completes the five acceptance sessions, approving, editing and rejecting a real candidate in under two minutes without help.

---

### Step 24. Translation sample (week 9, parallel with step 23)

Touch: nothing in code; a spreadsheet or a short script pulling 30 translated notices across French, German, Ukrainian, Arabic, Albanian for the two human readers.

Build: hand the sample to a French reader and a German reader (two hours each, arranged in week 8); Ukrainian, Arabic and Albanian get the model's own back-translation as a cheaper check, flagged to the reviewer rather than independently human-read this round.

Accept: the sample is accepted by both readers, or specific failures are logged as `"translation wrong"` rejections and the prompt is revised before shadow-to-live entry.

---

### Step 25. Wave 2 connectors, one at a time (weeks 9 to 12, parallel with shadow and live)

Touch: `monitor/connectors/albania.py`, `bosnia.py`, `kosovo.py`, `montenegro.py`, `north_macedonia.py`, `mauritania.py`, `niger.py`, `togo.py`, `kaduna.py`, `lagos.py`, `monitor/connectors/isdb.py`, `boad.py`, `monitor/connectors/oecd_mail.py`, remaining EU below-threshold portal connectors in Matthew's priority order.

Build: same connector-class discipline as wave 1 — a fixture and a contract test before a source is enabled, no exception for being "week 10 already." Balkan portals are `BrowserConnector`, behind registration; Kaduna and Lagos are `PageConnector` per the sub-national inventory sheet.

Accept: each connector is added to the enabled set only when its own fixture and contract test pass; by end of week 12, 37 of the planned 37 wave-2 connectors are enabled (63 total with wave 1).

---

### Step 26. Live-mode entry (week 10 to 11)

Touch: `config/regions.yaml` (`europe_west_africa: status: live`, committed with the gate evidence in the message), scheduler config, `docs/import_mapping.md` (cadence confirmed).

Build: entry gate checked, not assumed: five consecutive shadow days above 30 percent precision in the pilot region and in the francophone exception, each measured separately, translation sample accepted, the step 16 dry-run import accepted by BD, and Matthew's sign-off recorded. Only then does the status change to `live` release real export batches on the agreed cadence, recommended Monday alongside the metrics job. Notifications remain off for the whole pilot (D31).

Accept: the gate's four conditions are each recorded with a date and a number, not just asserted in a status message; live mode, once entered, produces its first real export batch and the named operator imports it into the CRM within the agreed cadence, with the batch id and the import date recorded.

---

### Step 27. Weekly tuning cadence (weeks 11 to 14)

Touch: `scripts/weekly_tuning.md` (a checklist, not code), `monitor/health/metrics.py` (extend with per-region, per-language breakdowns).

Build: Monday, the metrics job runs automatically. Tuesday, a 30-minute session reviewing rejections by reason, precision by region and language, recall, connector breaks. Wednesday, any resulting change (a lexicon phrase, a threshold, a geography weight) ships as a versioned config change, never a code change, with `lexicon_versions` or `thresholds.yaml`'s own change history as the record.

Accept: four consecutive weekly cycles (11 through 14) each produce a dated tuning note; every week's export batch is produced and imported, with no batch older than the cadence sitting unexported or unimported.

---

### Step 28. D25 reviewer-load check (week 10, executed by week 12 if triggered)

Touch: nothing unless the split is triggered. If it is: the second reviewer (Sara, by decision 67's default) takes that region or the exception countries by agreement, no code change; `config/users.yaml` records it once 22c exists.

Build: measure reviewer minutes per region from the audit log (already recorded on every decision since the weekend). If the pilot region or the francophone exception exceeds three hours a week, add a second reviewer for it.

Accept: a measured number exists at week 10, cited in the week 14 gate report either way.

---

### Step 29. D4 dedupe-depth decision (week 8 measurement, week 13 decision)

Touch: nothing unless embeddings are brought forward. If they are: `monitor/dedupe/embeddings.py`, pgvector index migration.

Build: measure the cross-language duplicate rate actually observed in shadow and live. Only build the embeddings path if it exceeds 5 percent; otherwise this step is a measurement and a decision, not code.

Accept: the measured rate is in the week 14 gate report; if embeddings were built, a test shows an English/French pair joining that the fuzzy-plus-system-name method alone missed.

---

### Step 30. Week 14 gate report

Touch: `docs/gate_report_week14.md`, generated from `metrics.py`'s history, not written free-hand.

Build: the seven numbers from Full Build Plan v1.0 §4 (Live phase): back-test recall overall and non-English, precision at the queue, translation-wrong rate, time to detect, reviewer load by region, connector break rate by class, and qualified opportunities and pipeline value sourced by the Monitor by country. The last of these cannot come from the pipeline's own tables under D31, because nothing is written back after export: BD supplies it at the gate from the CRM, joined on `monitor_candidate_id`, and the report says so rather than implying the Monitor measured it. Add two numbers D31 makes necessary: the duplicate count (approved records that turned out to duplicate an existing Opportunity) and export health (batches produced, records imported, columns rejected). Plus the D4 and D25 decisions from steps 28 and 29, reviewer load and precision by region, and the readiness of the first rollout region against the entry gate in `docs/regional_rollout.md`. Whether CRM integration is taken up at all is a separate piece of work after the gate, and the duplicate count is its first input.

Accept: the report exists, every number traces to a query against the pipeline's own tables (no number is asserted without a query attached in an appendix), and the business owner named in week 0 has a documented decision: scale, extend, or stop.

---

### Step 31. Handover documentation (week 14, not a build step)

Touch: `RUNBOOK.md` (final pass), `docs/handover.md`.

Build: nothing new; consolidate the runbook, the security review, the gate report and the decision register's still-open items into a handover package for whoever holds the 0.5 to 1 FTE maintenance role if the pilot scales, per Architecture v0.4 §9.6.

Accept: a new reader (not me) can follow the runbook to add a source, read health, and understand the checkpoint without asking a question that isn't already answered in these documents.

## After the pilot (not this build)

`docs/regional_rollout.md` (decision 65) is the plan: R0 platform readiness, then one region at a time through `onboarding`, `build`, `shadow` and `live`, each with the same entry and go-live gates. Order: North America first (owner Manuel, CEO), then Caribbean & Latin America, Central & Southeast Europe, MENA & Francophone Africa (gated on the VP hire), East & Southern Africa, Lusophone, Asia & the Pacific, and Pakistan, Central Asia and Türkiye. This file gets a new set of steps for R0 and for each region once the week 14 gate decides to proceed. CRM integration is not part of any region's activation.
Weeks 3 to 8: translation stage, West African portal connectors, French lexicon tuning, golden set to 150.
