"""One achatspublics.sn tender row -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/senegal.json`, recorded 2026-09-13: the
one `GET https://api.achatspublics.sn/anon/tdo?size=200` response
`monitor/connectors/senegal.py` reads, `data.content`'s 35 rows. The connector
yields one `RawNotice` per row and the payload is that row's own JSON, verbatim -
one request, no per-notice detail fetch (see the connector's own module
docstring). So `raw` here is one tender dict, with the fields this module reads:

    uid                    "45824991-595e-4570-ae89-3c6e3abf61a3"   external id
    reference              "T_MDE_DSI_627"                          human reference, not used - see below
    libelle                "[Marché de test ] Rénovation ..."       the title
    description            null on all 35 rows in hand
    organization.libelle   "Ministère de la dématerialisation ..."  BUYER
    direction.libelle      "Direction des Systèmes d'information"   buyer sub-unit, not the buyer
    publicationDate        "2025-11-18T16:37:40.481+00:00"          PUBLICATION DATE
    submissionDate         "2025-11-18T17:00:00.000+00:00"          DEADLINE
    status                 "PUBLISHED"                              the only value measured; not read here

Four judgements, all measured on those 35 rows rather than assumed.

**`external_id` is `uid`, not `reference`.** `reference` ("T_MDE_DSI_627") is a
human-readable code built from the organisation and department, and it is what a
person reading the portal's own page would recognise; `uid` is what the API itself
treats as the record's identity - it is the value the confirmed per-record route
(`GET /anon/tdo/{uid}`) is keyed on, and it is what `NOTICE_URL` (imported from
`monitor/connectors/senegal.py`, not re-declared here per rule 6) is built from.
`external_id` is chosen to match whatever the source's own URL is built from, the
same relationship World Bank's `external_id` (its own `id`) and EBRD's
(`notice_id`) both have to their own `NOTICE_URL`. `reference` is not carried onto
`Notice` at all: there is no second identifier field to put it in, and Architecture
v0.4 appendix E's buyer-facing reference is a record-builder concern, not a
notice-mapping one.

**The timestamps need reshaping before `dates.py` will read them.**
`publicationDate` and `submissionDate` both publish
`YYYY-MM-DDTHH:MM:SS.sss+00:00` - milliseconds included - on all 70 values across
the 35 rows, and `monitor/normalise/dates.py`'s own `DATE_FORMATS` has no
millisecond form (it was built for TED's and Liberia's zoned-but-whole-second
timestamps). Checked directly: `parse_deadline("2025-11-18T17:00:00.000+00:00")`
returns `None` with a `deadline_unparsed` warning, while
`parse_deadline("2025-11-18T17:00:00+00:00")` - the same instant with the
milliseconds cut - parses cleanly. `_drop_millis` below does exactly that
reshaping, the same relationship `sierra_leone.py`'s `_iso` has to its own
source's day-month order: it turns one shape `dates.py` cannot read into the
unambiguous one it already does, so the one place that builds an aware UTC
datetime stays the only place that does it. A value that does not match the
measured shape is returned untouched, to fail loudly rather than being silently
misread.

**Both dates are required, and an unparseable one raises rather than becoming a
silent `None`.** Unlike Sierra Leone's ambiguous two-digit years, where a
genuine gap in the source is expected and `None` is the honest answer, both
`publicationDate` and `submissionDate` are present and well-formed on all 35 rows
in hand, and `monitor/connectors/senegal.py`'s own `publication_date` already
raises on a missing or unparseable `publicationDate` before this module ever sees
the row. This module holds `submissionDate` (the deadline) to the same standard
for the same reason: a missing or malformed value here means the endpoint changed
shape, not that a notice was published without a closing date, so it is reported
as a mapping failure with the row's `uid` attached rather than shown to a
reviewer as an opportunity with no deadline at all.

**The "[Marché de test]" rows are mapped, not filtered.** Nine of the 35 titles
in the fixture carry a test marker in one form or another - "[Marché de test ]
Rénovation du bâtiment des archives", "TEST Aménagement de bureaux pour les
services de sécurité", "(Marché TEST) Fourniture de denrées et produits
alimentaires" and six more - and one organisation, "Ministère de la
dématerialisation (TESTS APPEL)", exists only to publish them. Dropping them here
would be a normaliser deciding what is relevant, which is rule 5's finding
("A normaliser that also filters is a finding"); they map like any other row and
reach `notices` like any other row, and the lexicon and the scorer - which see the
word "test" in the title and score accordingly - are where that judgement
actually belongs.

**`cible` is recorded here, not carried.** `sources/senegal.yaml` measures this
field itself (29 "COMMUNITY", 6 "NATIONAL" of the 35 rows) and reads it as a
bidder-eligibility scope - a UEMOA/regional-community threshold on which
bidders may compete - rather than a statement about the buyer's administrative
level, and it explicitly leaves the question of what, if anything, to do with it
to whoever writes this module. Nothing here does anything with it: `Notice` has
no eligibility field to put it in (that vocabulary,
`EligibilityFlag`/`eligibility_flags`, belongs to the model's own `Score` and to
`Candidate`, populated at the scoring stage rule 5 reserves for that stage), so
`cible` is read by neither this module nor `admin_level` below. `admin_level`
stays the registry's own `national`, unchanged by `cible`, for the same reason
`monitor/normalise/mali.py` does not try to read a sub-national level off a buyer
name: nothing in the row is the evidence rule 5 would need to decide one.

`description`, `marketType.libelle`, `passationMode.libelle` and `direction.libelle`
are likewise not carried: `description` is null on every row in hand and `body`
falls back to empty exactly as it would if the field were carried and empty;
the other three name a procurement type, a bidding procedure and a buyer sub-unit
that `Notice` has no field for and that belong, if anywhere, to the scorer's own
`procurement_type` and to the record builder, not to this module.
"""

