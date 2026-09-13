"""One SIGMAP dossier row -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/mali.json`, recorded 2026-09-13, and the
98 rows `monitor/connectors/mali.py` reads from
`GET https://marchespublics.ml/portail/api/sigmap/dossiers/dossier-sigmap`. The
connector yields one `RawNotice` per row and the payload is that row's own JSON,
verbatim - not a `{"listing": ..., "detail": ...}` pair the way Liberia's is,
because SIGMAP has no second, per-notice endpoint this connector reads (see the
connector's own module docstring). So `raw` here is one dossier dict:

    id          175527                      the connector's own record key
    dosNr       "0639/P-2026"               the tender's own published reference
    ctrName     "OFFICE DU NIGER"           contracting authority (buyer)
    dptName     "OFFICE DU NIGER"           issuing department
    dtpCode     "AAO" | "AMI"               notice type
    dosName     "..."                       the title
    dosStatus   1                           always 1 on this endpoint
    dosDate     "20260911"                  publication date, 8-digit YYYYMMDD

Five judgements, all measured on those 98 rows rather than assumed.

**The buyer is `ctrName`, not `dptName`.** The connector's own docstring labels
`ctrName` "contracting authority name (buyer)" and `dptName` "issuing department
name". They agree on 29 of the 98 rows and differ on the other 69 - e.g. `ctrName`
"MINISTERE DU TRAVAIL, DE LA FONCTION PUBLIQUE ET DU DIALOGUE SOCIAL" against
`dptName` "CABINET" - and where they differ, `dptName` is consistently the
narrower internal office within the authority named by `ctrName`, the same
buyer-versus-sub-unit shape `monitor/normalise/senegal.py` resolves by reading
`organization.libelle` and not `direction.libelle`. Both are institutional names on
every row; neither is ever a person (rule 19 has nothing to strip here because the
connector never fetched anything with contact data in it).

**`external_id` is `dosNr`, not `id`.** `id` is described by the connector's own
docstring as "this connector's own record key" - a row number in this connector's
own reading of the endpoint, not a number SIGMAP publishes to the public. `dosNr`
("0639/P-2026") is what the portal itself calls the tender and is what a person
matching this notice against a paper file or a CRM search would use. Both are
unique across all 98 rows in the fixture; `dosNr` is chosen because it is the
source's own identifier and not merely this connector's bookkeeping.

**`dosDate` needs no reshaping.** The brief for this module raised the question of
whether `monitor/normalise/dates.py` accepts a bare `YYYYMMDD` digit string, since
that is all `dosDate` ever is. Checked directly: `DATE_FORMATS` in `dates.py`
already carries `"%Y%m%d"` - added for TED's own bare-date-with-offset case - and
`parse_published("20260911")` returns `2026-09-11 00:00:00+00:00` without any help
from this module. So `dosDate` is passed to `parse_published` unchanged; the only
work this module does is confirm the string is 8 digits before handing it over, so
a differently-shaped value raises here with the dossier's own id attached rather
than surfacing as a bare warning from `dates.py` with nothing to trace it to.

**There is no deadline, no value, and no CPV code anywhere on this endpoint.**
`monitor/connectors/mali.py`'s own docstring confirms all three for the whole
98-row corpus: no field carries a closing date, no `montant`/`devise`/price field
of any kind exists (that data lives only on the separate award endpoints this
connector does not read), and no CPV or UNSPSC code appears in this platform's
JSON at all. `deadline_at` stays `None`, `estimated_value` and `value_currency`
stay `None` together (never a guessed pair), and `cpv_codes` stays the model's
own empty default - the free filter falls through to the lexicon for every
notice from this source, the same outcome Liberia's ISIC-only classification and
Sierra Leone's uncoded table already produce.

**`dtpCode` and `dosStatus` are read by the connector, not by this module.**
`monitor/connectors/mali.py` already raises before this module ever sees a row
whose `dtpCode` is outside `AAO`/`AMI` or whose `dosStatus` is not `1`, so nothing
here re-checks them - and nothing here uses `dtpCode` to decide what to keep
either way (rule 5: an AMI is a manifestation-interet notice and an AAO is a call
for tenders, and both are mapped identically; deciding which is worth a reviewer's
time is the lexicon and the scorer's job, not this one's). The `Notice` model has
no field for a source's own notice-type code, so `dtpCode` is not carried onto
the record at all - noted here so its absence reads as a decision and not a gap.

**There is no per-notice URL, so `Notice.url` is the shared listing page for every
notice.** `monitor/connectors/mali.py`'s own docstring explains why: the human
page for this listing is a single Angular route with no per-dossier deep link ever
observed, so `NOTICE_URL` there is one constant, not a template built from a row
field the way Liberia's or Senegal's per-notice URLs are. This module imports that
same constant rather than declaring a second copy of it (rule 6): the row itself
carries no URL of its own to read.

**Everything is national.** `sources/mali.yaml` declares `admin_level: national`
and nothing on the row states otherwise - `ctrName` runs from ministries
("MINISTERE DE L'EDUCATION NATIONALE") to national agencies and state enterprises
("OFFICE DU NIGER", "CAISSE MALIENNE DE SECURITE SOCIALE"), never a named
individual and never a sub-national government body the row itself identifies.
Guessing a regional or local level from an office name would be exactly the
keyword-driven judgement rule 5 leaves to a later stage that actually has the
evidence to make it; here there is none, so the registry's own declaration is what
is read.
"""

