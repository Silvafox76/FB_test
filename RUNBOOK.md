# Runbook

How to operate the Monitor. Step 11 fills this in properly; what is here now is
what the steps completed so far have produced, including the measurements each
step's acceptance test asked to be recorded.

## Measurements

Recorded as each step's acceptance test produced them. Never edited afterwards:
a number that moves gets a new row with its date, so the trend is visible.

### Free filter drop rate

| Date | Source | Fetched | Considered | Passed | Dropped | Drop rate | Held for translation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-09-11 | ted | 50 | 9 | 0 | 9 | 100% | 41 |

Read that carefully, because the headline number is the least useful part of it.

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

**The sample is 3.5% of what matched.** The query matched `totalNoticeCount` 1,449
notices; the connector fetches one page of 50 and does not page. See
`docs/open_decisions.md` item 9. No recall claim can be made from this number, and
none should be until paging exists.

## What is not here yet

Start and stop, adding a source, reading health, changing a keyword, rolling back
a prompt, where the logs are and where the cost line is: BUILD_ORDER step 11 owns
all of that and writes it then. This file exists now only because step 5's
acceptance test says to record the drop rate in it. Until step 11, `README.md` has
the commands and `docs/open_decisions.md` has what is unsettled.
