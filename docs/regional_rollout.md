# Regional rollout: one region at a time, after the pilot

19 September 2026. Ryan Dear. Decision 65 in `docs/open_decisions.md`. Supersedes phases 4 and 5 of
Pilot Plan v0.4 and section 9.6 of Architecture v0.4 where they describe the shape of scale-up.

## The approach in one paragraph

Nothing rolls out until the pilot passes its week 14 gate with a decision to scale. After that, one
platform-readiness phase (R0), then regions come online one at a time, each through the same five
statuses and the same two gates. Only one region is ever in build or shadow. The next region's source
onboarding, which is paperwork and not code, may run while the current region is in shadow. A region
never starts without a named lead and a named reviewer, whatever its place in the order. The order
below is a recommendation; the gates decide.

## Region statuses

Held in `config/regions.yaml`, changed only by a commit that cites the gate evidence.

| Status | Sources | Queue | Export | What it means |
| --- | --- | --- | --- | --- |
| `inactive` | none | none | none | Exists for users, routing and reporting. Notices from it are held at the free filter with a reason, before any model call. |
| `onboarding` | none enabled | none | none | Source inventory and ToS work under way. No connector code. |
| `build` | enabling one by one | staged for testing, not worked | none | Connectors, fixtures, contract tests, lexicon or translation path, region golden set. |
| `shadow` | all wave-1 sources for the region | worked daily by the region's reviewers | dry runs only, discarded | Precision and reviewer load measured. |
| `live` | all enabled | worked | released on the agreed cadence | The region's export batches go to BD. |

Europe & West Africa is in `build` today and moves to `shadow` at step 23 and `live` at step 26. This
replaces the global `monitor mode` flag in the original step 23: mode is per region and lives in
config, so the pilot builds the mechanism every later region uses.

A validation test enforces the one-at-a-time rule: at most one region in `build` or `shadow` at once.

## R0. Platform readiness (after the gate, before any second region)

One phase, about four weeks, because each item below is either cheaper to do once or dangerous to
discover under a second region's load.

- **Production shape.** Move from the pilot host to the production shape in Architecture v0.4
  section 6. Database backups and restore tested on a real restore.
- **Model budget per region.** The daily call and USD caps become per region in `thresholds.yaml`,
  and the system-wide cap is their sum. A second region must not be able to starve the first.
- **Maintenance owner named.** The 0.5 to 1 FTE maintenance role exists as a person, not a line in a
  plan. Every added region adds connectors that break; this is the constraint that actually limits
  pace.
- **Region activation runbook.** `RUNBOOK.md` gains the activation checklist below, rehearsed once
  on an inactive region in a staging database.
- **Reporting by region.** The weekly metrics and the gate report template produce every number per
  region without hand-work.
- **Access.** Verified Access policy and `users.yaml` handle users across regions; a viewer role for
  regional VPs who want visibility before their region is live.

Exit: all six done, and the pilot region has run live for four consecutive weeks with no connector
down longer than its repair target.

## Per-region activation: the same steps every time

1. **Entry gate (`inactive` to `onboarding`).**
   - A named regional lead is in post.
   - At least one named reviewer is committed at 30 minutes a day.
   - The previous region is `live`, or in `shadow` with no open blocking finding.
   - The maintenance owner confirms the break rate and repair time across all live regions are inside
     the targets set at the week 14 gate.
   - Unassigned countries in the region's area are resolved in the workbook.
2. **Onboarding.**
   - The region's source inventory is built from the BD worksheet for that region. Where no worksheet
     exists, building one is the first task.
   - `source-onboarder` clears ToS row by row, in the order the regional lead ranks the countries.
   - The region's lead names five to ten known misses for the back-test, at least two not in English.
3. **Build.**
   - Donor and aggregator feeds are extended to the region's countries first, because they are cheap
     and they carry most donor-funded PFM work.
   - National portals follow, in the lead's order.
   - Languages without a lexicon go through the translation stage and the English lexicon. A native
     lexicon is added only when measured volume justifies it, as the pilot did for French.
   - The region's own golden set of at least 50 notices is labelled by its reviewer, not by the pilot's.
4. **Shadow.** At least two weeks of daily runs worked by the region's reviewers, with the five
   acceptance sessions from step 23 repeated for new reviewers.
5. **Go-live gate (`shadow` to `live`).**
   - Five consecutive shadow days above 30 percent precision for the region.
   - Region golden-set precision at or above the pilot's gate.
   - Back-test recall reported, overall and non-English.
   - Translation sample accepted for any new language.
   - Reviewer load under three hours a week per reviewer.
   - The lead's sign-off recorded with a date.
6. **Live.** First export batch released, imported by BD, batch id and import date recorded. The next
   region may now enter `build`.

## Recommended order

| # | Region | Lead | Why this position | Indicative span |
| --- | --- | --- | --- | --- |
| 0 | Europe & West Africa, plus the francophone exception | Matthew | The pilot | Weeks 0 to 14 |
| 1 | Central & Southeast Europe, and Azerbaijan | Elena Enache | Five of nine countries are already on TED, so the region tests that activation is mostly a config change, at the lowest cost, before a heavy build. Serbia, Moldova and Azerbaijan need portals. | 6 to 8 weeks |
| 2 | Caribbean & Latin America | Adrian Waldman | The problem the initiative started from: a state-level Mexico deal missed. Highest expected value. Needs a LATAM worksheet, which does not exist yet, and sub-national Mexico. Onboarding overlaps R1's shadow. | 14 to 18 weeks |
| 3 | MENA & Francophone Africa | TBD (new hire) | Eight countries already running under the pilot exception, so the build is the Arabic-language and North African half. Cannot start until the VP is hired; if the hire lands early, this moves to position 2. The exception ends here. | 10 to 14 weeks |
| 4 | East & Southern Africa | Bridget | English-language, portals already watched by hand from the BD worksheet, reuses the pilot's West African connector patterns. | 10 to 12 weeks |
| 5 | Lusophone | Marcos | Portugal is a TED flip. Brazil's national procurement portal is large; Mozambique and Angola need portals. Portuguese through translation first. | 10 to 14 weeks |
| 6 | Asia & the Pacific | Boon (Ben) | 35 countries, many languages, ADB as the anchor donor feed. The largest single build. | 14 to 20 weeks |
| 7 | Pakistan, Central Asia, Türkiye | Taimur Malik | Seven countries, five scripts and languages, portals of mixed openness. | 10 to 12 weeks |
| 8 | North America | TBD (new hire) | Cheap to build on open federal data, but no lead and the smallest PFM tender volume. Moves up the moment a lead exists and BD wants it. | 6 to 8 weeks |
| – | China | TBD | No country list, no lead. Not scheduled. | – |

Serially, with onboarding overlapping the previous shadow, positions 1 to 8 take roughly 18 to 24
months after R0. The spans are estimates from the pilot's own build rate and will be replaced by
measured numbers after R1 and R2.

## What this does not change

CRM integration stays deferred through the rollout; if it is ever taken up it is its own decision,
not part of a region's activation. The checkpoint, the export file and the acquisition-ethics rules
apply identically in every region. Each region's reviewers decide only in their own region
(rule 25).

## Open items

- Countries with no region (Central Africa, Sudan, Israel, Palestine, Timor-Leste's double listing
  and others in `docs/open_decisions.md`) need placing before the region that should own them
  reaches `onboarding`.
- Belarus sits in Central & Southeast Europe. Canada, the EU and the UK sanction Belarus as they do
  Russia, which the draft treats as excluded. It needs the same explicit decision before R1.
- The MENA and North America VP hires set the real order for positions 3 and 8.