from __future__ import annotations

import structlog

from monitor.connectors.mali import NOTICE_URL
from monitor.models import Notice
from monitor.normalise.dates import parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "mali"
COUNTRY = "ML"
LANGUAGE = "fr"

# sources/mali.yaml declares the language; no row carries one of its own to read,
# so this is asserted rather than detected, the same reasoning liberia.py and
# sierra_leone.py give their own 1.0.
LANGUAGE_CONFIDENCE = 1.0

# See the module docstring: nothing on the row distinguishes a sub-national buyer.
ADMIN_LEVEL = "national"

# dosDate is always exactly 8 digits (YYYYMMDD) on every row measured; see the
# module docstring for why no reshaping is done before handing it to dates.py.
DOS_DATE_LENGTH = 8


def _published_at(raw: dict, *, dossier_id: str):
    """`dosDate`, checked and parsed. Raises rather than silently dropping the date.

    `dates.py` already reads a bare `YYYYMMDD` string; the check here is only that
    the value is one, so a differently-shaped or missing date raises with this
    dossier's own id attached rather than reaching `dates.py` as an anonymous
    warning.
    """
    value = raw.get("dosDate")
    if not isinstance(value, str) or len(value) != DOS_DATE_LENGTH or not value.isdigit():
        raise ValueError(f"mali dossier {dossier_id} has dosDate {value!r}, not an 8-digit YYYYMMDD string")

    published = parse_published(value, source_id=SOURCE_ID, url=NOTICE_URL)
    if published is None:
        # Reachable only for a calendar-shaped-but-impossible value (month 13, say):
        # the digit-and-length check above already rules out a wrong shape, and
        # every dosDate measured across the fixture parses. Mali always states one,
        # so a failure here is the endpoint changing, not a day with no date.
        raise ValueError(f"mali dossier {dossier_id} has an unparseable dosDate {value!r}")
    return published


def map_notice(raw: dict) -> MappedNotice:
    """One SIGMAP dossier row to a Notice."""
    dossier_id = str(raw.get("id", "unknown"))

    title = (raw.get("dosName") or "").strip()
    if not title:
        raise ValueError(f"mali dossier {dossier_id} has no dosName")

    buyer = (raw.get("ctrName") or "").strip()
    if not buyer:
        raise ValueError(f"mali dossier {dossier_id} has no ctrName")

    external_id = (raw.get("dosNr") or "").strip()
    if not external_id:
        raise ValueError(f"mali dossier {dossier_id} has no dosNr")

    notice = Notice(
        content_hash=content_hash(title, ""),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=NOTICE_URL,
        title=title,
        buyer=buyer,
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=_published_at(raw, dossier_id=dossier_id),
        # No deadline field on this endpoint at all; see the module docstring.
        deadline_at=None,
        language=LANGUAGE,
        language_confidence=LANGUAGE_CONFIDENCE,
        # No value or currency field anywhere on this endpoint; see the module
        # docstring. Left as the model's own None/None default rather than passed
        # explicitly, the same choice sierra_leone.py makes for the same reason.
        body="",
    )
    # No English is published anywhere on this source; title_en and body_en stay
    # empty for step 14, per monitor/normalise/mapped.py.
    return MappedNotice(notice=notice)
