"""DÖE notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/doe.json`, recorded 2026-09-12: one day's
listing and the 90 notices it selected. The request that produced it, and why it is
shaped that way, is in `monitor/connectors/doe.py`'s docstring.

The fixture holds two populations and almost every rule below exists because of
the split. 57 of the 90 notices come from the legacy feed (`eformsVersion:
eforms-sdk-0.1`, `dataSource: SERVICE_BUND_DE`, numeric ids) and 33 are eForms-DE
proper. The legacy half carries no declared language (53 of 90 across both
counts), no buyer address (53 of 90) and no classification block (21 of 90). It
does carry a title, a description, a buyer name and a deadline, which is why it is
read rather than skipped: those four are what the reviewer, the lexicon and the
deduper need.

Five things the recorded day settled.

1. **`admin_level` comes from the buyer's legal type, which names the tier of
   government in the code itself.** This is BUILD_ORDER step 13's design point for
   this connector: Germany's sub-national coverage without one source per Land.
   The eForms-DE `buyer-legal-type` list is not an arbitrary set of 21 values, it
   is a base code with a segment naming the tier - `-bund` federal, `-land`, and
   `-kommun` municipal, plus `bbeh` for Bundesbehörde and `lbeh` for
   Landesbehörde - so the level is read off the segment rather than from 21
   hand-written rows, and a 22nd code carrying a known segment maps correctly.
   Read off all 975 notices published on 2026-09-11: 523 declare a legal type
   across 21 distinct values, of which 260 are municipal, 118 are Land level and
   54 are federal. The other 452 declare none and take the registry's value.
   The seven EU-style codes in the list (`pub-undert`, `pub-undert-ra`,
   `pub-undert-la`, `pub-undert-cga`, `org-sub`, `grp-p-aut`, `def-cont`) are the
   same vocabulary `monitor/normalise/ted.py` already resolves, so they are
   delegated to it rather than copied: two tables for one code list would
   eventually disagree.

2. **The deadline is `deadlineReceiptTenders` where the procurement closes on
   tenders and `deadlineReceiptRequests` where it closes on requests to
   participate.** Those are two different deadlines and not two spellings of one
   (rule 1): a one-stage procedure has a closing date for offers, a two-stage
   procedure has a closing date for the first round. Checked across all 975
   notices of the day, they never co-occur - 581 carry only the tender date, 55
   only the request date, 20 neither - and on the 90 recorded notices the split is
   80 and 10 with every notice carrying one. Reading only the first would have
   handed the reviewer a blank deadline on every negotiated procedure, which is 55
   notices a day, and each of those blanks would have looked like a notice that
   simply had no date.

3. **`publicationDate` is written with microseconds** (`2026-09-11T02:04:04.556507Z`,
   on all 90), which `monitor/normalise/dates.py` has no format for, so it returns
   None and the notice loses its publication date. The fraction is dropped here
   before the shared parser runs; nothing else about the string is touched. The
   obvious alternative is wrong and was measured to be wrong:
   `noticePreferredPublicationDate` parses cleanly but is the date the buyer asked
   to be published on, and it disagrees with the actual publication day on 72 of
   the 90. A reviewer would have seen a plausible date that was several days out.

4. **The buyer is the first entry of `buyers`, resolved through
   `organisationReference`.** That resolved on all 90 and the resolved
   organisation carried the `buyer` role on all 90, so this is one route and not a
   choice between `buyers` and `organisationRoles`. One notice in the fixture is a
   joint procurement with nine buyers; the first is the `grp-p-aut` body running
   the procedure on the other eight's behalf, which is the buyer a reviewer needs.
   `organisationName` is non-empty on all 90, and it is proposed as text: no
   Account is resolved here or anywhere in the pipeline (CLAUDE.md, export target).

5. **The country and the language are the registry's, checked against the notice
   rather than read from it.** Only 37 of the 90 declare either. Where a country
   is declared it is DEU on all of them, and on all 579 of the day's 975 notices
   that declare a buyer country; where a language is declared it is a single DEU.
   So both are taken from `sources/doe.yaml` and the declared value becomes a
   guard: a notice declaring another country or another language is a change in
   what this source publishes and it raises, rather than being stored under a
   column that the geography weights and the lexicons would then believe.

No English rendering is published, so both `MappedNotice` English fields stay
empty and every notice reaches the step 14 translation stage (rule 9).
"""

from __future__ import annotations

import re
from decimal import Decimal

import structlog

from monitor.connectors.doe import notice_url
from monitor.models import Notice
from monitor.normalise.codes import country_alpha2, language_alpha2
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.ted import admin_level_for
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "doe"

# From sources/doe.yaml, which is the authority; the contract test asserts the
# three agree. See points 1 and 5 of the module docstring for why each is a
# constant here rather than a field read off the notice.
COUNTRY = "DE"
LANGUAGE = "de"
DEFAULT_ADMIN_LEVEL = "national"

