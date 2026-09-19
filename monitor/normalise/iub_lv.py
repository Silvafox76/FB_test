"""IUB Open Data (Latvia) notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/iub_lv.json`, recorded 2026-09-19: the
whole day 18-09-2026, 215 records of every notice type. This module is measured
against `monitor/connectors/iub_lv.py`'s own `select()` output for that day - 49
kept records (`pil-contract` 33, `pil-contract-social` 8, `mk-contract` 6,
`sps-iv-contract` 1, `sps-contract` 1) - wrapped the way `fetch_raw` wraps them:
`{"published_day": "YYYY-MM-DD", "record": {...}}`.

**Country and currency are both statutory-adjacent constants, the way `fts.py`
fixes `COUNTRY` for Find a Tender and `ejn_ba.py` fixes `CURRENCY` for the
convertible mark.** `organizationData.countryCode` is `LVA` on all 49 kept
records, so `country_alpha2` resolves it on every one measured; `DEFAULT_COUNTRY`
exists only as the fallback for a record the countryCode field is genuinely
absent on, since IUB is Latvia's own national procurement register and every
notice on it is, at minimum, published by a body operating under Latvian
procurement law even where a buyer's own country field is silent. `CURRENCY` is
fixed to `EUR` for the same reason `ejn_ba.py` fixes `BAM`: the source states no
currency anywhere in its schema (`additionalInformation.estimatedValue` is a bare
numeric string, confirmed on every valued lot in the recorded day), and Latvia's
statutory currency has been the euro since 1 January 2014, when Council
Implementing Decision 2013/779/EU authorised Latvia's adoption of the euro under
Regulation (EC) No 974/98, replacing the lats permanently and without a
transitional dual-currency period for pricing.

**Admin level is one value for the whole source, following `fts.py`'s own
reasoning for Find a Tender rather than `ejn_ba.py`'s per-notice reading.** IUB is
a single national procurement register with buyers of every tier - state bodies
and municipal administrations both appear in the 49 kept records
(`monitor/connectors/iub_lv.py`'s own docstring) - and, unlike EJN's
`ContractingAuthorityAdministrativeUnitType`, nothing in the schema states a
buyer's tier at all. `DEFAULT_ADMIN_LEVEL = "national"` describes the platform,
not a claim that every buyer is central government.

**Deadline is the earliest of every lot's own end date and time, reshaped locally
rather than by extending `monitor/normalise/dates.py`.**
`lots[].tenderingProcess.deadlineReceiptTendersEndDate` is `DD/MM/YYYY` -
confirmed day-first, not month-first, because several recorded values (`19/10/2026`,
`20/10/2026`, `21/09/2026`, `28/09/2026`, `30/09/2026`) carry a first component
above 12, which cannot be a month. `dates.py` deliberately carries no
day-first-vs-month-first format for exactly this reason (its own docstring: "a
source that publishes them needs its own mapper deciding which it means"), so
this module does the deciding, once, for this one field, and hands
`parse_deadline` a string it can already read (`YYYY-MM-DDTHH:MM`) rather than
teaching the shared module a new ambiguous shape. `dates.py` itself needed no
change. Present on every lot of every one of the 49 kept records; a multi-lot
notice's lots share one deadline on every recorded case, so "earliest" is a
defensive `min()` over whatever a future day's file states rather than an
assumption that they always agree.

**Value follows `ejn_ba.py`'s rule exactly: read per lot, never summed.** A
single-lot notice's own `additionalInformation.estimatedValue` is the notice's
`estimated_value`, in EUR, or nothing where the lot states none (18 of the 39
single-lot notices recorded). A notice with more than one lot (10 of 49) never
gets a summed total; `estimated_value` stays `None` and `value_note` quotes every
lot's own figure, numbered by position in the `lots` list - the same numbering
`ejn_ba.py` and `boamp.py` both use, because nothing in the fields this module
reads names a lot number reliably (IUB's own `sequenceNumber` happens to agree
with list position on every recorded lot, but is not read here, for the same
reason `ejn_ba.py` does not trust a field it has not had to). 3 of the 10
multi-lot notices have at least one valued lot and get a non-empty `value_note`;
the other 7 have none, and get an empty one - "nothing published" is what
`estimated_value=None` and no note already says, and a note repeating that adds
nothing (the same posture `ejn_ba.py`'s own `lot_value_note` takes). The sentence
around the figures is `value_note` in `sources/iub_lv.yaml`, read through the
registry (rule 6); the figures themselves are the lots'.

**Buyer is `organizationData.name`, and the recorded fixture contradicts the
brief this module was written to.** The brief states the field is absent on the
6 `mk-contract` records and that the Notice convention (empty string) should
apply there. It is not: all 6 `mk-contract` records in the recorded fixture carry
a populated `organizationData.name` - the private-law body itself (a
Cabinet-Regulation-104 procedure is run *by* that body, e.g. `"Latvijas
Samariešu apvienība"`, not by a public authority on its behalf), which is exactly
the buyer a `mk-contract` notice has. The `.get("name") or ""` read below still
falls back to the empty string, matching the Notice convention and every other
normaliser in this registry, but that branch is not exercised anywhere in this
fixture; see `tests/unit/test_normalise_iub_lv.py` for a synthetic case that
exercises it, and the session's own report for this discrepancy.

**Body is `procurementProject.description`, carried verbatim, including where it
repeats the title.** 47 of the 49 kept records state a description identical to
`name` and 1 states none at all; unlike `ghana.py`, this module does not drop a
repeated description to an empty body, because nothing here has measured that
IUB's index ever truncates or summarises the way GHANEPS's own description field
does - a description that repeats the title is what the buyer published, and
rule 9 keeps it.

**CPV is `cpvType` plus every code in `additionalCpvType`.** Empty on the same 6
`mk-contract` records that also lack a buyer-side tier field (43 of 49 carry at
least the main code); `monitor/filter/cpv.py` reads an absent code as "not a
failed match", so those six still reach the lexicon.

**URL and external id both come from the connector, not reimplemented here.**
`notice_url()` already resolves the tender's own EIS procedure page where one
exists and falls back to the day's own open-data file otherwise
(`monitor/connectors/iub_lv.py`); this module calls it rather than repeating its
judgement. `identifier` is the external id - a UUID on every one of the 49
recorded, confirmed against the fixture rather than assumed.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

import structlog

from monitor.connectors.iub_lv import notice_url
from monitor.models import Notice, Source
from monitor.normalise.codes import country_alpha2
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value
from monitor.registry import load_sources

log = structlog.get_logger(__name__)

SOURCE_ID = "iub_lv"
LANGUAGE = "lv"

# Latvia's statutory currency since 1 January 2014 (Council Implementing
# Decision 2013/779/EU, under Regulation (EC) No 974/98) - see the module
# docstring. Nothing in this source's schema states a currency, the way
# ejn_ba.py's own CURRENCY note explains for the convertible mark.
CURRENCY = "EUR"

# The fallback only: every one of the 49 kept records recorded carries
# organizationData.countryCode "LVA", so country_alpha2 resolves it directly.
# This exists for the record a future day's file states none on - IUB is
# Latvia's own national procurement register, so its absence is read as this
# platform's own country rather than as an unknown one (rule 4: an unknown code
# elsewhere still raises; this is the one field this source's own identity
# already answers).
DEFAULT_COUNTRY = "LV"

# IUB is a national procurement register with buyers of every tier and no field
# stating one, the same reasoning fts.py gives DEFAULT_ADMIN_LEVEL for Find a
# Tender's own unqualified buyers.
DEFAULT_ADMIN_LEVEL = "national"

# lots[].tenderingProcess.deadlineReceiptTendersEndDate, day-first - see the
# module docstring for how this was confirmed against the recorded data rather
# than assumed from documentation.
DEADLINE_DATE = re.compile(r"^(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})$")

# The four keys `value_note` in sources/iub_lv.yaml must carry: the sentence
# around the lot figures, one valued lot, one unvalued lot, and what separates
# lots. The strings themselves live only there (rule 6); `value_note_phrasing`
# raises on a missing key rather than supplying one. Same shape as
# `monitor/normalise/ejn_ba.py`'s own `VALUE_NOTE_KEYS`.
VALUE_NOTE_KEYS = ("lot_only", "lot_valued", "lot_unvalued", "separator")


def map_notice(raw: dict) -> MappedNotice:
    """One `{"published_day", "record"}` payload to a Notice."""
    for part in ("published_day", "record"):
        if part not in raw:
            raise ValueError(f"iub_lv payload is missing {part!r}; got {sorted(raw)}")

    day = date.fromisoformat(raw["published_day"])
    record = raw["record"]
    external_id = str(record.get("identifier") or "").strip()
    if not external_id:
        raise ValueError("iub_lv record has no identifier")

    title = (record.get("name") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: record has no name")

    lots = record.get("lots") or []
    if not lots:
        # The connector's own `select()` requires `lots` to be a present key but
        # not that it be non-empty; every one of the 49 kept records recorded
        # carries at least one, so this is a defensive check against that
        # invariant breaking rather than a case the fixture contains.
        raise ValueError(f"{external_id}: record has no lots")

    url = notice_url(_source().api_url, day, record)
    body = (((record.get("procurementProject") or {}).get("description")) or "").strip()
    estimated_value, value_currency, value_note = notice_value(lots)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        # The buyer as the notice names it. See the module docstring for why
        # this falls back to the empty string on a branch the recorded fixture
        # never actually exercises. No Account is resolved here or anywhere in
        # the pipeline (CLAUDE.md, export target).
        buyer=((record.get("organizationData") or {}).get("name") or "").strip(),
        country=notice_country(record),
        admin_level=DEFAULT_ADMIN_LEVEL,
        published_at=parse_published(raw["published_day"], source_id=SOURCE_ID, url=url),
        deadline_at=notice_deadline(lots, external_id=external_id, url=url),
        # IUB publishes in Latvian only and states no language field; see the
        # open-data page's own "Data is offered in Latvian" (sources/iub_lv.yaml).
        language=LANGUAGE,
        language_confidence=1.0,
        cpv_codes=notice_cpv_codes(record),
        estimated_value=estimated_value,
        value_currency=value_currency,
        value_note=value_note,
        body=body,
        status="detected",
    )
    # No English is published anywhere on this source; every notice from here
    # reaches the step 14 translation stage.
    return MappedNotice(notice=notice)


def notice_country(record: dict) -> str:
    """`organizationData.countryCode`, alpha-3, or `DEFAULT_COUNTRY` where the
    field is absent. See the module docstring for why the fallback is this
    source's own country rather than a guess."""
    code = (record.get("organizationData") or {}).get("countryCode")
    if not code:
        return DEFAULT_COUNTRY
    return country_alpha2(code)


