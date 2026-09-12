"""BOAMP annonce -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/boamp.json`, recorded 2026-09-12 from the
414 annonces published on 2026-09-11. The request that produced it, and which of
BOAMP's four hosts it came from, is in `monitor/connectors/boamp.py`'s docstring.

**The fact that decides the shape of this module: one BOAMP flux carries four
different document formats, and 46% of a day is not the one a reader of the eForms
documentation would expect.** The census of 2026-09-11, by the single child element
of `DONNEES`:

    EFORMS     224   eForms UBL, the above-threshold JOUE notices
    FNSimple   165   the national simplified form
    MAPA        24   marché à procédure adaptée, below threshold
    DSP          1   délégation de service public

They share no field for the title, the description or the CPV code. A parser built
on the eForms block alone would read 224 of 414 files, report a healthy run and
silently lose every below-threshold French notice - which is the half of BOAMP
where a commune buying an accounting system actually appears.

So the title, the description and the CPV codes are read through a table keyed on
the format the document itself declares (`FORMATS` below). That is a lookup and not
a fallback chain (rule 1): nothing is tried and then retried elsewhere, the
document says which of the four it is and that row is the only one read. A format
BOAMP has not published before raises rather than mapping to an empty notice.

**What is uniform, and what it is not good enough for.** BOAMP wraps all four
formats in the same `GESTION` management block, and on 414 of 414 files it carries
`IDWEB`, `NOMORGANISME`, `DATE_PUBLICATION`, `DESCRIPTEURS`, `DEP_PUBLICATION` and
`RESUME_OBJET`. The first three are read here. `RESUME_OBJET` is deliberately not
the title: it is BOAMP's index summary and **it is truncated at 200 characters**,
ellipsis included - 8 of the 289 tenders recorded hit the cap, one of them ending
"... pour le compte du Crous de...". The format's own `intitule` / `Name` /
`objet` / `titreMarche` is the title the buyer wrote, and it is present on 289 of
289.

Six more things the recorded day settled.

 1. **The deadline is `DATE_LIMITE_REPONSE` in the management block**, which is the
    one place all four formats state it, and it is published in six different
    shapes. 281 of 414 files carry it; the 133 without it are every ATTRIBUTION,
    MODIFICATION and RECTIFICATIF plus 8 tenders that state no closing date.
    43 of the 281 carry a fractional second (`2026-09-29T12:00:00.000+02:00`),
    which `monitor/normalise/dates.py` cannot parse - it has no `%f` format - so
    those would silently become None and cost 15% of BOAMP's deadlines. The
    fraction is removed here before the string is handed over. Sub-second precision
    on a tender closing time is noise, and dropping it is exact rather than a guess
    (rule 10). The shared parser is the thing that should grow the format; this is
    the one source measured to need it.
 2. **119 of the 281 deadlines state no timezone at all**, and the rest state
    `+02:00` (122, Paris summer time), `+01:00` (21, Paris winter time used in
    September), `Z` (15), and `+03:00`, `+04:00` and `-03:00` once each. Whatever
    the publisher stamped is what is stored. `parse_deadline` reads a naive string
    as UTC; BOAMP's naive deadlines are Paris local, so those land at most two
    hours later than the hour published. The date changes for exactly one of the
    119 (a 23:00 close). The alternative is inventing a timezone for a string that
    does not state one, which is what rule 10 exists to forbid.
 3. **17 of the 289 tenders carry no description at all** - 11 FNSimple and 6 MAPA -
    and `Notice.body` is empty for them. The title is what the lexicon and the
    scorer then see. One of the 17 is "ASSISTANCE À LA GESTION DE LA TAXE LOCALE
    SUR LA PUBLICITÉ EXTÉRIEURE", so this is not a harmless gap; it is a gap in
    what BOAMP publishes, and inventing a body from the rendered HTML would put the
    buyer's staff names and contact details into a model call (rule 19).
 4. **MAPA carries no CPV code anywhere in its schema.** 0 of 19 recorded MAPA
    tenders have one, against 145 of 145 eForms and 117 of 124 FNSimple. Those
    notices reach the lexicon stage, which is where a French notice belongs
    anyway: `monitor/filter/cpv.py` treats an absent code as "not a failed match".
    Measured over the 289 tenders: 28 pass the 48/72/79 prefixes and 26 more carry
    no code at all, so about 54 of a day reach `config/lexicon_fr.yaml` and 235 are
    dropped for nothing.
 5. **The country is FR for every notice and it is not read from the document.**
    The two non-French country codes on the recorded day are a Belgian *supplier*
    inside an award notice and the EU Publications Office as a reference body -
    neither is a buyer. BOAMP is the French Republic's own bulletin.
 6. **The language is French and BOAMP does not state it in a form all four
    formats share.** The eForms third states `NoticeLanguageCode` FRA on 224 of
    224, and DILA's own rendering of every one of the 414 files declares
    `<html lang="FR">`. That is the publication's statement rather than a
    detection, so `language_confidence` is 1.0.

**`admin_level` is the registry's one value for the whole source, and that is a
compromise.** BOAMP states the buyer's level only in eForms, as
`buyer-legal-type`, and only on 218 of the 224 eForms files; FNSimple, MAPA and DSP
carry nothing equivalent on the other 190. Reading it from one format and guessing
it for the rest is the fallback rule 1 forbids, and there is no second signal to
use: the eForms notices from central-government buyers are single-département
publications as often as the communal ones, so `DEP_PUBLICATION` does not stand in
for it. One value is carried, and it is `local` because that is what the source
measurably is: of the 119 eForms notices that state a qualified level, 93 are local
authorities and 6 regional against 20 central-government ones, and France's
ministries publish on PLACE and TED rather than here. The cost is that the 9% which
are central-government arrive in the export as "Local/Regional"
(`config/record_defaults.yaml`). The reviewer sees the buyer's name and the notice
URL beside it.

**The published value is read at procedure level only, and only from eForms.**
Measured across all 584 payloads held in `storage/boamp/` on 2026-09-12: 323 are
eForms and the other 261 (FNSimple 218, MAPA 42, DSP 1) carry no value element in
their schema at all. `EstimatedOverallContractAmount`, always `currencyID="EUR"`,
occurs at two paths that are siblings rather than one nested inside the other -
`ContractNotice/ProcurementProject/RequestedTenderTotal/EstimatedOverallContractAmount`
for the procedure as a whole (123 occurrences, 19 of them zero for "not stated",
so 104 real totals) and
`ContractNotice/ProcurementProjectLot/ProcurementProject/RequestedTenderTotal/EstimatedOverallContractAmount`
for each lot (329 occurrences) - so a descendant-axis search finds both and the
result alone cannot tell them apart. Only the procedure path is read here. 24 of
the 323 eForms notices state a value on their lots and none at the procedure
level; summing those lots would be this module's own arithmetic, not the
published figure (rule 9), and it would be arithmetic the publisher itself does
not always agree with anyway - 12 notices state a procedure total that differs
from the sum of their lots (one states 245 000€ against three lots summing
204 166.68€) - so those 24 carry no value rather than a computed one. A separate
`FrameworkMaximumAmount` element, in the eForms extension namespace rather than
the base UBL one, exists at both levels too (48 procedure-level, 144 lot-level)
and is never read: it is a ceiling on a framework agreement, not an estimate of
the contract, and storing a ceiling as an estimate is exactly the kind of
plausible-looking wrong figure CLAUDE.md's export rules exist to keep out.
`estimated_value_usd` is still never set from here: the figure carried is EUR,
the column is USD, and turning one into the other needs a dated rate, which is
what staging applies (migration 012) to every source that states a value, this
one now included.

`DESCRIPTEURS` (BOAMP's own 375-term subject vocabulary) and `ANNONCE_ANTERIEUR`
(the annonce a correction corrects) are both read by nothing here: `Notice` has no
column for either. They are noted so the step that adds one does not go looking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import structlog

from monitor.connectors.boamp import (
    NOTICE_URL,
    notice_idweb,
    notice_nature,
    parse_document,
    sole_child,
)
from monitor.models import Notice
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "boamp"

# All three from sources/boamp.yaml, which is the authority; the contract test
# asserts the two agree. See the docstring for why `admin_level` is one value for
# the source rather than a level read per notice.
COUNTRY = "FR"
ADMIN_LEVEL = "local"
LANGUAGE = "fr"

# The eForms namespaces as BOAMP writes them. They are the UBL ones rebased on
# `urn:boamp:`, not the `urn:oasis:` originals, which is why they are spelled out
# here instead of being remembered from the standard.
CBC = "{urn:boamp:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
CAC = "{urn:boamp:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"

# The management block, shared by all four formats.
FORMAT_BLOCK = "DONNEES"
BUYER = "GESTION/INDEXATION/NOMORGANISME"
PUBLISHED = "GESTION/INDEXATION/DATE_PUBLICATION"
DEADLINE = "GESTION/INDEXATION/DATE_LIMITE_REPONSE"

# A fractional second on an ISO timestamp. `monitor/normalise/dates.py` has no
# format with `%f`, and 43 of the 281 recorded deadlines carry one.
FRACTION = re.compile(r"(?<=:\d\d)\.\d+")


@dataclass(frozen=True)
class FormatPaths:
    """Where one BOAMP document format puts the three fields that are not shared.

    `document` maps a notice's NATURE to the child of the format element that holds
    it. eForms names that child after the notice kind - `ContractNotice` for an
    APPEL_OFFRE, `PriorInformationNotice` for a PRE-INFORMATION - and the three
    national formats call it `initial` whatever the nature. A mapping and not a
    single name because the first live fetch found a nature the recorded day did not
    contain, and a single name could only have been made to fit it by trying one
    element and then another, which is the fallback rule 1 forbids.

    A (format, nature) pair that is not here raises. That is the same choice as an
    unknown format: reading a prior information notice through a contract notice's
    paths would produce an empty notice and report a healthy run.

    `cpv` is a tuple because a notice classifies its main object and its lots'
    additional objects in separate elements, the way TED splits
    `classification-cpv` across fields. It is empty for MAPA, which has no CPV
    element in its schema at all.

    `value` is the procedure-level path to the published contract value. It is
    None for the three national formats, which have no value element in their
    schema at all - recorded once here rather than discovered per notice - and,
    for EFORMS, it is deliberately the procedure-level path only: see the module
    docstring for why the lot-level path beside it is never read or summed.
    """

    document: dict[str, str]
    title: str
    description: str
    cpv: tuple[str, ...]
    value: str | None


# Keyed on the single child element of `DONNEES`, which is the format the document
# declares itself to be. Measured on all 414 files of 2026-09-11; the title path is
# present on 289 of 289 tenders in every format.
#
# The two national CPV paths are written on the leaf pair rather than on their
# containers (`codeCPV` in FNSimple, `CPV` in DSP) because `classPrincipale` occurs
# under `objetPrincipal` and `objetComplementaire` and nowhere else in either
# schema: 354 occurrences on the recorded day, all of them under those two.
FORMATS = {
    "EFORMS": FormatPaths(
        # PRE-INFORMATION was not in the 414 files of 2026-09-11 and the first live
        # fetch hit two of them on 2026-09-10, where the run stopped rather than
        # mapping one wrongly (rule 4). It is a pre-tender signal, the earliest
        # warning that a procurement is coming, and it is worth having: the two seen
        # were a vehicle-hire framework and an AMO advisory sourcing notice. It
        # states no DATE_LIMITE_REPONSE, because there is nothing to close yet, and
        # the title, description and CPV paths below resolve on it unchanged.
        document={"APPEL_OFFRE": "ContractNotice", "PRE-INFORMATION": "PriorInformationNotice"},
        title=f"{CAC}ProcurementProject/{CBC}Name",
        description=f"{CAC}ProcurementProject/{CBC}Description",
        # One path: in eForms the main classification and every lot's additional
        # ones are the same element type, so `.//` reads all of them at once.
        cpv=(f".//{CBC}ItemClassificationCode",),
        # The procedure-level total only. Its sibling under each
        # `ProcurementProjectLot` is the same element name one level down and is
        # deliberately not read here or anywhere else in this module - see the
        # module docstring for the 24 notices that state only a lot value and the
        # 12 where the procedure total disagrees with the lots' sum.
        value=f"{CAC}ProcurementProject/{CAC}RequestedTenderTotal/{CBC}EstimatedOverallContractAmount",
    ),
    "FNSimple": FormatPaths(
        # The national formats put every nature in `initial`. No PRE-INFORMATION has
        # been seen in one; if one appears it raises here rather than being assumed
        # to follow the same rule.
        document={"APPEL_OFFRE": "initial"},
        title="natureMarche/intitule",
        description="natureMarche/description",
        cpv=(".//objetPrincipal/classPrincipale", ".//objetComplementaire/classPrincipale"),
        # No value element anywhere in the schema; see the module docstring.
        value=None,
    ),
    "MAPA": FormatPaths(
        # The national formats put every nature in `initial`. No PRE-INFORMATION has
        # been seen in one; if one appears it raises here rather than being assumed
        # to follow the same rule.
        document={"APPEL_OFFRE": "initial"},
        title="description/objet",
        description="caracteristiques/principales",
        cpv=(),
        # No value element anywhere in the schema; see the module docstring.
        value=None,
    ),
    "DSP": FormatPaths(
        # The national formats put every nature in `initial`. No PRE-INFORMATION has
        # been seen in one; if one appears it raises here rather than being assumed
        # to follow the same rule.
        document={"APPEL_OFFRE": "initial"},
        title="descriptionMarche/titreMarche",
        description="descriptionMarche/description",
        cpv=(".//objetPrincipal/classPrincipale", ".//objetComplementaire/classPrincipale"),
        # No value element anywhere in the schema; see the module docstring.
        value=None,
    ),
}


def map_notice(raw: dict) -> MappedNotice:
    """One BOAMP annonce to a Notice. Raises on anything it cannot map.

    `raw` is the envelope `monitor/connectors/boamp.py` stored: the day directory
    the file came from and the XML as served.
    """
    day = date.fromisoformat(raw["day"])
    reference = f"published {raw['day']}"
    root = parse_document(raw["xml"], reference=reference)
    idweb = notice_idweb(root, reference=reference)
    url = NOTICE_URL.format(idweb=idweb)

    paths = format_paths(root, reference=idweb)
    document = document_element(root, paths, reference=idweb)

    title = text_of(document, paths.title)
    if not title:
        raise ValueError(f"{idweb}: {paths.title} is empty; a notice with no title cannot be mapped")
    body = text_of(document, paths.description)
    value, value_currency = procedure_value(document, paths)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=idweb,
        url=url,
        title=title,
        # Present and non-empty on all 414 recorded files. This is the buyer the
        # record builder proposes as text; no Account is resolved here or anywhere
        # in the pipeline (CLAUDE.md, export target).
        buyer=text_of(root, BUYER),
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=published_at(root, day=day, idweb=idweb, url=url),
        deadline_at=deadline(root, idweb=idweb, url=url),
        language=LANGUAGE,
        # BOAMP publishes in French by statute and says so per notice; see point 6
        # of the module docstring. Not a detection, so there is nothing uncertain.
        language_confidence=1.0,
        cpv_codes=cpv_codes(document, paths),
        # Procedure level only, in EUR, never converted or summed from lots; see
        # the module docstring.
        estimated_value=value,
        value_currency=value_currency,
        body=body,
        status="detected",
    )
    # BOAMP publishes no English rendering of anything. Empty means "no English was
    # published" (monitor/normalise/mapped.py), so every notice from here reaches
    # the step 14 translation stage - unless the French lexicon has already decided
    # it, which is what makes this source cheap.
    return MappedNotice(notice=notice)


def format_paths(root, *, reference: str) -> FormatPaths:
    """The row of `FORMATS` for the format this document declares itself to be.

    BOAMP names the format as the single child element of `DONNEES`. An unknown one
    raises: mapping a fifth format through a fourth format's paths would produce an
    empty notice and report a healthy run.
    """
    declared = sole_child(root.find(FORMAT_BLOCK), path=FORMAT_BLOCK, reference=reference)
    if declared not in FORMATS:
        raise ValueError(
            f"{reference}: unknown BOAMP document format {declared!r}; "
            f"add it to FORMATS in monitor/normalise/boamp.py (known: {sorted(FORMATS)})"
        )
    return FORMATS[declared]


def document_element(root, paths: FormatPaths, *, reference: str):
    """The element inside the format block that holds this notice.

    Two ways this raises, and both are things nothing downstream could detect.

    A nature this format has no element name for: the pipeline has met a kind of
    notice nobody has mapped, and guessing which element holds it is how an empty
    notice gets reported as a healthy run.

    A named element that is not there: the nature in the management block and the
    document in the data block disagree - an `APPEL_OFFRE` whose body is an
    `attribution`. The two agreed on all 414 files of the recorded day.
    """
    nature = notice_nature(root, reference=reference)
    name = paths.document.get(nature)
    if name is None:
        raise ValueError(
            f"{reference}: no document element is mapped for nature {nature!r}; "
            f"add it to FORMATS in monitor/normalise/boamp.py (known: {sorted(paths.document)})"
        )

    element = root.find(f"{FORMAT_BLOCK}/*/{name}")
    if element is None:
        raise ValueError(f"{reference}: nature is {nature} but the document has no {name!r} element")
    return element


def text_of(element, path: str) -> str:
    """One element's text, ends trimmed, or empty where the path is absent.

    Only the ends are trimmed. The newlines inside a BOAMP description are the
    buyer's own paragraphs and bullet lists, not the serialiser's indentation -
    checked across the recorded day - so collapsing them would rewrite what was
    published (rule 9).
    """
    return (element.findtext(path) or "").strip()


def cpv_codes(document, paths: FormatPaths) -> list[str]:
    """Every CPV code the notice classifies itself under, main object and lots.

    Empty for MAPA, which has no CPV element, and for the notices in the other
    formats whose classification element is present but empty (3 of the recorded
    day's `objetPrincipal` containers). `monitor/filter/cpv.py` reads an absent code
    as "not a failed match", so those notices go on to the lexicon.
    """
    values = [element.text or "" for path in paths.cpv for element in document.findall(path)]
    return extract_codes(*values)


def procedure_value(document, paths: FormatPaths) -> tuple[Decimal | None, str | None]:
    """The procedure-level published value, or (None, None) where none is stated.

    None for the three national formats (`paths.value` is None for them). None
    also for a eForms notice that states a value only on its lots and not at the
    procedure level - 24 of the 323 recorded - because summing the lots would be
    this module's own arithmetic, not the published figure (rule 9); see the
    module docstring for the 12 notices where the publisher's own total disagrees
    with that sum anyway. `published_value` turns a stated zero into None too.
    """
    if paths.value is None:
        return None, None
    element = document.find(paths.value)
    if element is None:
        return None, None
    return published_value(element.text, element.get("currencyID"), source_id=SOURCE_ID)


def published_at(root, *, day: date, idweb: str, url: str) -> datetime:
    """`DATE_PUBLICATION`, checked against the day directory the file came from.

    They agreed on all 414 files of 2026-09-11, and the agreement is what makes the
    window a path rather than a date filter (`monitor/connectors/boamp.py`, fact 1).
    A disagreement means the flux has been reorganised - notices backfilled into a
    day that is not their own - and the window would then be reading the wrong days
    while looking perfectly healthy.
    """
    stated = text_of(root, PUBLISHED)
    published = parse_published(stated, source_id=SOURCE_ID, url=url)
    if published is None:
        raise ValueError(f"{idweb}: {PUBLISHED} is {stated!r}, which is not a date")
    if published.date() != day:
        raise ValueError(
            f"{idweb}: {PUBLISHED} is {published.date().isoformat()} but the file was published "
            f"in the {day.isoformat()} directory; the flux layout changed"
        )
    return published


def deadline(root, *, idweb: str, url: str) -> datetime | None:
    """The closing date and time as BOAMP published them, or None where it states none.

    None on 133 of the 414 recorded files: every award, modification and correction,
    and 8 tenders that state no closing date. A missing deadline is visible on the
    candidate page; an invented one sends a reviewer to a closed tender (rule 10).
    """
    stated = text_of(root, DEADLINE)
    if not stated:
        return None
    return parse_deadline(without_fraction(stated), source_id=SOURCE_ID, url=url)


def without_fraction(value: str) -> str:
    """`2026-09-29T12:00:00.000+02:00` -> `2026-09-29T12:00:00+02:00`.

    43 of the 281 recorded deadlines carry a fractional second and
    `monitor/normalise/dates.py` has no format that accepts one, so without this
    they would parse to None and be logged as unreadable. Removing a sub-second
    component from a tender closing time changes nothing a bidder can act on.
    """
    return FRACTION.sub("", value)
