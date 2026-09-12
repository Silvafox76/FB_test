"""World Bank project pipeline, search.worldbank.org API v3.

A different endpoint from `monitor/connectors/worldbank.py` and deliberately not
merged with it (BUILD_ORDER step 15). That one reads `api/v2/procnotices`, which
publishes tenders. This one reads `api/v3/projects` filtered to `status` Pipeline:
operations the Bank has disclosed but not yet taken to its Board. A pipeline entry
has no tender, no bid and no closing date. It is the early signal that procurement
is coming, eight to eighteen months out.

Recorded from a real call on 2026-09-12. The fixture is
`tests/contract/fixtures/worldbank_pipeline.json` and this parser is written
against the fields that are in it.

**The measured hazard, and it is a nastier relative of the notices API's.** This
endpoint also ignores query parameters it does not recognise, in silence, but here
the ignored parameter has a name that looks exactly right. Probed 2026-09-12:

    countrycode=GH             -> 28,113 rows (the whole corpus)
    countrycode_exact=GH       ->    340 rows
    status=Pipeline            -> 28,113 rows (the whole corpus)
    status_exact=Pipeline      ->    317 rows
    banana=split               -> 28,113 rows

`countrycode` and `status` are real fields *in the response* - every project row
carries both - so a connector built on them reads as though it were written
against the data. Only the `_exact` twins filter. The combination is what makes
this worse than step 11's hazard: `countrycode_exact=GH&status=Pipeline` returns
340 rows, all of them Ghanaian, three quarters of them Closed projects from the
1990s. A page that is correctly scoped on one axis and silently unscoped on the
other does not look like a bug. It looks like Ghana. So every row that comes back
is checked against both filters here, and a row outside either raises (rule 4).

An `_exact` value that is wrong fails the other way, loudly: `status_exact` is
case sensitive (`pipeline` and `PIPELINE` both return 0) and an unknown value
returns 0 rather than the corpus. Zero yield on a source that normally yields is
already a failure state, so that direction needs no new defence.

Four more measured facts that shaped the request:

  1. **`fl=*` is required.** Without it the response carries 15 fields and none of
     `status`, `countrycode`, `pdo` or `project_abstract` - that is, neither
     prose to score nor the two values the filter check reads. The filter still
     works without it, so the omission would show up as a mapper failure rather
     than as a fetch failure. An `fl` naming a subset is not honoured: asking for
     `id,project_name,pdo,status,countrycode` returns `project_abstract` too.
  2. `rows` caps at 1,000 (`rows=2000` returns 1,000) and `^` is the OR separator,
     as on the notices API. `,` and `|` return nothing.
  3. **The whole in-scope corpus is 42 rows**, so this connector makes one request
     and reads all of it. There is no window and no paging, which is a decision
     taken from a measurement rather than a simplification: see WINDOWLESS below.
  4. `api/v2/projects` is a 404; v3 is the live path. `apilang=fr` and `apilang=es`
     are ignored and return the English edition, and there is no language field on
     a project row - see the normaliser for what that settles.

WINDOWLESS. The notices connector cuts a sorted page at a two-day lookback because
it is walking 418,561 rows for the 13 that are new. Here the covered countries hold
42 pipeline projects in total and they were disclosed at about four a month
(30 of the 42 carry a 2026 `public_disclosure_date`, spread across eight months).
A two-day window would return nothing on roughly six days in seven, and
`monitor/health/source_health.py` counts zero `items_seen` on a source whose
`expected_min` is above zero as a failure state, not an empty success - this source
would sit at `unhealthy` permanently while working perfectly. Reading the whole set
costs one request and 265 KB, `items_seen` is 42 every run, and the content hash
makes `items_new` zero after the first, which is exactly how TED behaves on a
second run within its window. Because the whole set is read, nothing here depends
on the server's ordering, so no `srt` is sent and none is asserted; `srt` is
silently ignored for an unknown field name here too, and this connector is simply
not exposed to that.
"""

from __future__ import annotations

import json

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The API's own maximum. Asked for in full because the whole in-scope set is 42
# rows: 1,000 is 24 times the measured corpus, and if the set ever outgrows one
# page `parse_projects` raises rather than reading a silent fraction of it.
ROWS = 1000

# The one status that means "disclosed, not yet approved by the Board": no tender,
# no bid, no closing date. Counts over the whole corpus on 2026-09-12: Pipeline
# 317, Active 2,590, Closed 18,304, Dropped 6,902, which sum to the 28,113 total.
#
# This is the API's vocabulary, not configuration. Rule 6 puts keywords,
# thresholds, weights and placeholders in YAML because a person tunes them, and
# nobody tunes the string "Pipeline" - it is what the endpoint answers to, case
# sensitively, and the day it changes this connector must fail rather than be
# re-tuned. It is also not in sources/worldbank_pipeline.yaml: `Source` forbids
# unknown fields, so a `status` key there would have to be added to the model every
# source is validated against, which puts one source's vocabulary in all of their
# contracts. Same reasoning as NOTICE_TYPES in monitor/connectors/worldbank.py.
#
# Narrowing further was considered and rejected. `last_stage_reached_name` splits
# the 42 into Concept Review 20, OIS Sign-off 8, Begin Appraisal 5, Begin
# Negotiation 4, Decision Meeting 4, Technical Design 1. Keeping only the literal
# "Begin Appraisal" would drop 37 of 42, including both ends of the range that
# matters: Concept Review is the earliest signal and Begin Negotiation is the
# closest to procurement. The stage is carried into the notice body instead, where
# the reviewer and the scorer can both see it.
PIPELINE_STATUS = "Pipeline"