from __future__ import annotations

import re

import structlog

from monitor.connectors.senegal import NOTICE_URL
from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "senegal"
COUNTRY = "SN"
LANGUAGE = "fr"

# sources/senegal.yaml declares the language; no row carries one of its own to
# read, so this is asserted rather than detected - the same reasoning
# monitor/normalise/mali.py gives its own 1.0.
LANGUAGE_CONFIDENCE = 1.0

# See the module docstring: nothing on the row distinguishes a sub-national buyer,
# and `cible` is bidder eligibility, not buyer administrative level.
ADMIN_LEVEL = "national"

# "2025-11-18T16:37:40.481+00:00" -> ("2025-11-18T16:37:40", "+00:00"). Measured on
# all 70 timestamp values (publicationDate and submissionDate, 35 rows each).
TIMESTAMP_WITH_MILLIS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.\d+([+-]\d{2}:\d{2})$")


def _drop_millis(raw: str) -> str:
    """Cut the millisecond fraction dates.py's own DATE_FORMATS has no form for.

    Deliberately not a parse: it reorders a known shape into one
    `monitor/normalise/dates.py` already accepts, the same relationship
    `sierra_leone.py`'s `_iso` has to its own source. A value that does not match
    is returned untouched, to fail in the caller's own raise rather than here.
    """
    match = TIMESTAMP_WITH_MILLIS.match(raw)
    if not match:
        return raw
    return match.group(1) + match.group(2)


def _required_timestamp(raw: dict, field: str, *, uid: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"senegal row {uid} has no {field}")
    return value.strip()


def _published_at(raw: dict, *, uid: str):
    """`publicationDate`, required and always parseable per the module docstring."""
    value = _required_timestamp(raw, "publicationDate", uid=uid)
    parsed = parse_published(_drop_millis(value), source_id=SOURCE_ID, url=NOTICE_URL.format(uid=uid))
    if parsed is None:
        raise ValueError(f"senegal row {uid} has an unparseable publicationDate {value!r}")
    return parsed


def _deadline_at(raw: dict, *, uid: str):
    """`submissionDate`, required and always parseable per the module docstring."""
    value = _required_timestamp(raw, "submissionDate", uid=uid)
    parsed = parse_deadline(_drop_millis(value), source_id=SOURCE_ID, url=NOTICE_URL.format(uid=uid))
    if parsed is None:
        raise ValueError(f"senegal row {uid} has an unparseable submissionDate {value!r}")
    return parsed


def _buyer(raw: dict, *, uid: str) -> str:
    """`organization.libelle`, not `direction.libelle` - see the module docstring."""
    organization = raw.get("organization") or {}
    name = (organization.get("libelle") or "").strip()
    if not name:
        raise ValueError(f"senegal row {uid} has no organization.libelle")
    return name


def map_notice(raw: dict) -> MappedNotice:
    """One achatspublics.sn tender row to a Notice."""
    uid = (raw.get("uid") or "").strip()
    if not uid:
        raise ValueError("senegal row has no uid")

    title = (raw.get("libelle") or "").strip()
    if not title:
        raise ValueError(f"senegal row {uid} has no libelle")

    # null on all 35 rows in hand; see the module docstring. Read generically
    # rather than special-cased on that observation, so a future row that does
    # carry one is not silently dropped.
    body = (raw.get("description") or "").strip()

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=uid,
        url=NOTICE_URL.format(uid=uid),
        title=title,
        buyer=_buyer(raw, uid=uid),
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=_published_at(raw, uid=uid),
        deadline_at=_deadline_at(raw, uid=uid),
        language=LANGUAGE,
        language_confidence=LANGUAGE_CONFIDENCE,
        # estimated_value and value_currency are not passed: no value or currency
        # field exists anywhere on this source (sources/senegal.yaml's own
        # finding), so both are left at the model's own None/None default rather
        # than passed explicitly - the same choice monitor/normalise/mali.py makes
        # for the same reason.
        body=body,
    )
    # No English is published anywhere on this source; title_en and body_en stay
    # empty for step 14, per monitor/normalise/mapped.py.
    return MappedNotice(notice=notice)
