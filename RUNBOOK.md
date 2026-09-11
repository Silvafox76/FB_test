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

## Commands

```
make up            # start Postgres, apply migrations, seed the registry
make test          # ruff check and the test suite
make fetch S=ted   # run one connector once
make filter        # run the free filter over everything not yet filtered
make status        # source health and the filter's arithmetic per source
```

## Reading `make status`

One row per registry source. `drop rate` is `dropped / (passed + dropped)`, so it
deliberately excludes the notices held for translation: they have not been decided
about and counting them as kept would flatter the number. Watch `needs tr.`
alongside it; a large value there means the filter is deciding about a small slice
of what arrived.

`state` is from `source_health`: `healthy`, `watch` at two consecutive failures or
zero-yield runs, `unhealthy` at the source's own `max_consecutive_failures`. See
`docs/open_decisions.md` item 6 for why that counter and an hourly schedule
currently contradict each other.

## Adding a source

1. `source-onboarder` clears the terms of service and writes `sources/<id>.yaml`
   with `enabled: false`.
2. Add its host to the environment's network allowlist, or the fixture cannot be
   recorded. `uv run python scripts/check_egress.py` reports what is reachable.
3. Record a fixture from one real call into `tests/contract/fixtures/<id>.json`.
   Never write the parser first.
4. Write the connector and its contract test against the recorded fields.
5. Wire it into `CONNECTORS` in `monitor/fetch.py` and flip `enabled: true`. A test
   enforces that the registry's enabled set and that table agree.

## Changing a keyword

Edit `config/lexicon_en.yaml` or `config/lexicon_fr.yaml`, then `make seed`. The
new content hash is recorded in `config_versions`, so a run can be traced back to
the phrases that were in force. Add the case to
`tests/unit/fixtures/filter_cases.yaml` **before** changing the lexicon, so the
miss is provably fixed rather than patched.