# The response is an object keyed by project id, not the array the notices API
# returns. Asserted below, because iterating a list of dicts and iterating a dict
# of dicts both "work" in Python and one of them yields strings.
CONTAINER = "projects"

# Present on all 42 recorded rows. Deliberately absent from this list, with the
# recorded count: `impagency` and `borrower` (35 each, and absent together on the
# same 7 rows), `project_abstract` (30 non-empty), `milestones` (42, unused here),
# and the financing amounts (26 to 41), which the normaliser explains it does not
# carry. What is required is what the filter check and the mapper read on all 42.
REQUIRED_FIELDS = (
    "id",
    "project_name",
    "status",
    "countrycode",
    "last_stage_reached_name",
    "public_disclosure_date",
    "boardapprovaldate",
    "pdo",
)

# The public page for one project. Verified 200 on 2026-09-12 for P517776 and for
# P173108, the oldest entry in the set.
PROJECT_URL = "https://projects.worldbank.org/en/projects-operations/project-detail/{id}"


class WorldBankPipelineConnector(FeedConnector):
    """One request for the whole pipeline set, checked against what was asked for."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused: there is no CPV code anywhere in this source, so every project
        # reaches the lexicon stage. Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def params(self) -> dict:
        return {
            "format": "json",
            "rows": ROWS,
            # Without this the response carries 15 fields and none of the four the
            # mapper and the filter check read. See fact 1 in the module docstring.
            "fl": "*",
            "countrycode_exact": country_query(self.source.covers),
            "status_exact": PIPELINE_STATUS,
        }

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        response = client.get(self.source.api_url, params=self.params())
        response.raise_for_status()
        document = response.json()

        rows = parse_projects(document, countries=frozenset(self.source.covers))
        raw_notices = [
            self.raw_notice(
                url=PROJECT_URL.format(id=row["id"]),
                payload=json.dumps(row, ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for row in rows
        ]

        if len(raw_notices) > self.source.expected_max:
            log.warning(
                "worldbank_pipeline_ceiling_reached",
                fetched=len(raw_notices),
                ceiling=self.source.expected_max,
                detail="raise expected_items_per_run in sources/worldbank_pipeline.yaml",
            )

        log.info("worldbank_pipeline_fetch", projects=len(raw_notices))
        return raw_notices


def country_query(covers: list[str]) -> str:
    """The `countrycode_exact` value for the registry's covered countries.

    `^` is the OR separator. Unlike the notices API, which matches on the Bank's
    own country spellings ("Gambia, The"), this endpoint holds ISO 3166-1 alpha-2
    codes and the registry's `covers` list can be sent as it stands. All 19 were
    checked individually against the live API on 2026-09-12: every one returns
    rows, Kosovo included as XK, and the 19 per-country totals sum to exactly the
    3,633 the combined query returns, so no covered project is double counted and
    no code is silently matching nothing.

    The join was checked for the order sensitivity that `monitor/connectors/
    worldbank.py` found on the same host, where a `project_ctry_name` containing an
    apostrophe is dropped from a `^` list unless it comes first. It does not reach
    here, and that was measured rather than reasoned from the codes having no
    apostrophe in them: forward and reversed both return 42, dropping BJ from the
    list also returns 42, and BJ on its own returns 185 projects of which 0 are
    Pipeline. Benin's absence from the recorded set is Benin, not a lost value.
    """
    return "^".join(covers)


def parse_projects(document: dict, *, countries: frozenset[str]) -> list[dict]:
    """The projects of one response, checked for shape, completeness and filter.

    Four checks, in the order a failure is cheapest to explain:

      1. The container, its type, and the fields the mapper reads, so a renamed
         field raises instead of yielding fewer projects.
      2. That the whole result set was read. `total` is what the server matched;
         anything above what came back means the set has outgrown one request and
         this connector would be reading a silent fraction of it.
      3. That the server honoured `status_exact`. `status=Pipeline` without the
         suffix returns the whole corpus, so a row in any other status means the
         suffix was dropped and Closed projects from the 1990s are being scored.
      4. That the server honoured `countrycode_exact`, for the same reason on the
         other axis.
    """
    if CONTAINER not in document:
        raise ValueError(f"World Bank projects response has no {CONTAINER!r} key; got {sorted(document)}")

    container = document[CONTAINER]
    if not isinstance(container, dict):
        raise ValueError(
            f"World Bank projects {CONTAINER!r} is a {type(container).__name__}, not the id-keyed object "
            "this parser reads; the notices API returns an array and this one does not"
        )
    rows = list(container.values())

    total = int(document.get("total", len(rows)))
    if total > len(rows):
        raise ValueError(
            f"World Bank projects matched {total} rows and {len(rows)} came back; the pipeline set has "
            f"outgrown the single {ROWS}-row request this connector makes and needs paging"
        )

    for row in rows:
        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValueError(f"World Bank project {row.get('id', '?')} is missing {missing}")

        if row["status"] != PIPELINE_STATUS:
            raise ValueError(
                f"World Bank project {row['id']} is {row['status']!r}, which was not asked for; "
                "the status_exact filter was ignored and the response is the unfiltered corpus"
            )
        if not covered_codes(row, countries):
            raise ValueError(
                f"World Bank project {row['id']} is for {row['countrycode']!r}, none of which is covered; "
                "the countrycode_exact filter was ignored and the response is the unfiltered corpus"
            )
    return rows


def covered_codes(row: dict, countries: frozenset[str]) -> list[str]:
    """The covered ISO2 codes on one project row.

    `countrycode` is a list on every row: a Bank operation can name more than one
    country even though all 42 recorded ones name exactly one. Membership is what
    proves the filter was honoured; which single country the notice carries is the
    normaliser's question and it answers it separately.
    """
    return [code for code in row.get("countrycode") or [] if code in countries]
