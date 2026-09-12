"""EU Funding and Tenders portal, the SEDIA search API at api.tech.ec.europa.eu.

Recorded from a real call on 2026-09-12. The fixture is
`tests/contract/fixtures/euft.json` and this parser is written against the fields
that are in it.

**The one thing this connector exists to defend against: the query is only read
when it arrives as a multipart file part, and when it is not it is ignored in
silence.** Measured on 2026-09-12 with httpx, same URL, same filter, three
encodings:

    files={"query": ("blob", body, "application/json")}   200,   368,489 results
    data={"query": json.dumps(query)}                     200, 4,184,545 results
    json=query                                            200, 4,184,545 results

4,184,545 is the whole portal index - events, news, person and organisation
records in 24 languages - and it comes back with HTTP 200 and a well-formed
response. A connector written the obvious way, with `json=`, would have looked
like it worked and poured the entire corpus into the pipeline. That is why the
request is built the way it is below and why `parse_results` re-checks every row
against what was asked for: the encoding requirement is undocumented, so it can
change, and the failure would be silent (rule 4 against a measured hazard).

The rest of the query language is narrow and was established the same way.
`terms` and `range` inside a `bool` work; `prefix`, `wildcard` and `query_string`
are all HTTP 400. A `terms` clause on a field the index does not have returns
zero rows rather than the whole corpus - the opposite of the World Bank API's
behaviour - so a misspelled field here reads as a quiet day, which is what
`expected_items_per_run` in the registry is for. `text` and `apiKey` are both
mandatory query parameters; `***` is the match-all value for `text` and any real
term filters on top of the body (`text=governance` cut the live set to 2).

**`apiKey=SEDIA` is a public portal constant and not a credential (rule 20).**
Measured: it is accepted from an unauthenticated client with no registration, no
header and no cookie; its value is the name of the index, which every document
echoes back in its own `DATASOURCE` field; omitting it is an HTTP 400 for a
missing parameter, and a wrong value is an HTTP 500 "An internal error occurred",
which is a server failing to resolve a database name rather than an
authentication system refusing an identity - that would be 401 or 403. Nothing
about it is secret and there is nothing here for Secrets Manager to hold.

Four more measured facts that shaped the request:

  1. **One notice is indexed once per portal UI language, and the copies are
     identical.** A tender in the window 2026-09-08 onward had 12 or 13 documents
     each; 28 distinct `callIdentifier` values produced 672 documents. Filtering
     `language: en` returned exactly 28 - one per tender, no tender missing and
     none doubled. The `language` field is the UI language of the copy and NOT the
     language the notice is written in: all 24 copies of the Guatemala tender
     carry its Spanish title. So this filter is deduplication, and it costs
     nothing in rule 9 terms; the notice's own language is read from the lot's
     tender-document reference in `monitor/normalise/euft.py`.
  2. **`DATASOURCE` separates a live index from a frozen archive.** `SEDIA` is
     current (newest record 2026-09-11 on the day of recording); the legacy
     `SEDIA_PRD_CENTRICITY` index stops dead at 2023-05-26, still carries
     "Open for submission" on tenders that closed in 2023, spells `procedureType`
     as a string where SEDIA uses a numeric code, and puts the procurement title
     in `caName` where SEDIA puts the contracting authority. Asking only for
     SEDIA removes all four problems at once and loses nothing current.
  3. **`status` is the source's own statement and it goes stale.** Asking for the
     two live statuses cuts the covered-country corpus from 365 documents to 35,
     and every one of the 330 removed is a tender the portal itself calls Closed
     or "Cancelled after publication". It is not a substitute for a date window:
     14 of the 35 that remain have a deadline already in the past and are still
     marked open. Dropping those is the filter's decision, not this module's
     (rule 5); they are read, mapped with the deadline that was published, and
     the reviewer sees the real date.
  4. `pageSize` is clamped at 100 in silence - 101 and 200 both return 100 - and
     `pageNumber` is 1-based.

There is no date window and that is deliberate, the same decision as
`monitor/connectors/worldbank_pipeline.py`. `startDate` filters correctly as a
`range` and sorts monotonically, so a window would work; it is not used because
notices for the 19 covered countries arrive at roughly one a week (5 in the six
weeks before recording), so a two-day lookback would return nothing on 27 days
out of 28 and rule 4's zero-yield check would be dead exactly where it is needed.
The whole live covered-country set is 35 documents and fits in one request, so
every run reads all of it, `content_hash` makes the repeats free (monitor/fetch.py
inserts nothing for a hash it already has), and zero yield means the query broke.
"""

from __future__ import annotations

import json

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# The index to search. Public portal constant, not a credential; see the docstring.
API_KEY = "SEDIA"

# The free-text half of the query. `***` is this API's match-all; the structured
# filtering is all in the `query` part, and `text` is a mandatory parameter that
# cannot be left out (HTTP 400).
MATCH_ALL_TEXT = "***"

# The API clamps anything above this to 100, silently.
PAGE_SIZE = 100

# The absolute bound on requests per run, above the registry's expected_max. The
# live covered set is 35 documents, so this is 14 times the measured need.
MAX_PAGES = 5

