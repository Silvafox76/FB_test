# Runbook

How to operate the Monitor. Step 11 fills this in properly; what is here now is
what the steps completed so far have produced, including the measurements each
step's acceptance test asked to be recorded.

## Measurements

Recorded as each step's acceptance test produced them. Never edited afterwards:
a number that moves gets a new row with its date, so the trend is visible.

### Free filter drop rate

| Date | Source | Matched | Fetched | Considered | Passed | Dropped | Drop rate | Held for translation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-11 | ted | 1,449 | 50 | 9 | 0 | 9 | 100% | 41 |
| 2026-09-11 | ted (paged) | 1,449 | 1,449 | 270 | 33 | 237 | 88% | 1,132 |
| 2026-09-12 | fts | 25 | 25 | 25 | 2 | 23 | 92% | 0 |
| 2026-09-12 | prozorro | ~2,000/day | 400 | 380 | 0 | 380 | 100% | 10 |
| 2026-09-12 | ted (awards excluded) | 822 | 822 | 176 | 21 | 155 | 88% | 630 |

The last TED row is after two decisions taken on 2026-09-12: award and post-award
notice types are excluded at the query, and the daily call cap moved from 600 to
2,000. Together they turned an unreachable backlog into a reachable one.

| | before | after |
| --- | --- | --- |
| TED notices matched per two-day window | 1,449 | 822 |
| held for translation | 1,132 | 630 |
| daily call cap | 600 | 2,000 |
| over or under the cap | 532 over | 1,370 under |

The stored TED notices were cleared and re-fetched under the new query, because the
old ones included about 600 award notices that would each have cost a translate
call to discover we did not want them. Nothing downstream had consumed them: no
scores, no candidates, no model translations.

A side effect worth knowing: 198 of 250 recorded notices now carry a deadline,
against 16 of 50 before. Contract notices have deadlines and award notices do not,
so excluding awards directly improves clustering, because the deduper needs two
known deadlines to join on a title.

Three sources, 96% dropped overall for the price of zero model calls.

**Prozorro is what the CPV stage is for.** 380 of its 390 notices were dropped on
their classification alone: tyres, fuel, food, vehicle parts, the ordinary goods
procurement of a country at war. None of it reached a model and none of it cost
anything. It is also why the Prozorro connector filters the change feed on status
before fetching detail: about 2,000 tenders are modified a day, only 9% are open to
a bidder, and a detail record is 110 KB.

**Prozorro's run hit its ceiling.** 400 details is `expected_max` in
`sources/prozorro.yaml`, so this is a sample of the day rather than the day. Unlike
TED, the ceiling is a real limit rather than a safety net, because the only way to
see a tender's subject is to fetch it in full. Raising it raises the request count
one for one.

The second row is the same query after paging was fixed. Read both carefully,
because the headline number is the least useful part of either.

**Paging changed the answer from nothing to something.** Reading one page of 50
produced zero candidates. Reading all 1,449 produced 33. Those 33 are the first
real material the scorer will ever see, and the first run would have reported a
clean day and found none of them.

On the 50-notice first row:

**41 of 50 never reached the lexicon at all.** They are in German, Polish, Finnish,
Croatian, Spanish, Dutch and Italian, and there are only two lexicons, English and
French. Those notices are held at `needs translation` with status `detected`, not
dropped, and step 14 is what releases them. Until then the free filter can only
decide about the English and French share of TED, which on this day was 9 notices
out of 50.

**Of the 9 it could decide about, it dropped all 9, and all 9 were right.** Data
centre co-location, office furniture, advertising and marketing, security
guarding, event organisation, corporate hospitality. No false negative in the
sample: nothing dropped looks like a PFM opportunity.

**Zero passed, and zero would have passed even with TED's free English titles.**
TED supplies an English title for 47 of the 50 (see `docs/open_decisions.md` item
8), so the obvious idea is to match the English lexicon against those and rescue
the 41. Measured: it rescues none. This day's 50 TED notices contain no public
financial management opportunity at all. That is a plausible result rather than a
broken lexicon, and it is why the drop rate is not yet evidence about precision.

**The sample was 3.5% of what matched.** Fixed: the connector now pages to the end
of the result set, six requests at the API's maximum page size of 250.

## What the full read showed

**1,132 notices are held for translation against a 600-call daily cap.** One
translate call each, so a single day's TED exceeds the cap by 532 calls. The cap
stops the run with an event, which is correct behaviour and not a bug, but it
means TED alone consumes the whole day's model budget and the backlog grows. Cost
is not the constraint: 1,132 calls is about USD 4.75 against a USD 25 cap. The
call count is. See `docs/open_decisions.md` item 11.