def notice_cpv_codes(record: dict) -> list[str]:
    """`cpvType` plus every code in `additionalCpvType`. Empty on the 6
    `mk-contract` records that state neither."""
    additional = record.get("additionalCpvType") or []
    return extract_codes(record.get("cpvType") or "", *additional)


def notice_value(lots: list[dict]) -> tuple[Decimal | None, str | None, str]:
    """The published value, or (None, None, note) when several lots each state
    their own. See the module docstring for why a single lot's figure is the
    notice's value and several are quoted rather than summed (rule 9)."""
    if len(lots) == 1:
        info = lots[0].get("additionalInformation") or {}
        amount, currency = published_value(info.get("estimatedValue"), CURRENCY, source_id=SOURCE_ID)
        return amount, currency, ""
    return None, None, lot_value_note(lots)


def lot_value_note(lots: list[dict]) -> str:
    """What every lot published about its value, quoted and never summed.

    Numbered by position in `lots`, the same choice `ejn_ba.py` and `boamp.py`
    both make for their own per-lot notes - see the module docstring for why
    IUB's own `sequenceNumber` is not read instead. Empty when none of the lots
    states a positive value (7 of the 10 multi-lot notices recorded): a note
    that says nothing was published anywhere adds nothing `estimated_value=None`
    does not already say.
    """
    phrasing = value_note_phrasing()
    parts: list[str] = []
    valued = False
    for position, lot in enumerate(lots, start=1):
        info = lot.get("additionalInformation") or {}
        amount, currency = published_value(info.get("estimatedValue"), CURRENCY, source_id=SOURCE_ID)
        if amount is None:
            parts.append(phrasing["lot_unvalued"].format(number=position))
        else:
            valued = True
            parts.append(phrasing["lot_valued"].format(number=position, amount=amount, currency=currency))

    if not valued:
        return ""
    return phrasing["lot_only"].format(lots=phrasing["separator"].join(parts))