# The segment of an eForms-DE `buyer-legal-type` code that names the tier of
# government. Source-specific vocabulary, like the World Bank's country spellings:
# nobody tunes these, they are what the German list says, and the day one changes
# this mapper must fail rather than be re-tuned (rule 6 is about keywords,
# thresholds, weights and placeholders, which these are not).
GERMAN_LEVEL_SEGMENT = {
    "bund": "national",  # koerp-oer-bund, anst-oer-bund, stift-oer-bund
    "bbeh": "national",  # Bundesbehörde: omu-bbeh, omu-bbeh-niedrig, oberst-bbeh
    "land": "regional",  # koerp-oer-land, anst-oer-land, stift-oer-land
    "lbeh": "regional",  # Landesbehörde: omu-lbeh, oberst-lbeh
    "kommun": "local",  # kommun-beh, koerp-oer-kommun, anst-oer-kommun
}

# The two closing dates, which never appear on the same notice. See point 2.
DEADLINE_FIELDS = ("deadlineReceiptTenders", "deadlineReceiptRequests")

# The fractional seconds on `publicationDate`. Anchored to follow a seconds field
# so it cannot match anything else in a timestamp. See point 3.
FRACTIONAL_SECONDS = re.compile(r"(?<=:\d{2})\.\d+")


def map_notice(raw: dict) -> MappedNotice:
    """One DÖE notice to a Notice. Raises on anything it cannot map."""
    notice_id = raw["noticeIdentifier"]
    version = raw["noticeVersion"]
    # The id is only unique with the version: one notice was published twice on
    # 2026-09-11, as a contract notice and as a correction 16 minutes later, and
    # the detail endpoint treats the pair as the key.
    external_id = f"{notice_id}-{version}"
    url = notice_url(notice_id)

    purpose = raw["purpose"]
    title = _single(purpose.get("title"), "purpose.title", external_id, required=True)
    body = _single(purpose.get("description"), "purpose.description", external_id, required=False)

    estimated_value, value_currency = published(raw)
    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        buyer=buyer_name(raw),
        country=country(raw),
        admin_level=admin_level(raw),
        published_at=published_at(raw, url=url),
        deadline_at=deadline(raw, url=url),
        language=language(raw),
        # The registry states the language and the notice's own declaration is
        # checked against it above, so there is nothing to be uncertain about.
        language_confidence=1.0,
        cpv_codes=cpv_codes(raw),
        estimated_value=estimated_value,
        value_currency=value_currency,
        body=body,
        status="detected",
    )
    # German only; no English rendering is published (rule 9). Empty means "no
    # English was published", never "the original was English"
    # (monitor/normalise/mapped.py), so every notice reaches step 14.
    return MappedNotice(notice=notice)


def buyer_organisation(raw: dict) -> dict:
    """The organisation the first buyer entry points at. See point 4."""
    buyers = raw["buyers"]
    if not buyers:
        raise ValueError(f"{raw['noticeIdentifier']}: notice has no buyers")

    reference = buyers[0].get("organisationReference")
    if not reference:
        raise ValueError(f"{raw['noticeIdentifier']}: first buyer has no organisationReference")

    for organisation in raw["organisation"]:
        if organisation.get("partyIdentification") == reference:
            return organisation

    raise ValueError(
        f"{raw['noticeIdentifier']}: buyer reference {reference!r} matches no organisation; "
        f"got {[o.get('partyIdentification') for o in raw['organisation']]}"
    )


def buyer_name(raw: dict) -> str:
    """The buyer as text. Non-empty on all 90 recorded notices, so absence raises."""
    name = (buyer_organisation(raw).get("organisationName") or "").strip()
    if not name:
        raise ValueError(f"{raw['noticeIdentifier']}: buyer organisation has no organisationName")
    return name


def country(raw: dict) -> str:
    """DE. The registry's country, with the notice's own declaration as a guard."""
    address = buyer_organisation(raw).get("address") or {}
    declared = (address.get("countryCode") or {}).get("value")
    if declared and country_alpha2(declared) != COUNTRY:
        raise ValueError(
            f"{raw['noticeIdentifier']}: buyer declares country {declared!r}, not {COUNTRY}; "
            "sources/doe.yaml says this source publishes German notices"
        )
    return COUNTRY


def language(raw: dict) -> str:
    """de. The registry's language, with the notice's own declaration as a guard.

    A notice declaring two official languages raises rather than one being picked:
    all 37 that declare one declare exactly one, and choosing between two would be
    choosing which language `title` and `body` are stored under.
    """
    declared = raw.get("noticeOfficialLanguages") or []
    if len(declared) > 1:
        raise ValueError(
            f"{raw['noticeIdentifier']}: notice declares {len(declared)} official languages "
            f"{[entry.get('value') for entry in declared]}; this mapper stores one original"
        )
    for entry in declared:
        if language_alpha2(entry["value"]) != LANGUAGE:
            raise ValueError(
                f"{raw['noticeIdentifier']}: notice declares language {entry['value']!r}, not {LANGUAGE}; "
                "sources/doe.yaml says this source publishes German notices"
            )
    return LANGUAGE