**The free filter's precision is poor, and now there is evidence.** The phrases
doing the passing are dominated by generic terms: `establishment` (6 notices),
`recruitment` (4), `recrutement` (3), `gouvernance` (2), `compliance` (2),
`conformité` (2). What passes includes 360-degree feedback consultancy, job
grading methodology, temporary staffing and graphic design. Genuinely PFM
notices are in there too - a payroll tool, an audit mandate, grant management -
but they are the minority. This is the free filter doing its job, which is to cut
cost rather than to be precise, and the scorer is what judges relevance. It is
also exactly the evidence step 19's tuning pass needs, recorded now while it is
cheap to gather. See `docs/open_decisions.md` item 12.

**42% of what we page is already decided.** Of the 1,449, `can-standard` 541,
`can-modif` 51, `can-social` 17 and `veat` 18 are award and post-award notices for
tenders that are finished. `cn-standard` 741 and the `pin-*` 44 are the
opportunities. Narrowing the query would nearly halve the volume and the
translation backlog with it, but whether an award notice is worthless is a BD
question - it names the winner, which is competitor intelligence. See
`docs/open_decisions.md` item 13.

### Scoring

| Date | Notices scored | Schema validity | Cost | prompt_version |
| --- | --- | --- | --- | --- |
| not yet run | - | - | - | `5cb6a4eb2de9` |

Step 6's acceptance asks for a real scoring run above 95% schema validity. It has
not been run: there is no `ANTHROPIC_API_KEY` in the environment the pipeline was
built in, so no model call has been made at any point. See
`docs/open_decisions.md` item 10.

Everything else is ready for it. 33 notices sit at `filtered_in`, the system
prompt is about 3,600 tokens at version `5cb6a4eb2de9`, and the run is costed at
USD 0.006 for the first call and USD 0.003 for each one after it, since the prompt
block is marked for caching and is identical for every notice. The whole run is
about **USD 0.09 and 33 of the 600 daily calls**. Cost is not what is stopping
this.

To run it: put the key in `.env`, then `make score`. Record the four numbers
above, and keep the `prompt_version`, because a score is only comparable to
another score made under the same prompt.

### Golden set

| Date | Labelled | Scored | Precision at 60 | Recall | Schema validity | Mean cost | prompt_version |
| --- | --- | --- | --- | --- | --- | --- | --- |
| not yet run | 0 of 30 | - | - | - | - | - | `5cb6a4eb2de9` |

`tests/golden/golden.csv` holds 30 real TED notices with an empty label column.
Two things have to happen before there is a number, and they are independent:

1. **A person labels the set.** `relevant` or `not`, one per row, by hand. The
   pipeline will not do it and `make golden` refuses to run until it is done. A
   golden set labelled by the same family of model it measures scores well against
   its own opinion, and a real regression reads as agreement.
2. **A model credential exists**, as for steps 6 and 14.

The 30 are what the free filter passed, which is the population the scorer is
actually given. Reading them is a fair warning about what the filter currently
lets through: alongside asset management and payroll systems there is temporary
staffing, 360-degree feedback consultancy and graphic design. Expect a low first
precision, and expect that to be the honest starting point rather than a problem
with the harness. `docs/open_decisions.md` item 12 has the evidence.

To run it: `make golden-export` writes the file, a person labels it, `make golden`
reports and appends a line to `tests/golden/history.csv`. The line carries the
`prompt_version` and the model with every number, because a score is only
comparable to another made under the same prompt.

### Candidates

None yet. `make stage` has nothing to do until notices reach `scored`, which is the
scorer's output and therefore waits on the model credential. The dedupe and staging
logic is tested against real database rows (`tests/unit/test_stager.py`), including
the cap: the sixteenth candidate from one source on one day stays at `scored` and
keeps its score.

`make run` chains the whole pass and currently stops at the scorer, after fetching
1,449 notices and filtering them, which is the intended behaviour rather than a
failure: each stage commits its own work, so a pass that stops keeps everything
before the stopping point and the next pass resumes there.

## What is not here yet

Start and stop, adding a source, reading health, changing a keyword, rolling back
a prompt, where the logs are and where the cost line is: BUILD_ORDER step 11 owns
all of that and writes it then. This file exists now only because step 5's
acceptance test says to record the drop rate in it. Until step 11, `README.md` has
the commands and `docs/open_decisions.md` has what is unsettled.
