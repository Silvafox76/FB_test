"""EU Funding and Tenders notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/euft.json`, recorded 2026-09-12: the 35
tenders that were live for the 19 covered countries on that day. The request that
produced it, and why it is shaped that way, is in `monitor/connectors/euft.py`'s
docstring.

Five things the recorded set settled.

1. **The deadline is a wall clock stamped with an offset it does not have.**
   `deadlineDate` reads `2026-09-18T10:00:59.000+0000` on all 25 notices that have
   one, always `+0000`, while `cftTimezone` names the zone the procurement
   actually closes in - eight of them in the fixture, from `Africa/Abidjan` at
   UTC+0 to `Europe/Skopje` at UTC+2. The `+0000` is wrong and the wall clock is
   right. Cross-checked against TED's eForms `deadline-receipt-tender-time-lot`,
   which carries the real offset, for four notices in four zones on 2026-09-12:
   603937-2026 `14:30:59+02:00` and 581930-2026 `14:30:59+02:00` (Podgorica),
   422869-2026 `12:00:59+02:00` (Skopje), and 549932-2026 `10:00:59+01:00`
   (Porto-Novo, the 2026-08-07 corrigendum to 494617-2026). The portal's wall
   clock matches all four; its offset matches none. Taking the `+0000` at face
   value would put every deadline one to two hours later than it is, which is the
   generous direction: it sends a reviewer to a tender that closed that morning.
   So the clock is read as local time in `cftTimezone`, and a `deadlineDate` that
   arrives with a real offset instead raises, because that would mean the API
   started publishing the instant and this rule would then be moving it.
   `closingDate` is the same instant spelled with a `Z` - identical to the second
   on all 8 notices carrying both - so it is not read; one field, one path
   (rule 1).

2. **The notice's own language is the lot's tender-document language, and a prior
   information notice does not have one.** `lots` is a JSON string holding the
   eForms lot structure, and
   `tenderingTerms.callForTendersDocumentReference.languageID` is an ISO 639-2
   code that `monitor/normalise/codes.py` already converts: 21 notices ENG, 4 FRA,
   never more than one value in a notice. The other 10 state nothing, and they are
   exactly the 10 prior information notices (`callIdentifier` ending `-PIN`,
   `procedureType` 47396222, no deadline either) - a PIN announces a procurement
   that has not opened, so there are no tender documents to have a language. Those
   take `DEFAULT_LANGUAGE` from `sources/euft.yaml` with `language_confidence` 0,
   the same shape as `monitor/normalise/doe.py`'s default admin level, and 0 means
   "the source stated nothing" rather than a weak guess. It is a real exposure and
   it is written down rather than smoothed over: 4 of the 35 notices are French,
   and a French PIN mapped as English is filtered against the English lexicon.
   What limits it is that the CPV stage runs first and passes on 48/72/79 without
   consulting the lexicon at all, so a French PIN for IT work still reaches the
   scorer, which sees the original French text.

3. **`caName` is not the contracting authority on this datasource.** It is the
   procurement title, byte for byte, on 35 of 35 notices. The buyer is the entry
   marked `isLeadAuthority` inside `cftLeadContractingAuthorityCode`, which is a
   JSON string and which is on all 35 with exactly one entry and exactly one lead.
   The names there are what the record builder wants - "Government of Montenegro,
   The Ministry of Finance, The Directorate for Finance, Contracting and
   Implementation", "Agence de Developpement de l'Enseignement Technique (ADET)" -
   and `caName` would have put the tender's own title in the buyer column of every
   exported record.

4. **A notice can be for several covered countries at once.** `geographicalZones`
   mixes country ids with region roll-ups ("West Africa Region", "Sub-Saharan
   Africa", "Region IPA instrument"), and 7 of the 35 name more than one covered
   country - one regional programme names 13. `Notice.country` holds one code, so
   the rule is the first covered country in `ZONE_CODES` order, which is the
   registry's `covers` order, which is the pilot's own priority order: West Africa
   before Ukraine and the Balkans. The full zone list stays in the stored payload,
   and a notice naming more than one is logged with all of them so the choice is
   visible rather than implied.

5. **No value is carried into `estimated_value_usd`.** 24 of the 35 state a
   contract amount and every one of them is in EUR. Converting it would need a
   rate this pipeline does not hold and would date the moment it was written, so
   the field stays None and the scorer's own estimate is the only one there is,
   the same decision as `monitor/normalise/simap.py`.

`description` is plain text on all 35 - no markup at any length from 38 to 2,065
characters - so it is stored as published with no stripping (rule 9). Nothing here
translates and no English rendering is claimed: the connector's `language: en`
filter picks one of the portal's identical UI copies, not a translation, so the
French notices reach step 14 with `title_en` and `body_en` empty, which is what
empty means (monitor/normalise/mapped.py).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog

from monitor.connectors.euft import ISO2_BY_ZONE, ZONE_CODES, one
from monitor.models import Notice
from monitor.normalise.codes import language_alpha2
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "euft"

# From sources/euft.yaml, which is the authority; the contract test asserts the
# two agree. The notice is published by the financier rather than the buyer, which
# is what lets an EU notice and the national notice for one procurement land in
# the same candidate cluster.
ADMIN_LEVEL = "donor"

# From sources/euft.yaml's `language`, likewise asserted by the contract test. Used
# only for a notice that declares none of its own; see point 2 of the docstring.
DEFAULT_LANGUAGE = "en"

# Not a weak reading of the text: the source stated nothing at all. A value above
# zero would claim evidence that does not exist.
LANGUAGE_NOT_STATED = 0.0

# The notice states its own document language, so there is nothing uncertain.
LANGUAGE_STATED = 1.0

# `2026-09-18T10:00:59.000+0000`, the one timestamp shape in this API. Written out
# rather than handed to monitor/normalise/dates.py because that module's formats
# stop at whole seconds and, more importantly, because the offset in this string is
# wrong and the rule below has to know it (point 1 of the docstring).
TIMESTAMP = "%Y-%m-%dT%H:%M:%S.%f%z"

# The eForms path to the language of a lot's tender documents.
TENDERING_TERMS = "tenderingTerms"
DOCUMENT_REFERENCE = "callForTendersDocumentReference"
LOTS_CONTAINER = "procurementProjectLots"


def map_notice(raw: dict) -> MappedNotice:
    """One EU portal tender to a Notice. Raises on anything unmappable."""
    metadata = raw["metadata"]
    external_id = one(metadata, "identifier")
    url = one(metadata, "url")
    title = one(metadata, "title").strip()
    # Plain text on all 35 recorded notices, stored as published (rule 9).
    body = one(metadata, "description").strip()
    language, confidence = notice_language(metadata)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        # The lead contracting authority, which on these notices is usually the
        # beneficiary country's own institution. Proposed as text; no Account is
        # resolved here or anywhere in the pipeline (CLAUDE.md, export target).
        buyer=lead_authority(metadata, external_id=external_id),
        country=country_alpha2(metadata, external_id=external_id),
        admin_level=ADMIN_LEVEL,
        published_at=published(metadata, external_id=external_id),
        deadline_at=deadline(metadata, external_id=external_id),
        language=language,
        language_confidence=confidence,
        cpv_codes=cpv_codes(metadata, external_id=external_id),
        # 24 of 35 state an amount and all of them are in EUR; see point 5.
        # 24 of the 35 state an amount, but as a programme budget line rather than a
        # contract value; see the module docstring. Nothing is carried.
        body=body,
        status="detected",
    )
    # The portal supplies no English rendering. Its `language` field names the UI
    # copy the connector asked for, not a translation, so the French notices go to
    # step 14 with both fields empty.
    return MappedNotice(notice=notice)


def notice_language(metadata: dict) -> tuple[str, float]:
    """The notice's own language and how sure the source is, as (code, confidence).

    One value or none. Two different declared languages in one notice raises
    rather than picking one: `Notice.language` chooses the lexicon, and choosing
    it by position would be a guess that drops the notice for the wrong reason.
    """
    declared = declared_languages(metadata)
    if not declared:
        return DEFAULT_LANGUAGE, LANGUAGE_NOT_STATED
    if len(declared) > 1:
        raise ValueError(
            f"{one(metadata, 'identifier')}: lots declare {sorted(declared)} as the tender-document "
            "language; one notice cannot be filtered against two lexicons"
        )
    return language_alpha2(next(iter(declared))), LANGUAGE_STATED


def declared_languages(metadata: dict) -> set[str]:
    """Every `languageID` the notice's lots give their tender documents."""
    document = json.loads(one(metadata, "lots"))
    lots = document.get(LOTS_CONTAINER) or []
    found = set()
    for lot in lots:
        reference = (lot.get(TENDERING_TERMS) or {}).get(DOCUMENT_REFERENCE) or {}
        code = (reference.get("languageID") or "").strip()
        if code:
            found.add(code)
    return found


