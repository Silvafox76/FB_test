# Subagent set for the PFM Opportunity Monitor, Postgres-only scope

Seven project subagents, weeks 0 to 14, with no CRM integration in scope. Approved records live in Postgres and leave as an export file.

## What changed from the previous set

`creator-builder` is gone. `review-app-builder` takes its place and is now the permanent review interface rather than a weekend stand-in, and it owns the export as well as the queue and the write path. `design-cop` gains a scope block that treats any CRM client, OAuth flow, Deluge script or webhook as a blocking finding, so the boundary is enforced by review rather than by memory. `eval-harness` swaps the Creator drills for export integrity drills and a second role-boundary drill. `ops-analyst` reads the queue from Postgres directly and gains export backlog to its daily check. `source-onboarder` and the two connector builders are unchanged.

## Table naming

`crm_leads` becomes `approved_records`. Nothing has been built yet, so the rename is free today and stops the table implying an integration that does not exist. The columns still follow the CRM's Opportunity shape, because that is what the export has to produce.

## Install

From the repository root:

```bash
mkdir -p .claude/agents
cp *.md .claude/agents/
git add .claude/agents && git commit -m "Pilot subagent set, Postgres-only scope"
```

Restart Claude Code if the session predates the directory. Files edited on disk mid-session are not picked up; edits made through `/agents` take effect immediately. Verify with `/agents` in session or `claude agents` from the shell.

## File ownership

| Agent | Writes only | Model |
| --- | --- | --- |
| source-onboarder | `sources/*.yaml`, inventory rows | sonnet |
| feed-connector-builder | `monitor/connectors/feed.py`, `tests/fixtures/`, `tests/contract/` | sonnet |
| portal-connector-builder | `monitor/connectors/page.py`, `browser.py`, `tests/fixtures/`, `tests/contract/` | sonnet |
| review-app-builder | `review/`, `monitor/stage/record.py`, `config/record_defaults.yaml` | sonnet |
| eval-harness | `tests/golden/`, `tests/backtest/`, `reports/` | sonnet |
| ops-analyst | nothing, read-only by tool allowlist | sonnet |
| design-cop | nothing, read-only by tool allowlist | sonnet |

The main session owns everything else: Terraform, migrations and the two roles, normaliser, translator, filter, scorer, deduper, stager, `function_map.yaml`, both lexicons and `thresholds.yaml`.

## Invocation by phase

**Weeks 0 to 2, mobilise.** `source-onboarder` works the inventory in wave order, one row per invocation, three or four in parallel since they never touch the same file. Output is the signed-off registry with the access and ToS columns. No code yet, no other agent runs.

**Weeks 3 to 5, feeds.** Main session builds increment 1 end to end yourself, including the `FeedConnector` base, the normaliser, the filter and the scorer. Every later agent copies that shape, so delegating it is a false economy. From Find a Tender onward, `feed-connector-builder`, one source per invocation, two or three in parallel. `review-app-builder` for the queue and the write path in parallel, because the checkpoint is the deliverable that has to be real before anything else matters. `design-cop` before every commit.

**Weeks 6 to 8, portals.** `portal-connector-builder` for the ten West African portals, one at a time and only after `source-onboarder` has cleared the ToS position. These are the connectors that break, so the review is slower and the parallelism lower. `review-app-builder` for the record builder and the export. `eval-harness` at each increment gate and as the golden set is labelled.

**Weeks 9 to 10, shadow.** `ops-analyst` every morning. `eval-harness` weekly and for the translation sample. Wave 2 connectors continue through the two builders. First export dry run against hand-approved candidates.

**Weeks 11 to 14, live.** `ops-analyst` daily and for the weekly tuning input. `eval-harness` weekly and before every config release. Build agents only when a connector breaks. Export on the cadence the reviewer and BD agree.

Explicit invocation beats automatic delegation: "Use the portal-connector-builder subagent to build the Senegal ARCOP connector per BUILD_ORDER."

## Three warnings worth keeping

A subagent returns a summary, not a diff. It moves review load into `git diff`; it does not remove it. At 26 connectors in wave 1 and 63 by week 12, reviewing generated code is the real constraint.

Producing connectors faster does not reduce the maintenance the pilot exists to measure. Reaching 63 connectors in four weeks instead of six means the week 14 break rate is measured on connectors four weeks old, and the number that goes to leadership is softer. Keep the wave dates as planned.

With no CRM integration, there is no automated check for an opportunity BD is already tracking. The reviewer is the only control against duplicates, and the standing reminder on the candidate page is the whole mechanism. Treat every duplicate that reaches an export batch as a finding, not a nuisance.
