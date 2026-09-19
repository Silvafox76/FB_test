"""IUB Open Data, Latvia's Procurement Monitoring Bureau (Iepirkumu uzraudzības
birojs). One JSON file per calendar day, `{api_url}/{yyyy}/{mm}/{DD-MM-YYYY}.json`.

Recorded from a real call on 2026-09-19. The fixture is
`tests/contract/fixtures/iub_lv.json`: the whole day 2026-09-18 verbatim
(215 records of every notice type), the same day `sources/iub_lv.yaml` cites
as the last of its measured week (49 kept). Nothing in the fixture is
resampled or trimmed by type -- a connector that only ever saw pre-filtered
data could not prove its own filter works.

WHICH NOTICE TYPES THIS CONNECTOR KEEPS. `sources/iub_lv.yaml`'s own
classification, read against `lots[].result.decisionDate`, finds three
populations, not two: 11 confirmed post-decision types (`exclude_notice_types`
in the registry), five confirmed still-open calls for tenders, and a fourth
group -- planning notices and `pil-discussion`/`sps-discussion` -- the
registry entry deliberately leaves unresolved rather than guesses on. `Source`
has no positive "kept types" field, only the denylist `exclude_notice_types`
that `monitor/connectors/ted.py` and `monitor/connectors/doe.py` both use, and
a denylist here would silently pull the unresolved fourth group in alongside
the five confirmed ones. `KEPT_NOTICE_TYPES` below is therefore a second,
positive list, restated verbatim from the registry entry's own
"CONFIRMED AS A LIVE CALL FOR TENDERS" line rather than computed from
`exclude_notice_types`; `select()` still checks the exclude list too, so a
future registry edit that excludes one of these five is honoured rather than
silently overridden by this module's own constant.

THE URL, since IUB does not publish one page per notice the way TED or DÖE do.
`tenderingProcess.documentsURL` and `tenderingTerms.submissionURL` are the
same eis.gov.lv address wherever both are present -- confirmed on every kept
type except `mk-contract` (43 of 49 kept notices on the recorded day) -- a
real, public procedure page on Latvia's Electronic Procurement System,
fetched cold with no session and no login redirect. `mk-contract` (a
Cabinet-Regulation-104 procedure run directly by a private-law body outside
EIS, 6 of 49 kept) publishes neither: `documentsURL` is absent and
`submissionURL` is free text naming an email address or a postal address,
never a URL, on every one sampled. IUB publishes no other page for that
notice, so `notice_url()` falls back to the day's own open-data file,
addressed by the notice's own `identifier` -- not a retry after a failed
request (rule 1/2), a single deterministic choice of which address this
notice type actually has, the same shape as `monitor/connectors/ted.py`'s
`notice_url()` picking the English HTML link when present and the first
available language otherwise.

WHAT THIS SOURCE DOES NOT PUBLISH, both confirmed against the whole recorded
day rather than assumed:

  - No publication-date field of any kind, on any of the 215 records at any
    level of nesting (`lots[]` carries plenty of *procedural* dates --
    tender-opening, deadline, contract-signature -- but nothing states when
    IUB published the notice itself). The only fact that fixes a notice to a
    calendar day is which day's file it came from -- and the url is the
    tender's own EIS page for 43 of 49 kept notices, which carries no date
    either, so the url cannot carry it forward. The day's file is itself the
    publisher's own dated publication (`{yyyy}/{mm}/{DD-MM-YYYY}.json`), so
    the day is the published date by rule, not an approximation stood in for
    one: `fetch_raw()` wraps each record as `{"published_day": "YYYY-MM-DD",
    "record": {...}}` before it is stored, rather than leaving the
    normaliser to reconstruct it from a fetch time that only records when the
    pipeline happened to run.
  - No currency field anywhere. `lots[].additionalInformation.estimatedValue`
    states a bare number (`"1138842.98"`) on 46 of 215 records' lots and never
    a currency code, unlike DÖE's `purpose.estimatedValue.currencyID` or Find
    a Tender's `tender.value.currency`. Latvia's statutory currency is the
    euro, but the source itself never says so -- a flag for the normaliser
    rather than an assumption made here, since it is not a fact this
    connector can attach to a record the way `published_day` is.

Both gaps are read directly from the fixture, not asserted from documentation:
`sources/iub_lv.yaml` says this source carries "buyer contact, CPV, procedure
type, lots, values" without noting either absence.

A MISSING DAY IS NOT A 404. `sources/iub_lv.yaml` never actually tested this
case -- its seven measured days (12 to 18 September) all existed. Verified
live 2026-09-19: `19-09-2026.json`, `17-13-2026.json` (an invalid month) and
`01-01-2020.json` (years before IUB's open-data service existed) all answer
HTTP 200 with `content-type: text/html`, a ~21 KB page, not JSON and not a
404. `response.raise_for_status()` alone would not catch this. `fetch_day()`
instead checks the decoded body is a JSON list; a day with no file yet fails
exactly the way a genuinely broken response would (rule 4), and no case here
is special-cased into an empty day (rule 1).

PERSONAL DATA, redacted in the committed fixture, not by this connector
(same convention `tests/contract/fixtures/pcs_gb_sct.json` and
`contractsfinder_gb.json` already use for the identical hazard). Every kept
and excluded record's own `contactPoint`, and every buyer's
`organizationData.defaultContactPoint` / `buyerData[].defaultContactPoint`,
names IUB's registered notice contact by their own full name alongside a
personal work email and phone (`"name": "Anita Nikiforova"`,
`"electronicMail": "anita.nikiforova@vugd.gov.lv"`) on 412 of 215 x ~2 such
objects across the whole day. Wherever that shape's `name`, `electronicMail`
or `telephone` was non-empty it was replaced with
`"REDACTED (named individual, rule 19)"` / `"redacted@redacted.invalid"` /
`"REDACTED"`; `organizationData.name`, `buyerData[].name` and a winner's own
registered name (`isNaturalPerson: true` on a sole-trader winner is that
economic operator's registered legal identity, the substantive content of an
award notice, not a staff contact detail) were left untouched. Six
`mk-contract` records name a bidding contact directly inside free-text
`tenderingTerms.submissionURL` rather than a structured contact object
(`"sūtīt ... uz e-pastu viktors@edo.lv"`); those emails' local parts were
masked to `contact@<domain>` in place, keeping the sentence's meaning (where
to send a bid) without keeping the person.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# sources/iub_lv.yaml, "CONFIRMED AS A LIVE CALL FOR TENDERS, NOT EXCLUDED".
# See the module docstring for why this is a positive list rather than the
# `exclude_notice_types` denylist pattern `ted.py` and `doe.py` use.
KEPT_NOTICE_TYPES = frozenset(
    {
        "pil-contract",
        "pil-contract-social",
        "mk-contract",
        "sps-contract",
        "sps-iv-contract",
    }
)

# The default window when a scheduled pass calls with no since/until. IUB
# updates once daily at 04:00 EET for the *previous* calendar day ("data
# available for re-use shall be final and shall not be corrected" once
# published -- sources/iub_lv.yaml), so a run never asks for today's file:
# `days()` starts at yesterday, not today, unlike ted.py/doe.py's LOOKBACK_DAYS
# which count back from today itself.
LOOKBACK_DAYS = 2

# Every field a future normaliser reads on every kept notice. A renamed field
# raises rather than mapping a notice with no title or no buyer (rule 4).
# `tenderingProcess` and `tenderingTerms` are checked for presence as keys
# only, not as dicts: `mk-contract` carries `tenderingProcess: []` (a list,
# not an object) on every sampled record, a real shape of this source's own
# rather than a fixture defect, so `notice_url()` and the future normaliser
# both guard the type before reading into it.
REQUIRED_FIELDS = ("identifier", "name", "noticeType", "organizationData", "lots", "tenderingTerms", "cpvType")


class IubLvConnector(FeedConnector):
    """One GET per calendar day the window touches; each day's file already
    carries every notice type IUB published that day, so the type filter runs
    while parsing an already-fetched file rather than at the request (IUB's
    open-data API takes no query parameter of any kind -- confirmed live,
    a fixed URL per day, no `?` anywhere sources/iub_lv.yaml records).
    """

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # IUB's open-data API takes no query parameter at all (confirmed live:
        # every day's file answers the same fixed URL, nothing narrowed by a
        # `?`), and this connector does not filter by CPV either -- `select()`
        # keeps every KEPT_NOTICE_TYPES record regardless of `cpvType`, the
        # same as monitor/connectors/fts.py's own unused `cpv_prefixes`: the
        # free filter decides at step 5, on whatever CPV code the notice
        # states or the absence of one. Stored only so `monitor/fetch.py`'s
        # `build_connector` can construct every connector the same way.
        self.cpv_prefixes = cpv_prefixes

    def days(self, today: date | None = None, *, since: date | None = None, until: date | None = None) -> list[date]:
        """The calendar days this run reads, newest first, oldest included.

        `since`/`until` name an explicit window for a future backfill script,
        the same shape as `monitor/connectors/ted.py`'s own `since`/`until`
        (decision 59): the scheduled pass never sets them and reads the
        trailing `LOOKBACK_DAYS` ending yesterday.
        """
        end = until or ((today or date.today()) - timedelta(days=1))
        start = since or (end - timedelta(days=LOOKBACK_DAYS - 1))
        if start > end:
            raise ValueError(f"iub_lv: since {start} is after until {end}")
        span = (end - start).days
        return [end - timedelta(days=offset) for offset in range(span + 1)]

    def fetch_raw(
        self, client: httpx.Client, *, since: date | None = None, until: date | None = None
    ) -> list[RawNotice]:
        raw_notices: list[RawNotice] = []

        for day in self.days(since=since, until=until):
            records = self.fetch_day(client, day)
            kept = select(records, exclude_notice_types=self.source.exclude_notice_types)
            log.info("iub_lv_day", day=day.isoformat(), published=len(records), kept=len(kept))
            raw_notices.extend(
                self.raw_notice(
                    url=notice_url(self.source.api_url, day, record),
                    payload=json.dumps(wrap(day, record), ensure_ascii=False, sort_keys=True),
                    mime="application/json",
                )
                for record in kept
            )

        log.info("iub_lv_fetch", notices=len(raw_notices))
        return raw_notices

    def fetch_day(self, client: httpx.Client, day: date) -> list[dict]:
        """One day's file. One attempt; any failure raises (rules 2 and 3).

        Not a 404 for a day with no file yet -- see the module docstring --
        so this checks the decoded body is the JSON list every real day's
        file is, rather than trusting a 200 status code alone.
        """
        response = client.get(day_url(self.source.api_url, day))
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, list):
            raise ValueError(f"iub_lv day file for {day.isoformat()} is not a JSON list; got {type(document).__name__}")
        return document


def day_url(base: str, day: date) -> str:
    """`{base}/{yyyy}/{mm}/{DD-MM-YYYY}.json`, confirmed live 2026-09-19."""
    return f"{base}/{day:%Y}/{day:%m}/{day:%d-%m-%Y}.json"


def wrap(day: date, record: dict) -> dict:
    """The record as this connector stores it: the day's own file date beside
    the record it was read from, unmodified. The record itself carries no
    publication-date field at all (see the module docstring); the file it
    came from is the publisher's own dated publication, so `published_day` is
    the published date by rule, not a derived or invented one.
    """
    return {"published_day": day.isoformat(), "record": record}


def select(records: list[dict], *, exclude_notice_types: list[str]) -> list[dict]:
    """The notices this run keeps: the five confirmed live calls for tenders,
    checked for the fields a future normaliser reads.

    `exclude_notice_types` is checked as well as `KEPT_NOTICE_TYPES`, not
    instead of it: a registry edit that ever excludes one of the five is
    honoured rather than silently overridden by this module's own constant.
    """
    excluded = frozenset(exclude_notice_types)
    kept = []
    for position, record in enumerate(records):
        notice_type = record.get("noticeType")
        if notice_type is None:
            raise ValueError(f"iub_lv record {position} has no noticeType: {sorted(record)}")
        if notice_type not in KEPT_NOTICE_TYPES or notice_type in excluded:
            continue

        missing = [field for field in REQUIRED_FIELDS if field not in record]
        if missing:
            raise ValueError(f"iub_lv {notice_type} {record.get('identifier', '?')} is missing {missing}")
        kept.append(record)
    return kept


def notice_url(base: str, day: date, record: dict) -> str:
    """The tender's own procedure page, where the buyer runs one on EIS;
    otherwise the day's own open-data file, addressed by this notice's
    identifier. See the module docstring for why there is no third case.
    """
    tendering_process = record.get("tenderingProcess")
    documents_url = tendering_process.get("documentsURL") if isinstance(tendering_process, dict) else None
    if isinstance(documents_url, str) and documents_url.startswith("http"):
        return documents_url
    return f"{day_url(base, day)}?identifier={record['identifier']}"
