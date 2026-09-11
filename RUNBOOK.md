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

## What is not here yet

Start and stop, adding a source, reading health, changing a keyword, rolling back
a prompt, where the logs are and where the cost line is: BUILD_ORDER step 11 owns
all of that and writes it then. This file exists now only because step 5's
acceptance test says to record the drop rate in it. Until step 11, `README.md` has
the commands and `docs/open_decisions.md` has what is unsettled.