def lead_authority(metadata: dict, *, external_id: str) -> str:
    """The buyer: the entry marked `isLeadAuthority` in the contracting authorities.

    Exactly one is required. None means this notice names no buyer at all, which
    on 35 of 35 recorded notices never happens; more than one means the source
    started publishing joint authorities, and the record builder proposes one
    buyer, so picking one here would be the invention appendix E forbids.
    """
    authorities = json.loads(one(metadata, "cftLeadContractingAuthorityCode"))
    leads = [entry for entry in authorities if str(entry.get("isLeadAuthority")).lower() == "true"]
    if len(leads) != 1:
        raise ValueError(
            f"{external_id}: cftLeadContractingAuthorityCode has {len(leads)} lead authorities "
            f"among {len(authorities)} entries, expected one"
        )
    name = (leads[0].get("name") or "").strip()
    if not name:
        raise ValueError(f"{external_id}: the lead contracting authority has no name")
    return name


def country_alpha2(metadata: dict, *, external_id: str) -> str:
    """The first covered country the notice names, in the registry's `covers` order.

    The zone list also carries region roll-ups and countries outside the pilot,
    both of which are ignored here; the connector has already refused a notice
    with no covered zone at all, so the loop cannot fall through in a run.
    """
    zones = set(metadata["geographicalZones"])
    covered = [ISO2_BY_ZONE[zone] for zone in ZONE_CODES.values() if zone in zones]
    if not covered:
        raise ValueError(f"{external_id}: geographicalZones {sorted(zones)} name no covered country")
    if len(covered) > 1:
        log.info(
            "euft_multi_country_notice",
            source_id=SOURCE_ID,
            external_id=external_id,
            countries=covered,
            chosen=covered[0],
        )
    return covered[0]


