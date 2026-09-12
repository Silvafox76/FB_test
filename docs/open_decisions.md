# Open decisions

Questions raised by a step that the step itself did not settle, each with the step that has to.
Not the decision register in Architecture v0.4 section 10, which holds the architectural decisions
D1 to D33. This file holds the small ones that came out of building, so they are not rediscovered.

Closed items stay, with what was decided and where the decision now lives.

**If you are reading this to decide something rather than to understand the build,
read the first two sections only.** They hold the decisions that are blocked on a
person rather than on engineering, each with what it costs to leave it. Everything
after them is the build's own bookkeeping.

The first section is five sources whose terms **say something restrictive**. The
second is the West African wave, where the finding is the opposite and needs a
different kind of answer: five of those sources have **no terms at all** to read.

## Decisions needing a named person, not a commit

These are the ones to take to management. Each source below is **blocked on a person
reading terms and recording an answer**, not on engineering. The technical work is
either done or deliberately not started, and each row says which.

Five sources sit here. Nothing in this group is a defect and nothing in
it is waiting on the pipeline: the acquisition-ethics rule (CLAUDE.md rule 21) and
the registry's `tos_status` field are doing what they exist for, which is to stop a
robot from taking a decision about someone else's site.

**Read the `tos_status` values this way.** `reviewed_ok` means the review happened
and the answer was yes; eight sources are there and running. `reviewed_restricted`
means the review happened and produced a real answer that was not yes. It does not
mean unreviewed, and it is not a to-do: each file below carries the measurement, the
quoted terms and the options, so the decision is cheap to act on in either
direction.

| Source | What is built | What blocks it | Who can lift it | Cost of leaving it |
| --- | --- | --- | --- | --- |
| **simap** (Switzerland) | Connector, fixture from a real call, contract test. Held disabled and unwired. | AGB «Nutzung des API» clause 5 imposes obligations on how the data is *displayed*. Clause 4 affirmatively **permits** commercial reuse and passing to third parties, so the build is lawful; what is unsigned is acceptance of the display rules. | A named FreeBalance person, reading clause 5 and accepting the display obligations. Cheapest of the five. | Switzerland absent from the queue. Geography weight 0.6, so a low-priority market, but the work is already paid for and one signature turns it on. |
| **PLACE** (Spain) | Nothing, deliberately. No connector, no fixture, and the syndication URL is **not** recorded in the registry so a later session cannot trust an unverified endpoint. | The terms forbid **the fetch itself**, which is the harder kind: for simap the restriction binds what happens after the fetch, here it binds the request. No request has ever been made. | A named person recording an exception, after which the connector, fixture and contract test still have to be built. | Spain absent as a direct source. Mitigated: Spanish notices already reach the queue through TED — two of the twelve current candidates are Spanish buyers. |
| **UNDP** | Nothing. A probe script that fetches `/robots.txt` and nothing else, ever, and no fixture on purpose. | `procurement-notices.undp.org` answers every robot with `Disallow: /` on every path. That is rule 21's first clause and a bright line: no request may be made at all. UNDP advertises a procurement RSS feed on a path its own robots.txt disallows. | UNDP, in writing — that the advertised feed may be read on a schedule by an identified client. Or a named FreeBalance person recording that the feed is in scope, which is **the one place in this pipeline where a CLAUDE.md rule would be read down rather than followed** and should be visible as such. | UNDP country-office procurement absent. Real West African PFM volume. The file's own recommendation is to take the coverage through UNGM instead — which is itself blocked, below. |
| **UNGM** | Nothing, and no fixture **specifically** because committing one would put UNGM notice text in this repository's history permanently, which is the act their clause 7.2 names. A commit is not undone by a later decision going the other way. | Every technical answer is favourable: robots.txt permits the paths, no login, the endpoint answers a plain POST, the filters work, and it is **the best volume of any donor source in the registry**. Then clause 5.2 prohibits commercial use outright and 7.2 names storing their content. | A FreeBalance decision-maker's reading of terms quoted in full in `sources/ungm.yaml`. | The highest-volume donor source in the pilot, unread. **One fact that shapes the decision: UNGM sells the capability this pipeline reproduces** — a paid UNGM Pro "Tender Alert Service" that emails subscribers matching tenders, advertised on the same search page the connector would have read. |
| **MCC** | Registry entry only. No connector, because there is no readable page for one to read. | The only route MCC offers to live partner-country opportunities is `mcc.dgmarket.com`, a **third-party aggregator behind a bot wall**. Rule 21 forbids both the mirror and the wall, and no amount of review changes that. | A product decision, not a terms decision — see the three options below. | Least costly of the five, because MCC-funded tenders are mostly visible anyway: an MCA tender is normally published nationally too, and the scorer already lifts relevance when a notice names MCC as its financier. |