def notice_deadline(lots: list[dict], *, external_id: str, url: str) -> datetime | None:
    """The earliest of every lot's own `deadlineReceiptTendersEndDate` combined
    with `...EndTime`. `min()` over whatever the lots state rather than reading
    the first lot only, because nothing here has measured that a future day's
    lots must always agree - see the module docstring for the recorded day,
    where they do."""
    deadlines = [d for lot in lots if (d := lot_deadline(lot, external_id=external_id, url=url)) is not None]
    return min(deadlines) if deadlines else None


def lot_deadline(lot: dict, *, external_id: str, url: str) -> datetime | None:
    """One lot's own end date and time, reshaped to a string `parse_deadline`
    already reads. See the module docstring for why the day-first reshaping
    happens here rather than in `monitor/normalise/dates.py`."""
    process = lot.get("tenderingProcess")
    if not isinstance(process, dict):
        # `mk-contract` carries `tenderingProcess: []` at the *record* level
        # (monitor/connectors/iub_lv.py's own docstring); nothing recorded shows
        # this at the *lot* level, but the guard costs nothing and matches the
        # connector's own defensiveness about this exact field.
        return None
    date_str = (process.get("deadlineReceiptTendersEndDate") or "").strip()
    time_str = (process.get("deadlineReceiptTendersEndTime") or "").strip()
    if not date_str or not time_str:
        return None

    match = DEADLINE_DATE.match(date_str)
    if match is None:
        raise ValueError(
            f"{external_id}: deadlineReceiptTendersEndDate {date_str!r} is not the DD/MM/YYYY shape every "
            "recorded lot carries; extend DEADLINE_DATE in monitor/normalise/iub_lv.py once the new shape is measured"
        )
    iso = f"{match['year']}-{match['month']}-{match['day']}T{time_str}"
    return parse_deadline(iso, source_id=SOURCE_ID, url=url)


@lru_cache(maxsize=1)
def _source() -> Source:
    """This source's own registry row, read once. `api_url` builds the notice
    URL the same way `monitor/connectors/iub_lv.py`'s `notice_url` does for the
    connector, and `value_note` is read through `value_note_phrasing` below -
    both from the same `Source` rather than repeated here (rule 6)."""
    return next(candidate for candidate in load_sources() if candidate.id == SOURCE_ID)


def value_note_phrasing() -> dict[str, str]:
    """`value_note` from sources/iub_lv.yaml. Every key in `VALUE_NOTE_KEYS` must
    be present: a missing one raises naming it, because a default written here
    would be the placeholder string rule 6 keeps out of `.py` files."""
    source = _source()
    for key in VALUE_NOTE_KEYS:
        if key not in source.value_note:
            raise ValueError(
                f"sources/{SOURCE_ID}.yaml: value_note is missing the key {key!r} (needs {VALUE_NOTE_KEYS})"
            )
    return source.value_note