# `type` 0 is the portal's "Tender" facet: a call for tenders. The other values in
# the index are not procurement this pipeline can answer - 1 Grant and 2 "Calls
# for proposals" are EuropeAid grants and twinning (facet `contractType` over
# type 2 on 2026-09-12: Grants 255, Twinning 54, and no services, supplies or
# works at all), 8 is cascade funding, 6, 9 and 11 are topic and results pages,
# and ORGANISATION, PERSON and ANNOUNCEMENT are not notices.
DOCUMENT_TYPE = "0"

# The live index. See point 2 of the module docstring for what the other one is.
DATASOURCE = "SEDIA"

# The UI language of the one copy to read out of the 12 to 24 identical ones. Not
# the language the notice is written in; see point 1 of the module docstring.
INDEX_LANGUAGE = "en"

# Forthcoming and Open for submission: the two statuses that are still an
# opportunity. Closed (31094503) and "Cancelled after publication" are not asked
# for, the same acquisition-scope decision as TED's award types and the World
# Bank's Contract Award. This is the source's vocabulary rather than configuration
# (rule 6): nobody tunes a status code, it is what the API answers to, and the day
# it changes this connector must fail rather than be re-tuned. It is an inclusion
# rather than a `must_not` because the vocabulary mixes numeric codes with free
# strings, and an exclusion list over that would let an unknown new value through
# as an opportunity.
LIVE_STATUSES = ("31094501", "31094502")

# The portal's own numeric id for each covered country, keyed by the ISO code the
# registry uses. Same shape and same reasoning as `BANK_COUNTRY_NAMES` in
# monitor/connectors/worldbank.py: this is one source's vocabulary, so it does not
# belong in monitor/normalise/codes.py's standards tables, and it cannot go in
# sources/euft.yaml because `Source` forbids unknown fields and a `zone_codes`
# block there would have to be added to the model every source is validated
# against.
#
# All 19 verified against the live index on 2026-09-12 by faceting
# geographicalZones over the tender corpus; every one returns rows. The counts are
# in sources/euft.yaml.
ZONE_CODES = {
    # West Africa
    "BJ": "20000844",
    "BF": "20000840",
    "CI": "20000861",
    "GM": "20000899",
    "GH": "20000896",
    "LR": "20000942",
    "ML": "20000953",
    "MR": "20000958",
    "NE": "20000969",
    "NG": "20000971",
    "SN": "20001008",
    "SL": "20001006",
    "TG": "20001018",
    # Ukraine and the Western Balkans
    "UA": "20001031",
    "AL": "20000825",
    "BA": "20000836",
    "XK": "31008860",  # the portal labels it "Kosovo * UN resolution"
    "ME": "20001058",
    "MK": "20000952",
}

ISO2_BY_ZONE = {zone: code for code, zone in ZONE_CODES.items()}

# The container, and the fields that must be present on every notice or it cannot
# be mapped at all. Deliberately absent, with the count from the 35 recorded
# notices: `deadlineDate` and `cftTimezone` (25), which a prior information notice
# does not have because nothing is open yet, and `lots` language (25) for the same
# reason. `cftLeadContractingAuthorityCode` is required because it was on 35 of 35
# and it is the only place the buyer is named - `caName` is the procurement title
# on this datasource, not an authority.
CONTAINER = "results"
REQUIRED_FIELDS = (
    "identifier",
    "type",
    "DATASOURCE",
    "language",
    "status",
    "title",
    "description",
    "startDate",
    "geographicalZones",
    "mainCpv",
    "mainCpvCode",
    "callIdentifier",
    "cftLeadContractingAuthorityCode",
    "lots",
    "url",
)