**MCC's three options**, because it is the one whose answer is not a yes-or-no on terms:

  a. Re-scope to the MCA entities, one connector per country. It stops being a donor
     feed and becomes national portal work — steps 17 and 18's shape — and it
     overlaps them heavily: Benin, Côte d'Ivoire, Liberia and Senegal already have a
     national portal connector planned. The gain over those is MCC-funded tenders an
     MCA publishes only on its own site, which is a real gap, at a cost of 5 to 7 new
     hosts each needing its own robots and terms review.
  b. Build the Business Forecast as pipeline *intelligence* rather than notices —
     something that tells a reviewer a tender is coming in Kosovo next quarter. Needs
     a product decision first: nothing in the schema holds a row with no deadline and
     no URL, and `Candidate` is built around a notice a reviewer can open.
  c. Leave MCC unread and rely on the scorer. Cheapest, and what happens if nobody
     decides.

**What this group is not.** None of these five is a case of the pipeline being unable
to read a source. In every one, the request either works or would work. The reason
each is stopped is a judgement about someone else's terms, and rule 1 says that is a
decision a person takes and not a branch the code takes. Treating any of them as an
engineering backlog item would be the wrong reading.

## The West African wave: a different question, and a bigger one

Ten West African sources were onboarded on 2026-09-12 for BUILD_ORDER steps 17 to 19.
This section is separate from the one above because the finding is not the same shape.
Above, five sources have terms that **say something** and a person has to decide whether
to accept it. Here, the recurring finding is that **there are no terms to read**.

**The pattern, and it is worth stating plainly because it will not go away.** On Benin,
Gambia, Mali and Senegal, source-onboarder went looking for conditions d'utilisation,
mentions légales, a privacy policy or a disclaimer, and found none. Not a restrictive
clause, not a permissive one: no page. On Gambia the footer carries "Terms of Use" and
"Privacy Policy" links that are literal `href="#"` placeholders. On Mali and Benin the
guessed slugs all 404 and the compiled application bundle contains no such route. These
are national government procurement portals, and the question of whether a foreign
company may read them on a schedule has simply never been written down.

That is not the same as permission and it is not the same as refusal, so all four sit at
`tos_status: pending` rather than either answer. **Nobody can lift this by reading
harder.** It needs either a FreeBalance decision to proceed on the absence, recorded as
such, or a written approach to each authority. Both are decisions a person takes.

Senegal is the near miss in this group: its robots.txt is an explicit empty `Disallow:`,
which permits everything, and its data route needs no account. Only the missing terms
page keeps it at `pending`.

Three sources went the other way on the same test and are `reviewed_ok`: Burkina Faso,
Liberia and Sierra Leone, each on permissive robots.txt plus no login on the data
actually read plus no restrictive clause anywhere a clause would live. Nigeria is
`reviewed_ok` on a stronger footing than any of them — NOCOPO states an explicit ODC
Public Domain Dedication and Licence.

