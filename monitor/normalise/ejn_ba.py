"""EJN e-Nabavke BiH notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/ejn_ba.json.gz`, recorded 2026-09-19: 672
notices, each already joined to its lots and CPV codes by
`monitor/connectors/ejn_ba.py`'s own `build_payload` (`{"notice", "lots",
"lot_cpv_links", "cpv_codes"}`). This module reads that shape and nothing the
connector has not already resolved; `lot_cpv_links` is not read again here, since
`cpv_codes` is already the resolved set for the notice.

**Country and currency are both statutory constants, the way `fts.py` fixes
`COUNTRY` for Find a Tender.** `country` is `BA` for every notice: this is Bosnia
and Herzegovina's own state-level procurement register and carries no other
country. There is no currency field anywhere in the API - not on the notice, not
on a lot (`sources/ejn_ba.yaml`) - so `CURRENCY` is fixed to `BAM`, the
convertible mark (konvertibilna marka), introduced as the state's sole legal
tender by the Law on the Central Bank of Bosnia and Herzegovina (1997) under a
currency board arrangement, pegged first to the Deutsche Mark and, since 2002, to
the euro at 1.95583:1. No source publishes a price in anything else here, so
there is nothing to detect and nothing to guess.

**Admin level is read per notice, not defaulted for the whole source** (unlike
`sources/ejn_ba.yaml`'s own `admin_level: national`, which describes the
platform, not a claim about any one buyer - see that file's own note).
`ContractingAuthorityAdministrativeUnitType` is a closed six-value enum and the
recorded window hit all six (`Entity` 237, `Municipality` 171, `Canton` 140,
`City` 65, `Country` 42, `District` 17). Two of the six are unambiguous: `Country`
is a state-level body and `Entity` is one of the Federation of Bosnia and
Herzegovina or Republika Srpska - each a constitutional entity whose own
institutions run across a population in the millions, so both are `national`.
The other four are sub-national and split the same way
`sources/simap.yaml`'s office-directory levels do for Switzerland: `Canton` is
one of the Federation's ten cantonal governments, a tier below the entity, so
`regional`, the same slot simap gives its own cantonal offices; `District` is
Brčko District, the one self-governing unit that sits outside both entities'
cantonal structure rather than inside a municipality, so it is read at the same
tier as a canton rather than as a local authority. `City` and `Municipality` are
both units of local self-government under BiH's local-government law and are
both `local`, the same slot simap gives its own communal offices. An unrecognised
seventh value raises rather than being carried through blind, since it would mean
the enum changed under this connector (rule 4).

**Language is detected by script, because no field states it**
(`sources/ejn_ba.yaml`'s own finding: `ietfTag` is a request-time display
parameter for the API's own enum-label localisation, not a stored property of a
notice). Serbian, spoken mainly in Republika Srpska, is written in Cyrillic
script on this platform; Bosnian and Croatian share the Latin script and cannot
be told apart by script alone - the same limit `sources/simap.yaml` does not have
to face because simap states `creationLanguage` directly. So a Cyrillic letter
anywhere in the title or the buyer's name means Serbian, at 0.9 confidence
because the script is a strong but not certain signal (a Latin transliteration of
a Serbian buyer's name is possible and not checked for); everything else is
labelled `bs` at 0.6, because Latin-script Bosnian and Croatian are genuinely
indistinguishable here and the translation stage (step 14) has to handle both
regardless of which of the two this column claims. 180 of the 672 recorded
notices carry Cyrillic script.

**Value is read per lot, with no summing, ever (rule 9).** The notice DTO itself
has no value field; `EstimatedValue` lives only on a `Lots` row, and the
connector's own docstring establishes that every procedure gets at least one
`Lots` row, split or not - so `lots` here is never empty, and a payload where it
is raises. When a notice's `lots` list holds exactly one row (556 of the 672
recorded, every `HasLots: false` procedure and none other in this window), that
row's own `EstimatedValue` is the notice's `estimated_value`, in BAM, or nothing
when the row states none (1 of the 556). When it holds more than one row (116 of
672, every `HasLots: true` procedure - the API also returns one unsplit "whole
procedure" row alongside the numbered lots, whose value is measured to equal the
sum of the numbered ones on all 116, which is exactly the reason the whole list
is quoted rather than trusted as a total: this module does not know, from the
fields it reads, which row is the summary and which are the split, and rule 9
forbids doing that arithmetic itself either way), `estimated_value` stays `None`
and `value_note` quotes what every row in the list published, built the same way
`monitor/normalise/boamp.py` builds its own per-lot note from a `value_note:`
block in the registry (`sources/ejn_ba.yaml`), with the same four keys. Lots are
numbered by their position in the `lots` list, in the order the payload carries
them, because nothing in the fields this module reads (`EstimatedValue`,
`ShortDescription`) names a lot number reliably across both the split and
unsplit rows.

**The body joins every lot's own description, in that same order** (rule 9: the
project-level description is not always where a BiH notice puts its detail - the
recorded window's own multi-lot procedures put a paragraph on the whole
procurement in the unsplit row and a paragraph per lot on the split rows, and
losing the latter would lose most of what a multi-lot notice actually says).
Non-empty `ShortDescription`s are joined with a blank line; 127 of the 1,101
recorded rows carry none and contribute nothing rather than an empty paragraph.
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache

import structlog

from monitor.connectors.ejn_ba import notice_url
from monitor.models import Notice, Source
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value
from monitor.registry import load_sources

log = structlog.get_logger(__name__)

SOURCE_ID = "ejn_ba"
COUNTRY = "BA"

# Bosnia and Herzegovina's sole legal tender, fixed by statute rather than read
# from any field - see the module docstring. Every price on this source is BAM.
CURRENCY = "BAM"

# `ContractingAuthorityAdministrativeUnitType`, the closed six-value enum
# `monitor/connectors/ejn_ba.py` validates on the way in. See the module
# docstring for why each value lands where it does. A value not in this mapping
# raises rather than defaulting to the source's own `admin_level: national`.
ADMIN_LEVEL_BY_UNIT_TYPE = {
    "Country": "national",
    "Entity": "national",
    "Canton": "regional",
    "District": "regional",
    "City": "local",
    "Municipality": "local",
}

# Any Cyrillic letter in the title or buyer name reads as Serbian; see the module
# docstring for why Latin script cannot in turn distinguish Bosnian from Croatian.
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
LANGUAGE_CYRILLIC = ("sr", 0.9)
LANGUAGE_LATIN = ("bs", 0.6)

# `Announced` carries 0 to 3 fractional-second digits and `monitor/normalise/
# dates.py` has no `%f` format, the same gap `monitor/normalise/boamp.py` found
# and fixed the same way: the fraction is noise on a publication timestamp and
# dropping it before parsing changes nothing a reviewer could act on.
FRACTION = re.compile(r"(?<=:\d\d)\.\d+")

# The four keys `value_note` in sources/ejn_ba.yaml must carry: the sentence
# around the lot figures, one valued lot, one unvalued lot, and what separates
# lots. The strings themselves live only there (rule 6); `value_note_phrasing`
# raises on a missing key rather than supplying one. Same shape as
# `monitor/normalise/boamp.py`'s own `VALUE_NOTE_KEYS`.
VALUE_NOTE_KEYS = ("lot_only", "lot_valued", "lot_unvalued", "separator")


def map_notice(raw: dict) -> MappedNotice:
    """One EJN BA payload (`notice` plus its resolved `lots` and `cpv_codes`) to a Notice."""
    for part in ("notice", "lots", "lot_cpv_links", "cpv_codes"):
        if part not in raw:
            raise ValueError(f"ejn_ba payload is missing {part!r}; got {sorted(raw)}")

    notice = raw["notice"]
    lots = raw["lots"]
    external_id = str(notice.get("Id") or "").strip()
    if not external_id:
        raise ValueError("ejn_ba notice has no Id")

    title = (notice.get("ProcedureName") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: notice has no ProcedureName")
    if not lots:
        # The connector's own `build_payload` raises before this ever reaches a
        # mapper - every procedure has at least one Lots row (module docstring) -
        # so this is a defensive check against that invariant breaking, not a
        # case the recorded window contains.
        raise ValueError(f"{external_id}: notice has no lots")

    url = notice_url(_source().api_url, notice)
    body = lot_body(lots)
    estimated_value, value_currency, value_note = notice_value(lots, external_id=external_id)
    language, language_confidence = notice_language(notice)

    result = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        # The buyer as the notice names it. This is what the record builder
        # proposes as text; no Account is resolved here or anywhere in the
        # pipeline (CLAUDE.md, export target).
        buyer=(notice.get("ContractingAuthorityName") or "").strip(),
        country=COUNTRY,
        admin_level=admin_level(notice, external_id=external_id),
        published_at=parse_published(without_fraction(notice.get("Announced") or ""), source_id=SOURCE_ID, url=url),
        deadline_at=parse_deadline(notice.get("ApplicationDeadlineDateTime") or "", source_id=SOURCE_ID, url=url),
        language=language,
        language_confidence=language_confidence,
        cpv_codes=extract_codes(*(code.get("Code", "") for code in raw["cpv_codes"])),
        estimated_value=estimated_value,
        value_currency=value_currency,
        value_note=value_note,
        body=body,
        status="detected",
    )
    # No language a Latin-script/Cyrillic detection could call English, and no
    # field on this API supplies one anyway - every notice from here reaches the
    # step 14 translation stage.
    return MappedNotice(notice=result)


def admin_level(notice: dict, *, external_id: str) -> str:
    """The buyer's level of government, from the notice's own administrative unit type."""
    unit_type = notice.get("ContractingAuthorityAdministrativeUnitType")
    if unit_type not in ADMIN_LEVEL_BY_UNIT_TYPE:
        raise ValueError(
            f"{external_id}: ContractingAuthorityAdministrativeUnitType is {unit_type!r}; add it to "
            "ADMIN_LEVEL_BY_UNIT_TYPE in monitor/normalise/ejn_ba.py (known: "
            f"{sorted(ADMIN_LEVEL_BY_UNIT_TYPE)})"
        )
    return ADMIN_LEVEL_BY_UNIT_TYPE[unit_type]


def notice_language(notice: dict) -> tuple[str, float]:
    """Detected by script; see the module docstring for why this is a detection
    and not a reading, and why Latin script cannot resolve further than `bs`."""
    text = f"{notice.get('ProcedureName') or ''} {notice.get('ContractingAuthorityName') or ''}"
    return LANGUAGE_CYRILLIC if CYRILLIC.search(text) else LANGUAGE_LATIN


def lot_body(lots: list[dict]) -> str:
    """Every lot's own description, non-empty ones only, in the order given.

    127 of the 1,101 recorded lot rows carry no `ShortDescription`; those
    contribute nothing rather than an empty paragraph. See the module docstring
    for why this reads every row in `lots` - including the one unsplit "whole
    procedure" row a split notice also carries - rather than only the numbered
    ones.
    """
    descriptions = [(lot.get("ShortDescription") or "").strip() for lot in lots]
    return "\n\n".join(description for description in descriptions if description)


def notice_value(lots: list[dict], *, external_id: str) -> tuple[Decimal | None, str | None, str]:
    """The published value, or (None, None, note) when several lots each state their own.

    Exactly one `lots` row (every `HasLots: false` procedure in the recorded
    window, 556 of 672): that row's own value, or none where it states none.
    More than one (every `HasLots: true` procedure, 116 of 672): never summed
    (rule 9) - see the module docstring for why this module cannot tell the
    unsplit summary row from the numbered ones using only the fields it reads,
    and quotes every row in `value_note` instead.
    """
    if len(lots) == 1:
        amount, currency = published_value(lots[0].get("EstimatedValue"), CURRENCY, source_id=SOURCE_ID)
        return amount, currency, ""
    return None, None, lot_value_note(lots, external_id=external_id)


def lot_value_note(lots: list[dict], *, external_id: str) -> str:
    """What every row in `lots` published about its value, quoted and never summed.

    Numbered by position in the list, not by any field this module reads (see
    the module docstring). Empty when none of the rows states a positive value,
    the same posture `monitor/normalise/boamp.py` takes for its own lots: a note
    that says nothing was published anywhere adds nothing `estimated_value=None`
    does not already say.
    """
    phrasing = value_note_phrasing()
    parts: list[str] = []
    valued = False
    for position, lot in enumerate(lots, start=1):
        amount, currency = published_value(lot.get("EstimatedValue"), CURRENCY, source_id=SOURCE_ID)
        if amount is None:
            parts.append(phrasing["lot_unvalued"].format(number=position))
        else:
            valued = True
            parts.append(phrasing["lot_valued"].format(number=position, amount=amount, currency=currency))

    if not valued:
        return ""
    return phrasing["lot_only"].format(lots=phrasing["separator"].join(parts))


def without_fraction(value: str) -> str:
    """`2026-09-18T20:55:45.157Z` -> `2026-09-18T20:55:45Z`. See FRACTION above."""
    return FRACTION.sub("", value)


@lru_cache(maxsize=1)
def _source() -> Source:
    """This source's own registry row, read once. `api_url` builds the notice URL
    the same way `monitor/connectors/ejn_ba.py`'s `notice_url` does for the
    connector, and `value_note` is read through `value_note_phrasing` below -
    both from the same `Source` rather than repeated here (rule 6)."""
    return next(candidate for candidate in load_sources() if candidate.id == SOURCE_ID)


def value_note_phrasing() -> dict[str, str]:
    """`value_note` from sources/ejn_ba.yaml. Every key in `VALUE_NOTE_KEYS` must
    be present: a missing one raises naming it, because a default written here
    would be the placeholder string rule 6 keeps out of `.py` files."""
    source = _source()
    for key in VALUE_NOTE_KEYS:
        if key not in source.value_note:
            raise ValueError(
                f"sources/{SOURCE_ID}.yaml: value_note is missing the key {key!r} (needs {VALUE_NOTE_KEYS})"
            )
    return source.value_note