class EuftConnector(FeedConnector):
    """The live covered-country tender set, in one multipart-encoded query."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Unused here: this source publishes CPV codes, but dropping a notice on
        # them is `monitor/filter/cpv.py`'s decision and not a connector's
        # (rule 5). The codes cannot be filtered at the query either - `prefix`
        # and `wildcard` are both HTTP 400, so 48/72/79 cannot be expressed.
        # Taken so every connector is built the same way.
        self.cpv_prefixes = cpv_prefixes

    def query(self) -> dict:
        """The structured half of the request: type, index, copy, status, country."""
        return {
            "bool": {
                "must": [
                    {"terms": {"type": [DOCUMENT_TYPE]}},
                    {"terms": {"DATASOURCE": [DATASOURCE]}},
                    {"terms": {"language": [INDEX_LANGUAGE]}},
                    {"terms": {"status": list(LIVE_STATUSES)}},
                    {"terms": {"geographicalZones": zone_codes(self.source.covers)}},
                ]
            }
        }

    def params(self, page: int) -> dict:
        return {
            "apiKey": API_KEY,
            "text": MATCH_ALL_TEXT,
            "pageSize": PAGE_SIZE,
            "pageNumber": page,
        }

    def files(self) -> dict:
        """The query as a multipart file part, which is the only encoding read.

        A plain form field and a JSON body are both accepted with HTTP 200 and
        both ignore the query; see the module docstring for the three measurements.
        """
        body = json.dumps(self.query(), ensure_ascii=False).encode("utf-8")
        return {"query": ("blob", body, "application/json")}

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        wanted_zones = frozenset(zone_codes(self.source.covers))
        raw_notices: list[RawNotice] = []
        total = 0

        for page in range(1, MAX_PAGES + 1):
            response = client.post(
                self.source.api_url,
                params=self.params(page),
                files=self.files(),
            )
            response.raise_for_status()
            document = response.json()
            total = document.get("totalResults", 0)

            results = parse_results(document, zones=wanted_zones)
            raw_notices.extend(
                self.raw_notice(
                    url=notice_url(result),
                    payload=json.dumps(result, ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
                for result in results
            )

            if len(results) < PAGE_SIZE:
                break
            if len(raw_notices) >= self.source.expected_max:
                log.warning(
                    "euft_ceiling_reached",
                    fetched=len(raw_notices),
                    ceiling=self.source.expected_max,
                    detail="raise expected_items_per_run in sources/euft.yaml",
                )
                break
        else:
            log.warning("euft_max_pages_reached", fetched=len(raw_notices), max_pages=MAX_PAGES)

        log.info("euft_fetch", notices=len(raw_notices), total=total)
        return raw_notices


def zone_codes(covers: list[str]) -> list[str]:
    """The portal's zone ids for the registry's covered countries.

    An ISO code with no zone id raises: sending 18 of 19 would read as a quiet
    week in the nineteenth, and this API returns zero rows for a value it does not
    recognise rather than refusing it.
    """
    missing = [code for code in covers if code not in ZONE_CODES]
    if missing:
        raise ValueError(f"no EU portal zone id for {missing}; add it to ZONE_CODES in monitor/connectors/euft.py")
    return [ZONE_CODES[code] for code in covers]


def parse_results(document: dict, *, zones: frozenset[str]) -> list[dict]:
    """The notices of one response, checked for fields and for the filter.

    Two checks, in the order a failure is cheapest to explain:

      1. The container and the fields the mapper reads, as every other connector
         does, so a renamed field raises instead of yielding fewer notices.
      2. That the server honoured the query. The query is only read when it
         arrives as a multipart file part and is otherwise ignored in silence
         (module docstring), so a row of the wrong type, from the archived index,
         in another UI language, with a status that was not asked for, or for a
         country that is not covered means the filter stopped being applied and
         the response is 4.18 million documents of the whole portal.
    """
    if CONTAINER not in document:
        raise ValueError(f"EU portal response has no {CONTAINER!r} key; got {sorted(document)}")

    results = document[CONTAINER]
    for position, result in enumerate(results):
        if not isinstance(result.get("metadata"), dict):
            raise ValueError(f"EU portal result {position} has no metadata object")
        metadata = result["metadata"]

        missing = [field for field in REQUIRED_FIELDS if not metadata.get(field)]
        if missing:
            raise ValueError(f"EU portal notice {position} ({reference(metadata)}) is missing {missing}")

        _check_asked_for(metadata, "type", DOCUMENT_TYPE, "the type filter")
        _check_asked_for(metadata, "DATASOURCE", DATASOURCE, "the DATASOURCE filter")
        _check_asked_for(metadata, "language", INDEX_LANGUAGE, "the language filter")

        status = one(metadata, "status")
        if status not in LIVE_STATUSES:
            raise ValueError(
                f"EU portal notice {reference(metadata)} has status {status!r}, which was not asked for; "
                "the status filter was ignored and the response is the unfiltered index"
            )

        if not zones.intersection(metadata["geographicalZones"]):
            raise ValueError(
                f"EU portal notice {reference(metadata)} is for zones {metadata['geographicalZones']}, "
                "none of them covered; the geographicalZones filter was ignored and the response is "
                "the unfiltered index"
            )
    return results


def _check_asked_for(metadata: dict, field: str, expected: str, what: str) -> None:
    value = one(metadata, field)
    if value != expected:
        raise ValueError(
            f"EU portal notice {reference(metadata)} has {field}={value!r}, not {expected!r}; "
            f"{what} was ignored and the response is the unfiltered index"
        )


def one(metadata: dict, field: str) -> str:
    """One single-valued metadata field. Every value in this API is a list.

    Raises where the list is empty or holds more than one value: both mean the
    field does not mean what this parser reads it as, and reading the first of
    several would be a guess.
    """
    values = metadata.get(field) or []
    if len(values) != 1:
        raise ValueError(f"EU portal notice {reference(metadata)} has {len(values)} values for {field!r}, expected one")
    return values[0]


def reference(metadata: dict) -> str:
    """The notice id for an error message, without raising inside one."""
    identifier = metadata.get("identifier") or metadata.get("callIdentifier") or ["?"]
    return identifier[0]


def notice_url(result: dict) -> str:
    """The public page for one notice, as the notice itself states it.

    The result carries the same address twice, in its own `url` field and in the
    metadata's, and they agree on all 35 recorded notices. The metadata's is the
    one read, because that is the object the normaliser is written against and a
    RawNotice whose url came from somewhere else would be harder to trace.
    """
    return one(result["metadata"], "url")