| Source | Route found | Position | What it needs |
| --- | --- | --- | --- |
| **Burkina Faso** | DGCMEF's Drupal listing of the daily *Quotidien* bulletin, server-rendered, plain httpx | `reviewed_ok`. Connector in build. | Nothing. See the BUILD_ORDER correction below. |
| **Liberia** | PPCC e-GP's **unauthenticated OCDS 1.1 JSON API**, 1,385 records in one 256 KB request | `reviewed_ok`. Connector in build. | Nothing. Best technical route of the ten. |
| **Sierra Leone** | NPPA's own WordPress page, one server-rendered table, 32 rows | `reviewed_ok`. Connector in build. | Nothing, but see the volume note below. |
| **Nigeria** | NOCOPO's unauthenticated OCDS 1.1 JSON API under an explicit public-domain licence | `reviewed_ok` on terms, and **blocked on content**. See below. | A product decision, not a terms decision. |
| **Mali** | `marchespublics.ml` is an Angular SPA; its own backing API answers an unauthenticated GET with real notice JSON | `pending` — no terms page exists | The absence decision above. |
| **Benin** | `marches-publics.bj`, Angular, data behind `api.marches-publics.bj` after hydration | `pending` — no terms page exists | The absence decision, **and** a browser network path. |
| **Gambia** | `gppa.gm/tenders/notices`, rows injected by JavaScript | `pending` — terms links are `href="#"` placeholders | The absence decision, **and** a browser network path. |
| **Senegal** | APPEL at `achatspublics.sn` is a Nuxt SPA, but its own shipped JavaScript names `api.achatspublics.sn/anon/tdo`, which answers a plain unauthenticated GET with full tender JSON | `pending` — no terms page exists | The absence decision only. No browser needed. |

**Nigeria is the one worth a conversation, because every technical answer is yes and the
answer is still no.** NOCOPO is the best-engineered source in the entire pilot: no login,
no CAPTCHA, no PDF, no OCR, a documented OCDS 1.1 JSON API, and a licence that
explicitly permits sharing, creating and adapting. And it is **content-unfit for this
pipeline**. Four separate records sampled at "Tender Stage" all carried
`tender.status: "complete"` with no `tenderPeriod`, and one record's award date precedes
its own bid-opening milestone by five months. That is consistent with whole
planning-to-award histories being entered in one batch after the fact, which is what
bpp.gov.ng's own circulars say NOCOPO is for: MDA disclosure of procurement records, not
a live call-for-bids board. Nigeria is the largest economy in the priority geography and
the pipeline cannot use its federal portal to find live tenders. The options are to drop
it, to re-scope it as a *scoring signal* (buyer and MDA procurement history, which is a
different feature nothing in the schema currently holds), or to find Nigeria's live
tenders somewhere else — most likely the state portals, which are step 18's other half.

**The volume finding nobody should read past.** Sierra Leone's national portal published
**0 notices in the last 7 days, 2 in the last 30 and 28 in the last year**, with one
176-day gap. Liberia's published nothing on 10 of 32 days. These are honest measurements
of real national portals and they set an expectation for what the West African wave will
actually yield: it is a thin, bursty feed, not a European-volume one. The pipeline's
value in this geography is that it does not miss the two notices, not that it processes
many. Worth saying before week 14 measures throughput and reads low numbers as a defect.

## Open

