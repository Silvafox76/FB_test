"""simap publication -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/simap.json`, recorded 2026-09-12: 26
publications over seven days, plus the 4,966-row office directory. The request
that produced it, and why it is shaped that way, is in
`monitor/connectors/simap.py`'s docstring.

The payload this reads is the three documents the connector fetched, kept apart
under `search`, `detail` and `procOffice`, because simap splits one notice across
three endpoints and none of them is optional.

Six things the recorded week settled.

1. **The notice's own language is `detail.base.creationLanguage`.** simap's text
   fields are objects keyed `de`, `en`, `fr`, `it` with nulls for the languages
   not published, and more than one is filled on 17 of the 26 - so "which key has
   a value" does not answer the question. `base.translationLanguages` names which
   of the filled ones are renderings, with a `type` of `summary` or `complete`.
   On all 26, `title[creationLanguage]`, `procOfficeAddress.name[creationLanguage]`
   and `orderDescription[creationLanguage]` are present, so the original is read
   strictly from that one key and a missing one raises (rule 9).
   `project-info.publicationLanguages` looks like the same field and is a trap: it
   is null on every publication type except `tender` and `advance_notice`.
   Distribution: 20 German, 6 French, no Italian and no English original. Romansh
   is a Swiss official language and is not a simap publication language; a
   `creationLanguage` outside the four raises rather than reaching a lexicon that
   does not exist.

2. **The buyer's level of government is not in the publication.** The publication
   carries the office's name and postal address; only
   `/api/procoffices/v1/po/public` states its type, which is why the connector
   fetches that directory and stores the one matching row with each notice. Eight
   values across three levels plus `foreign`, all 4,966 offices on 2026-09-12:
   145 federal, 1,428 cantonal, 3,375 communal, 18 foreign. The recorded week used
   seven of the eight. This is the simap analogue of TED's eForms buyer legal type,
   and the reason `sources/simap.yaml`'s `admin_level` is the platform's level and
   not any notice's.

3. **No simap publication states a contract value.** 176 distinct keys across the
   26 recorded payloads, and the only `price` and `currency` in any of them are
   under `documentsCosts`, the price of the tender documents. So
   `estimated_value_usd` is never set from here and the scorer's own estimate at
   step 6 is the only one there is.

4. **The deadline needs no timezone invented for it.** `dates.offerDeadline` is a
   full ISO timestamp with the correct Zurich offset already applied -
   `2026-10-16T15:00:00+02:00` in summer time, `2026-10-30T23:59:00+01:00` in
   winter, 18 and 7 of the 25 recorded deadlines. The platform's own terms (AGB
   clause 4.1) say every time it records or displays is Europe/Zurich and that a
   deviating client timezone is ignored, and the API's offsets match that. So the
   rule is: parse it, keep the offset, convert nothing (rule 10). The one notice
   of 26 with no deadline is the `advance_notice`, which announces an intention to
   procure and has nothing to close.

5. **`dates` is absent entirely on the publication types the registry excludes**
   (`direct_award`, `award`, `abandonment`, `revocation`, `participant_selection`
   all carry `dates: null`), so the deadline lookup is written for a block that may
   not be there rather than for a field that may be null.

6. **`base.publicationDate` is a calendar day with no time.** Stored as its UTC
   midnight, which is 01:00 or 02:00 the same morning in Zurich, so the day the
   platform published survives the representation. Switzerland is east of UTC in
   both halves of the year, so this cannot render as the day before; nothing here
   needs the tz database and no dependency is added for it.

What this module deliberately does not do:

  - **It sets no `title_en` or `body_en`, although simap supplies them for 7 of
    the 26.** `monitor/fetch.py` stamps a source-supplied English rendering with
    `SOURCE_NATIVE_MODEL = "ted-eforms"`, which is TED's provenance and would be
    a false one on a simap notice. Rule 9 makes the stamp part of the record, and
    a wrong stamp is worse than a missing rendering: step 14 will produce a
    correctly stamped one for the price of a Haiku call. Wiring simap's own
    translations means a per-source provenance stamp in `fetch.py`, which is the
    main session's file and not this one's.
  - **It carries no body from the lots.** 5 of the 26 are multi-lot - one with 13
    lots and four with 2 - and on 2 of them the lot descriptions hold more text
    than the project description does; one, 42058-02, has a project description of
    exactly "siehe
    Ausschreibungsunterlagen" and 646 characters spread over 13 lots. The body is
    still the project-level `orderDescription` alone, because that is the one field
    that corresponds to TED's `description-proc` and the World Bank's
    `notice_text`, and because a body stitched together here would change its
    `content_hash` every time a publisher edited one lot. The lots' CPV codes *are*
    carried, which is the part that changes whether a notice survives step 5. If
    recall on multi-lot frameworks turns out short, lot text is the first thing to
    add and this is the measurement to add it against.

`detail.ted` is worth a note for the deduper and nothing here acts on it: 8 of the
26 recorded notices carry `{"status": "published", "url": "https://ted.europa.eu/..."}`,
the platform naming the exact TED notice the same tender was published as. TED is
step 4's source, so a third of simap's IT notices will arrive twice, and the
`notices` table has no column for a cross-reference that would let the deduper
join them by id instead of by title.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time

import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.simap import NOTICE_URL, cpv_code_strings
from monitor.models import Notice
from monitor.normalise.codes import country_alpha2
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "simap"

# The four languages simap publishes in, which are the four keys on every
# multilingual object in the payload. A `creationLanguage` outside this set is a
# notice whose text cannot be read in the language the record claims, and it would
# be filtered against a lexicon that does not exist (config/ has lexicon_en and
# lexicon_fr), so it raises. These are already ISO 639-1, which is what the
# lexicons and the geography weights key on, so nothing is converted.
PUBLICATION_LANGUAGES = frozenset({"de", "en", "fr", "it"})

# The public office directory's `type` to the buyer's level of government. All
# eight values, counted across all 4,966 offices on 2026-09-12.
#
# `foreign` (18 offices: Deutsche Bahn AG, Aeroport de Bale-Mulhouse, Swisscontact,
# SECO's overseas purchasing) is the one that does not fit the vocabulary, because
# it is not a level of the Swiss state at all. It is read as national: these are
# autonomous or cross-border undertakings and never a canton or a commune, so
# national is the true half of the statement and regional or local would be the
# false one. The alternative is to raise, which would fail a whole run over a value
# the platform legitimately publishes; `country` is read from the office's own
# address either way, so a Deutsche Bahn notice arrives as DE and not as CH.
# None of the 18 published a CPV-matching notice in the recorded week.
ADMIN_LEVEL_BY_OFFICE_TYPE = {
    "central_federation": "national",  # 61, the central federal administration
    "decentral_federation": "national",  # 58, SBB, EPFL and the like
    "other_federation": "national",  # 26, other bearers of federal tasks
    "cantonal": "regional",  # 738
    "other_cantonal": "regional",  # 690, cantonal transport and utility companies
    "communal": "local",  # 2,478
    "other_communal": "local",  # 897, communal associations and foundations
    "foreign": "national",  # 18, see above
}

# `dates.offerDeadline` is the closing date for offers, and for a
# `request_for_information` the closing date for responses. Read on its own and not
# alongside `expressionOfInterestUntil`, which is a different and earlier date: on
# the one recorded notice that states both, interest closes 2026-09-25 and the
# response is due 2026-10-02, so treating them as two spellings of one thing would
# take a week of runway off the reviewer's screen.
DEADLINE_FIELD = "offerDeadline"

# Runs of blank lines left where the description's layout paragraphs were.
BLANK_LINES = re.compile(r"\n{3,}")


def map_notice(raw: dict) -> MappedNotice:
    """One simap publication to a Notice. Raises on anything unmappable."""
    for part in ("search", "detail", "procOffice"):
        if part not in raw:
            raise ValueError(f"simap payload is missing {part!r}; got {sorted(raw)}")

    detail = raw["detail"]
    base = detail["base"]
    project_info = detail["project-info"]

    external_id = base["publicationNumber"]
    language = notice_language(base, external_id)
    url = NOTICE_URL.format(lang=language, project_id=base["projectId"])

    title = original(base["title"], language, field="base.title", external_id=external_id)
    body = strip_html(description(detail, language))
    office = buyer_office(project_info, external_id)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        # The buyer as the notice names it, in the notice's own language. This is
        # what the record builder proposes as text; no Account is resolved here or
        # anywhere in the pipeline (CLAUDE.md, export target).
        buyer=original(office["name"], language, field="procOfficeAddress.name", external_id=external_id),
        # The buyer's own country, from the buyer's own address. Not
        # `orderAddress.countryId`, which is the place of performance and said FR on
        # 2 of the 151 notices measured over six weeks: a Swiss buyer procuring work
        # on French soil is a Swiss opportunity.
        country=country_alpha2(office["countryId"]),
        admin_level=admin_level(raw["procOffice"], external_id),
        published_at=published_at(base, external_id),
        deadline_at=deadline(detail, url=url),
        language=language,
        # The notice states its own original language rather than it being detected.
        language_confidence=1.0,
        cpv_codes=extract_codes(*cpv_code_strings(detail)),
        # No simap publication states a contract value. See point 3 of the docstring.
        body=body,
        status="detected",
    )
    # No `title_en` or `body_en`, although simap supplies them on 7 of the 26
    # recorded notices. `monitor/fetch.py` would stamp them `model="ted-eforms"`.
    # See the docstring.
    return MappedNotice(notice=notice)


def notice_language(base: dict, external_id: str) -> str:
    """`base.creationLanguage`: the language the notice was written in, not translated into."""
    language = (base.get("creationLanguage") or "").strip().lower()
    if language not in PUBLICATION_LANGUAGES:
        raise ValueError(
            f"{external_id}: creationLanguage is {base.get('creationLanguage')!r}, not one of "
            f"{sorted(PUBLICATION_LANGUAGES)}; add it to PUBLICATION_LANGUAGES in "
            "monitor/normalise/simap.py once a lexicon for it exists"
        )
    return language


def original(values: dict, language: str, *, field: str, external_id: str) -> str:
    """One multilingual field in the notice's own language. Raises if it is not there.

    Substituting a translated key would store, say, the French rendering on a notice
    whose `language` column says `de`, and the lexicon, the deduper and the reviewer
    would all believe the column. Present on all 26 recorded notices for all three
    fields this reads.
    """
    value = (values or {}).get(language)
    if not value or not value.strip():
        filled = sorted(key for key, text in (values or {}).items() if text)
        raise ValueError(
            f"{external_id}: {field} has no text in the notice's own language {language!r}; filled: {filled}"
        )
    return value.strip()


def description(detail: dict, language: str) -> str:
    """The project's own description of what it is buying, as published.

    Empty where the publication carries no `procurement` block at all, which is
    every `abandonment` in the recorded sample; those types are excluded at the
    query, so this is the honest reading of an absent block and not a case the
    pipeline is expected to see. `Notice.body` allows empty and the title carries
    the subject.
    """
    procurement = detail.get("procurement") or {}
    return ((procurement.get("orderDescription") or {}).get(language) or "").strip()


def buyer_office(project_info: dict, external_id: str) -> dict:
    """`project-info.procOfficeAddress`: the buying office's name and address.

    Present on all 26 recorded notices and on every publication type sampled,
    including the ones the registry excludes. Its absence would leave the notice
    with no buyer and no country, so it raises rather than mapping to blanks.
    """
    office = project_info.get("procOfficeAddress")
    if not office or not office.get("countryId"):
        raise ValueError(f"{external_id}: project-info.procOfficeAddress has no countryId; got {office!r}")
    return office


def admin_level(office: dict, external_id: str) -> str:
    """The buyer's level of government, from the public office directory's `type`."""
    office_type = office.get("type")
    if office_type not in ADMIN_LEVEL_BY_OFFICE_TYPE:
        raise ValueError(
            f"{external_id}: office {office.get('id', '?')} has type {office_type!r}; add it to "
            "ADMIN_LEVEL_BY_OFFICE_TYPE in monitor/normalise/simap.py"
        )
    return ADMIN_LEVEL_BY_OFFICE_TYPE[office_type]


def published_at(base: dict, external_id: str) -> datetime:
    """`base.publicationDate`, a calendar day, as its UTC midnight.

    This publication's date, not `initialPublicationDate`: 6 of the 26 recorded
    notices are corrections and each republishes the whole notice under a new
    publication number, so the record is the correction and its own date is when it
    was published. The superseded one is named in `base.correctedPubId`.
    """
    raw = (base.get("publicationDate") or "").strip()
    try:
        day = date.fromisoformat(raw)
    except ValueError as cause:
        raise ValueError(f"{external_id}: publicationDate is {raw!r}, not yyyy-mm-dd: {cause}") from cause
    return datetime.combine(day, time.min, tzinfo=UTC)


def deadline(detail: dict, *, url: str = "") -> datetime | None:
    """The closing date, as published, with simap's own Zurich offset kept.

    None where the publication states none: `dates` is absent on the decided types
    and `offerDeadline` is null on an `advance_notice`, which announces an intention
    to procure. A missing deadline is visible on the candidate page; an invented one
    sends a reviewer to a closed tender (rule 10).
    """
    dates = detail.get("dates") or {}
    return parse_deadline(dates.get(DEADLINE_FIELD) or "", source_id=SOURCE_ID, url=url)


def strip_html(markup: str) -> str:
    """The description as text, in the notice's own language (rule 9).

    Every one of the 26 recorded descriptions is HTML: `<p>`, `<ul>` and `<br>` from
    the publisher's rich-text editor. Non-breaking spaces become ordinary ones,
    because they are layout rather than the publisher's prose and a phrase written
    with one in the middle would not match the lexicon, which matches whole phrases
    with ordinary spaces in them.
    """
    if not markup:
        return ""
    text = HTMLParser(markup).text(separator="\n").replace("\xa0", " ")
    return BLANK_LINES.sub("\n\n", text).strip()