def admin_level(raw: dict) -> str:
    """The buyer's tier of government. See point 1.

    Absent is normal and takes the registry's value: 452 of the day's 975 notices
    declare no legal type, all of them from the legacy feed. Present but
    unrecognised is not normal and raises, because a level quietly read as federal
    is what BUILD_ORDER's one-connector-for-16-Länder design exists to avoid.
    """
    legal_type = (raw["buyers"][0].get("buyerLegalType") or {}).get("value")
    if not legal_type:
        return DEFAULT_ADMIN_LEVEL

    level = level_for(legal_type)
    if level is None:
        raise ValueError(
            f"{raw['noticeIdentifier']}: unknown buyer-legal-type {legal_type!r}; add its tier segment to "
            "GERMAN_LEVEL_SEGMENT in monitor/normalise/doe.py, or the base code to "
            "UNQUALIFIED in monitor/normalise/ted.py"
        )
    return level


def level_for(legal_type: str) -> str | None:
    """The tier one eForms-DE legal type implies, or None if the code is unknown."""
    for segment in legal_type.split("-"):
        if segment in GERMAN_LEVEL_SEGMENT:
            return GERMAN_LEVEL_SEGMENT[segment]
    # The EU-style codes in the German list, resolved by the module that already
    # owns that vocabulary rather than by a second copy of it.
    return admin_level_for(legal_type)


def published_at(raw: dict, *, url: str = ""):
    """When the service published the notice, to the second. See point 3."""
    return parse_published(without_fraction(raw["publicationDate"]), source_id=SOURCE_ID, url=url)


def without_fraction(timestamp: str) -> str:
    """`2026-09-11T02:04:04.556507Z` -> `2026-09-11T02:04:04Z`.

    Dropped rather than rounded: the fraction is below any precision a publication
    time is read at, and truncating cannot move the notice into another second.
    `monitor/normalise/dates.py` carries no fractional-second format and is not
    this module's to change.
    """
    return FRACTIONAL_SECONDS.sub("", timestamp)


def deadline(raw: dict, *, url: str = ""):
    """The earliest closing date over the notice's lots. See point 2.

    Earliest because that is the first one that binds: a reviewer needs the soonest
    date they could miss, not the last. None where the notice states none, which is
    20 of the day's 975 and none of the 90 recorded; a missing deadline is visible
    on the candidate page and an invented one sends a reviewer to a closed tender
    (rule 10). Parsed by rule from the original, never from a translation.
    """
    stated = [
        value
        for lot in raw["lots"]
        for field in DEADLINE_FIELDS
        if (value := (lot.get("submissionTerms") or {}).get(field))
    ]
    parsed = [
        value
        for value in (parse_deadline(entry, source_id=SOURCE_ID, url=url) for entry in stated)
        if value is not None
    ]
    return min(parsed) if parsed else None


def cpv_codes(raw: dict) -> list[str]:
    """The notice's CPV codes, from the notice block and every lot block.

    Both or neither: the 90 recorded notices split 69 with codes at both levels and
    21 with none anywhere, so reading both is not a second selector but the same
    codes seen twice, de-duplicated by `extract_codes`.

    `extract_codes` keeps the eight-digit form and refuses anything shorter, which
    is right and is why the connector's listing stage reads codes differently: 298
    of the day's 2,231 classification rows carry a bare two-digit division, and a
    division has no eight-digit form that is not invented. Those notices therefore
    reach `monitor/filter/cpv.py` with no codes, where absence is not a failed
    match and the lexicon stage gets its turn.
    """
    blocks = [raw.get("classification") or {}]
    blocks.extend(lot.get("classification") or {} for lot in raw["lots"])

    written: list[str] = []
    for block in blocks:
        written.append((block.get("mainClassificationCode") or {}).get("value") or "")
        written.extend((entry.get("value") or "") for entry in block.get("additionalClassificationCode") or [])
    return extract_codes(*written)


def published(raw: dict) -> tuple[Decimal | None, str | None]:
    """The stated value as published. Every one in the recorded day is EUR.

    8 of the 90 notices state a value at all, and they state it in EUR. It used to
    be dropped, because decision 6 had no rate to convert it with; migration 012
    gives the pipeline a stamped rate, so the euro figure is carried as a euro
    figure and converted at staging like every other source's.
    """
    stated = raw["purpose"].get("estimatedValue") or {}
    return published_value(stated.get("value"), stated.get("currencyID"), source_id=SOURCE_ID)


def _single(entries, field: str, external_id: str, *, required: bool) -> str:
    """The one value of a per-language text field, in the notice's own language.

    eForms keys these by language and the notice is German only, so there is
    exactly one entry on all 90 recorded notices for both the title and the
    description. Two entries would be a bilingual notice this rule has never seen,
    and picking one of them would be picking which language is stored (rule 9).
    """
    entries = entries or []
    if len(entries) > 1:
        raise ValueError(f"{external_id}: {field} has {len(entries)} language entries; this mapper stores one")

    value = (entries[0].get("value") if entries else "") or ""
    value = value.strip()
    if required and not value:
        raise ValueError(f"{external_id}: {field} is empty")
    return value