| # | Raised at | Question | Decides at |
| --- | --- | --- | --- |
| 7 | Step 4 | **A stated value in a currency other than USD is dropped.** TED states an estimated value with its own currency and 21 of the 50 recorded notices carry one, all in EUR. `notices.estimated_value_usd` is USD by name, converting needs an exchange rate and a date, and nothing in this pilot has either, so the mapper stores nothing rather than an invented conversion that would reach the reviewer looking researched. The cost is real: appendix E's Total Opportunity Amount is one of the few figures BD gets for free, and it is currently empty for every EUR notice. Options: store the original value and currency as published and let the record builder present it unconverted; add a rates source; or leave it and accept the gap. | Step 10, when the record builder needs Total Opportunity Amount |
| 8 | Step 4 | RESOLVED IN PRACTICE, kept for the record until step 14's acceptance is formally re-run. **TED needs the translation stage after all, for bodies.** Recording the fixture showed TED translates the notice *title* into all 24 EU languages (present on 50 of 50) but not the *description*, which carries one language on 48 of 50 and English on only 3. So the English title arrives free and is stored in `translations` stamped `ted-eforms`/`source-native`, and the English body does not exist. Step 6's scorer sends the first 3,000 tokens of the body, which for TED will be Polish, German or Spanish unless step 14 translates it. Not a defect, but it changes what step 14 is for: it is not only the no-lexicon languages, it is every TED body. | Step 14, when the translation stage is built |
| 15 | Step 11 | **`weekend-slice` is tagged locally and cannot be pushed.** This session's GitHub credential returns HTTP 403 on any tag ref; a lightweight tag fails identically, so it is not the annotation, and the proxy's relay log shows no GitHub entry, so it is not egress. The GitHub API tools available here can create branches, files and pull requests but have no tag or release endpoint. The container is ephemeral, so the local tag is lost with it. The tag belongs on `6421a89`. One command from a person with push-tag rights: `git tag -a weekend-slice 6421a89 -m "BUILD_ORDER steps 1-11" && git push origin weekend-slice`. | Whoever holds tag-push rights on the repository |
| 16 | Step 6, first real run | **The scorer returns no `estimated_value_usd` on any notice, and that is mostly honest but not entirely.** 0 of 221 scored notices carry a value. For most that is correct: the notice states none. But decision 7 below means a EUR value is dropped at the normaliser before the model ever sees it, and TED states one on 21 of 50. So two causes are being reported as one, and appendix E's Total Opportunity Amount is empty for both. The Sierra Leone candidate is the counter-example worth noting: it carries USD 4,000,000, because the World Bank pipeline states its financing in USD. Resolving decision 7 would separate the two. | Step 16, with decision 7 |
| 17 | Step 15 | **UNDP's recommended fallback is itself blocked.** `sources/undp.yaml` recommends taking UNDP coverage through UNGM rather than seeking UNDP's permission. `sources/ungm.yaml` then blocks UNGM on its own terms. So the cheapest route for each is the other one, and neither is open. Someone should notice that the two files' recommendations do not compose before either is acted on; the honest reading is that UN-system procurement coverage needs one decision, not two. | A named person, taking both files together |
| 18 | Step 15 | **EBRD's fixture cannot be recorded: the host stopped answering after serving the first pass.** `ecepp.ebrd.com` served the recording pass on 2026-09-12 and then began resetting connections mid-exchange (four `ws_closed_mid_exchange` entries in the egress relay log; a plain GET still returns `[Errno 104] Connection reset by peer`). Most likely rate limiting after that pass. Tried twice and then left alone, because rule 21 allows one polite pass and hammering a refusing host is the opposite. The connector, normaliser, recorder, registry entry and all 56 contract cases are written and committed; the source is `enabled: false` and absent from `CONNECTORS`, and the test file skips at module level with the host's error in the reason, so the gap is reported in every run summary rather than being silent. An earlier `ebrd.json` was deleted rather than kept: it held real data in the recorder's previous shape, with detail pages already parsed into dicts instead of stored as the HTML they arrived as, so 45 of the 56 cases could not run against it and the detail parser could not be tested at all. A fixture that cannot exercise the parser it exists for is worse than none, because it looks like coverage. Re-run `scripts/record_ebrd_fixture.py` when the host answers. | Whoever next runs the recorder against a responsive host |
| 19 | Step 18 | **Step 18's acceptance test has no fixture, because the scanned bulletin may not exist.** Its acceptance is "the Burkina bulletin fixture (a scanned week) OCRs to text with the expected item count in range". source-onboarder downloaded ten issues spanning 2021-04 to 2026-09 and ran `pdftotext`/`pdffonts` on every one: **all ten carry genuine embedded subsetted TrueType fonts and extract 124,471 to 631,173 characters of real prose.** None is a scan. The bulletin is also **daily on business days**, not weekly as the step says. So the premise that some issues are scanned images is unsupported by a 10-issue sample, and the acceptance test cannot be run as written. `monitor/normalise/ocr.py` is built and its text-layer route is tested against real PDFs, so no work is lost either way. Options: sample further (roughly 1,390 issues are unsampled, and older ones are likelier to be scans); correct the step's premise and its acceptance test; or keep the OCR route for a different source that needs it — Nigeria's Federal Tenders Journal PDFs *were* found to be scanned images with no text layer. | A person correcting BUILD_ORDER step 18, or deciding to keep sampling |
| 20 | Step 17 | **Two browser sources cannot have their selector captured from this environment.** Benin and Gambia both need a `BrowserConnector`, and each needs a `row_selector` naming a notice row in the rendered page. Senegal was the third until its own JavaScript gave up an unauthenticated JSON endpoint, which is the lesson: read the bundle before reaching for a browser. Capturing one means rendering the page, and real browser TLS sessions to these hosts fail through this session's proxy (`ws_closed_mid_exchange` after about six seconds, reproduced against several West African government hosts with both Chromium and Firefox). That is a documented unsupported proxy pattern, so it reads as an environment limit rather than a fact about the portals. `monitor/connectors/browser_base.py` is built and its guard is tested; what is missing is one string per source. Benin's entry records `row_selector: benin-row-selector-not-yet-captured`, a deliberate non-match so the connector fails loudly rather than returning zero, and Gambia's is an evidence-based guess taken from its loading-skeleton markup and flagged as unverified. | Whoever has a network path that can drive a browser to these hosts |
| 21 | Step 18 | **An OCDS `contactPoint` carries personal data, and rule 19 makes that blocking if it reaches a model call.** Nigeria's sampled releases carried a supplier's personal phone number and email address in `contactPoint`. Liberia's API is the same standard and may carry the same. Rule 19 permits public notice text and metadata to reach a model and nothing else; a named individual's contact details are neither. The connector stores what was published (rule 9), so the strip belongs in the normaliser, before the filter and well before the scorer — and it is not only a model-call concern, since those values would otherwise sit in `notices.body` and render on the candidate page. Nothing is wrong today: neither source is enabled and neither has ever been fetched. It has to be built before either is. | Whoever builds the normaliser for the OCDS sources, before Nigeria or Liberia is enabled |
| 22 | Step 18 | **Two BUILD_ORDER connector classes are contradicted by evidence, and this is the cheap kind of correction.** Step 18 calls Liberia "a simpler Page connector"; it publishes an unauthenticated OCDS JSON API and is a `FeedConnector`. Step 17 assumes Benin and Senegal are `PageConnector`s; Benin's listing is Angular-rendered and needs a `BrowserConnector`, while Senegal needs neither — its SPA is backed by a plain JSON API, so it is a `FeedConnector`, simpler than the plan. All three registry entries record the evidence. None of the three costs anything to correct and none changes the shape of the wave — noted so that a later reader does not treat the registry and BUILD_ORDER disagreeing as a defect in the registry. | A person correcting BUILD_ORDER steps 17 and 18 |
| 23 | Step 19 | **The French lexicon's apostrophe fix is correct and currently worth nothing, which is the useful part.** Real French notices use both apostrophes: 400 of 584 BOAMP notices carry ASCII `'`, 119 carry typographic `’`, and 83 notices across the corpus carry both inside one document. A lexicon phrase can only be written one way, so it misses the other half whichever way it is written, and writing it twice would be the second selector rule 1 forbids. `monitor/filter/lexicon.py` now folds both sides to one canonical form. **Measured delta on all 707 French notices held today: nought**, because neither of the lexicon's two elided phrases matches anything in either form. So step 19's acceptance ("the French lexicon's miss rate is re-measured and improved from its week-1 baseline") is **not met by this change** and should not be recorded as met. It also cannot be met yet on its own terms: it asks for the miss rate on real *West African* French notices, and no West African source has been fetched. | Step 19, once a West African French source is enabled and a week of its notices exists |
| 2 | Step 3 | **The Zoho picklist values for Industry outside West Africa.** `config/record_defaults.yaml` carries `North & West Africa` from Architecture v0.4. The Europe and Balkans values are CRM picklist strings that appear in no document and in no reference record. **Decided: leave them as `TBD` and confirm at the dry-run import**, rather than guessing now. A wrong picklist value is a rejected column at import; a placeholder is one mapping decision the import operator makes once, in front of the actual picklist. Nothing exports before live mode, so no real record carries the placeholder in the meantime. This is the one derived field in appendix E that ships as a placeholder, and it is deliberate. | Step 16, at the dry-run import with the named import operator |

## Logged deviations

Files a step touched that its own list did not name, recorded so a reviewer does
not have to reconstruct the reason from a comment. None of these is a decision
anyone still has to take; they are here because CLAUDE.md asks that going outside
a step's files be said out loud.

| Step | File | Why |
| --- | --- | --- |
| 8 | `migrations/007_content_hash_is_per_source.sql`, `monitor/fetch.py` | `content_hash` was globally unique, so the same tender from two sources was stored once and the second copy dropped at ingest. That makes step 8's first dedupe rule unable to fire, its own acceptance ("the same notice text from two sources joins") impossible, step 11's World Bank join impossible, and appendix E's Partners Involved permanently empty. The hash is now scoped to its source. |
| 8 | `config/thresholds.yaml` (`regions`) | `candidates.region` had no source. The names match `record_defaults.yaml`'s `industry_by_region` keys, and a test asserts every country with a geography weight has a region. |
| 4 | `monitor/fetch.py` | Change detection has to write to the database and neither the connectors nor the normalisers may. New module rather than fattening `cli.py`. |
| 4 | `monitor/normalise/codes.py` | ISO 3166 and ISO 639 conversion, needed by every source, not only TED. |
| 5 | `migrations/005_notice_filtered_in.sql`, `monitor/models.py` | The four-value notice status vocabulary had no name for "passed the filter, waiting to be scored", which step 6 has to query for. |
| 5 | `monitor/health/status.py`, `monitor/cli.py` | Step 5 requires per-source filter statistics printed by `monitor status`, and its acceptance test runs `monitor filter` and `monitor status`. Neither command existed. Reporting lives in `health/` rather than `filter/` so filtering does not also own rendering. |
| 5 | `config/lexicon_fr.yaml` | A step 3 seed phrase, the bare word `formation`, failed one of step 5's own required hand-written cases by matching a communication-skills training tender. Fixed here, ahead of step 19's tuning pass, with the case written first. |
| 5 | `.pre-commit-config.yaml` | The pinned ruff was two major versions behind the one the project installs; they formatted differently, so every commit bounced. |
| 5 | `RUNBOOK.md` | Step 5's acceptance test says to record the drop rate there. Everything else the runbook needs is step 11's and is deliberately absent. |

## Closed

| # | Raised at | Question | Decided |
| --- | --- | --- | --- |
| 10 | Step 14 | **No model credential, so no model call had ever been made.** Steps 6 and 14 were built and unit-tested through a mocked transport, with no real response ever seen. | **Resolved: a key was provided and credited.** The pipeline then ran end to end: 662 notices translated, 221 scored, 143 candidates, 12 staged, USD 2.19 for the day against a USD 25 cap. The first real calls found four defects no test could have caught, all now fixed and each recorded in its own commit: the tool schema was refused outright (`minimum`/`maximum` unsupported under `strict`, while the module carried a comment claiming they were stripped); the cost line went negative on 28 of the first 29 calls (the API's four token counts are disjoint and the code subtracted cache reads from fresh input, which made the USD cap more permissive than configured); **five of appendix C's ten fields could not be returned at all** (they have defaults, so pydantic left them out of `required`, and `strict` will not carry a property that is not required — every notice came back with matched functions, system names, flags, value and deadline empty, indistinguishable from a model declining to match anything); and the World Bank's OR join silently drops an apostrophe-bearing country name unless it comes first, so Côte d'Ivoire yielded nothing on every run. The pattern in all four: the shape of a missing answer and the shape of a negative answer were identical, and nothing in the pipeline could tell them apart. |
| 14 | Step 11 | **Rule 22's cap disagreed with the config, the runbook, the environment and the systemd unit.** Five statements of one number, no two agreeing on which governed. `.env` silently enforced 600 over a configured 2,000 for long enough to kill a translation run 545 calls in. | **2,000, and rule 22 amended to say so, on the user's explicit decision.** CLAUDE.md now names `config/thresholds.yaml` as authoritative and records that `DAILY_CALL_CAP`/`DAILY_USD_CAP` exist for drill 3 only and never as a permanent setting. The systemd unit and drills README are corrected. The shape of the mistake is worth keeping: a configuration value with two sources of truth and a silent precedence order between them. Nothing failed and nothing warned; it surfaced only when a run grew big enough to hit the lower number. |
| 9 | Step 5 | **The TED connector saw 3.5% of what its own query matched.** One page of 50 out of 1,449, in an order the API does not document. | **Paged.** `TedConnector` now reads to the end of the result set: six requests at `limit` 250, verified as the API's maximum (300 is rejected). It stops on a short page, on the registry's `expected_max` as a ceiling, on an absolute 40-page bound, and raises if the API reports `timedOut`, because an incomplete read that looks complete is the failure this discipline is against. `sources/ted.yaml` now says `[50, 2500]`, measured rather than guessed. The result: 1,449 fetched where 50 were before, and 33 candidates where there were none. The first full run also found two things the 50-notice fixture never contained, `grp-p-aut` and Zambia, both caught loudly by the raise-on-unknown rules rather than recorded wrongly. |
| 11 | Paging fix | **One day of TED needed 1,132 translate calls against a 600-call daily cap.** A single source was 532 calls over, and the backlog grew every day. | **Cap raised to 2,000, cost cap untouched.** The USD cap is the one that protects the budget and 2,000 Haiku calls of this shape is about USD 8, inside the USD 25 cap, so the binding cap is now the one denominated in the thing anyone cares about. A test asserts that relationship holds, so raising the call cap again without checking fails the suite. Decision 13 cut the same backlog by 43% in the same change: 630 held rather than 1,132, which is 1,370 calls under the cap rather than 532 over. |
| 13 | Paging fix | **42% of what the TED query returned was already decided.** 541 award, 51 modification, 17 social award and 18 VEAT notices for finished tenders. | **Excluded at the query.** `sources/ted.yaml` carries `exclude_notice_types`, a new registry field, so it is a config edit and reversible: an award names the winner and Architecture v0.4 counts incumbency as changing a score, so if BD wants that signal the list is where it comes from. Measured: a two-day window went from 1,449 matched to 822. Excluded at the query rather than after the fetch, so they are never read rather than read and thrown away. |
| 6 | Step 4 | **The zero-yield health rule and the hourly scheduler contradict each other.** A run with zero *new* items incremented `zero_yield_runs`, but TED's query looks back two days, so every run after the first legitimately takes none. | **Counted on `items_seen` instead**, the recommendation as written. Step 9's acceptance is what forced it: it asks for three healthy sources and got two, because TED had re-read its own window three times and reached `unhealthy` while working perfectly. A parser that has broken returns nothing at all; a working one re-reading its window returns rows already stored. `items_seen` tells those apart and `items_new` cannot. |
| 5 | Step 4 | **Egress, and the TED fixture it blocked.** `api.ted.europa.eu` was denied by the environment's network policy, so no fixture could be recorded and no parser written. | **Allowlisted.** The four wave-1 source hosts were added to the environment's network policy. `scripts/check_egress.py` reports what is reachable and exits non-zero while any is not. All three enabled sources have since been fetched live. |
| 1 | Step 3 | **Which config is version-hashed in the database.** Rule 6 names sources, lexicons, thresholds, geography weights, the function map, the product mapping and the record defaults; only the lexicons were hashed, because `001_schema.sql` defined only `lexicon_versions`. | **Generalise to `config_versions`.** `migrations/004_config_versions.sql` creates one table with a row per config file per content hash, carries the lexicon rows across and drops `lexicon_versions`, so there is one table and one way to ask the question (rule 1). `monitor/registry/load.py` hashes every file under `sources/` and `config/` on each seed; a file added to `config/` without a kind in `CONFIG_KINDS` raises rather than quietly stopping being traceable. BUILD_ORDER's step 2 schema and step 3 acceptance line are corrected to match. Nine files are hashed today. |
| 3 | Step 3 | **Whether the model call accepts the generated tool schema as it stands.** `Score.model_json_schema()` emits `$defs` and a `$ref` for the nested `matched_functions` object. | **Keep the model; no change.** The API's JSON Schema support explicitly includes `$ref` and `$defs`, so that was never the risk. What is **not** supported is numeric and string constraints — `minimum`, `maximum`, `minLength` — which `Score` uses for `relevance` (0 to 100) and the non-empty string fields. The SDK strips those from what it sends and validates them client-side, which is exactly the contract step 6 already builds: validate the tool input with `Score`, one retry with the validation message appended, then park. The constraint still binds; it binds on the way back in rather than at the model. Step 6 should expect the model to be capable of returning `relevance: 101` and the validator to catch it. |
| 4 | Step 3 | **BUILD_ORDER's wording on `Score`.** It said the models match the step 2 schema "field for field" and that `Score` is the model-call tool schema, which only conflict if the whole `scores` table is read as the tool schema. | **Corrected in BUILD_ORDER step 3.** It now states the exception explicitly: `Score` is appendix C's ten output fields, and the call metadata is written by `monitor/score/run.py` alongside the validated `Score`. |