def published(metadata: dict, *, external_id: str) -> datetime:
    """`startDate`, which is a calendar day: midnight UTC on all 35 recorded notices.

    It equals `cftPublicationDateEForm`, which the API publishes as a bare date, on
    all 35, so the day is the whole of what is stated and no time of day is being
    discarded. Built as midnight UTC rather than passed through, because the
    `+0000` on this field is the same unreliable offset the deadline carries and
    there is no time of day for it to move.
    """
    return datetime.combine(timestamp(metadata, "startDate", external_id=external_id).date(), time.min, tzinfo=UTC)


def deadline(metadata: dict, *, external_id: str) -> datetime | None:
    """The closing date and time, read as local time in `cftTimezone`.

    None where the notice states no deadline, which is what a prior information
    notice is: 10 of the 35 recorded, all of them PINs. A missing deadline is
    visible on the candidate page; an invented one sends a reviewer to a closed
    tender (rule 10).
    """
    if not metadata.get("deadlineDate"):
        return None

    stated = timestamp(metadata, "deadlineDate", external_id=external_id)
    if stated.utcoffset().total_seconds() != 0:
        raise ValueError(
            f"{external_id}: deadlineDate {one(metadata, 'deadlineDate')!r} carries a real offset. "
            "This mapper reads the clock as local time in cftTimezone because the API stamps every "
            "deadline +0000 regardless of zone; if that has changed, read the instant and delete this rule."
        )

    zone = one(metadata, "cftTimezone").strip()
    try:
        local = ZoneInfo(zone)
    except ZoneInfoNotFoundError as cause:
        # The one permitted shape: re-raised naming the notice and the zone, and
        # swallowing nothing. A deadline this mapper cannot place is a changed
        # source, not a notice to store without one.
        raise ValueError(f"{external_id}: cftTimezone {zone!r} is not a known time zone") from cause
    return stated.replace(tzinfo=local)


def timestamp(metadata: dict, field: str, *, external_id: str) -> datetime:
    """One `2026-09-18T10:00:59.000+0000` value. Raises on anything else.

    Both callers read a date the reviewer acts on, so an unparseable value is a
    changed API rather than a field to skip.
    """
    raw = one(metadata, field)
    try:
        return datetime.strptime(raw, TIMESTAMP)
    except ValueError as cause:
        raise ValueError(f"{external_id}: {field} is {raw!r}, not {TIMESTAMP}") from cause


def cpv_codes(metadata: dict, *, external_id: str) -> list[str]:
    """Every CPV on the notice, the main one first.

    `mainCpvCode` is a JSON object naming one code and `mainCpv` is the unordered
    list of all of them, main code included on all 35 recorded notices - so this
    orders the list rather than adding to it. The order is what
    `monitor/filter/cpv.py` quotes when it drops a notice ("cpv 45000000 outside
    48/72/79"), and quoting an incidental code there would send a reviewer looking
    at the wrong classification.
    """
    main = (json.loads(one(metadata, "mainCpvCode")).get("mainCode") or "").strip()
    if not main:
        raise ValueError(f"{external_id}: mainCpvCode names no mainCode")

    codes = [main]
    codes.extend(code for code in metadata["mainCpv"] if code != main)
    return codes
