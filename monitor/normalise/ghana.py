"""One GHANEPS Current Tenders row -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/ghana.html` and `ghana_page2.html`,
recorded 2026-09-13, and the 20 rows `monitor/connectors/ghana.py`'s own
`parse_listing_page` reads from them (10 per page; `tests/contract/test_ghana.py`
locks that count). The connector yields one `RawNotice` per row and the payload
is that row's own dict, verbatim - one listing, no per-notice detail fetch (the
connector's own docstring: it never fetches the notice PDF linked from each row,
only the listing). So `raw` here is one row dict with the fields this module
reads (keys exactly as `test_one_whole_pass_yields_the_measured_count_and_stops_at_two_pages`
asserts them):

    resource_id        "3403640"                                   the connector's own resourceId, already
                                                                     cross-checked against both its links on the row
    title              "Construction of Akim Oda Branch..."         the title
    procuring_entity   "Social Security And National..."            buyer
    description        "Construction of Akim Oda Branch....."       often repeats the title; see below
    deadline           "Fri Oct 02 10:00:00 GMT 2026"                Java Date.toString(), always GMT
    procedure          "National Competitive Tendering"              free text, no code; not carried (see below)
    status             "Bid Submission"                              the connector's own EXPECTED_STATUS on every row
    notice_pdf_url     "https://www.ghaneps.gov.gh/epps/cft/..."     the per-notice link a reviewer opens
    publication_date   "Fri Sep 11 15:58:07 GMT 2026"                 Java Date.toString(), always GMT

Four judgements, all measured on those 20 rows rather than assumed.

**The description is dropped when it only repeats the title, the same judgement
`monitor/normalise/liberia.py` makes for its own source - but measured fresh
here, because GHANEPS's own template differs from Liberia's ePPS in one small
way.** Liberia's `tender.description` either equals `tender.title`
byte-for-byte or is a longer, different sentence, so a plain `==` after
stripping is enough there. GHANEPS's "Info" tooltip does the same exact-match
thing for 3 of the 20 rows in hand (e.g. resourceId 3543368, "Procurement of
Pick- Up" both times), but for a fourth - resourceId 3403640, the row named in
`monitor/connectors/ghana.py`'s own module docstring - the tooltip is the title
plus one trailing full stop and nothing else ("...at Akim Swedru." against
"...at Akim Swedru"), which a bare `==` would carry through as if it were new
information. Stripping a single trailing "." from each side before comparing
catches that fourth row without over-matching: the other 16 rows each add a
real detail - a buyer name, a quantity, a reference number, e.g. 3517574's
title "Pick Up - Double Cabin 4x4" against its own description "SUPPLY AND
DELIVERY OF 15NO. DOUBLE CABIN PICK UP VEHICLE" - and none of those 16 becomes
equal to its own title merely by removing one period. So 4 of 20 rows in the
fixture have their body dropped; the other 16 keep theirs.

**Both dates need reshaping before `dates.py` will read them, and it is a
field reorder, not the millisecond trim `senegal.py`'s `_drop_millis` does.**
`deadline` and `publication_date` both publish Java's default
`Date.toString()` format, `EEE MMM dd HH:mm:ss 'GMT' yyyy` ("Fri Oct 02
10:00:00 GMT 2026"), confirmed against all 40 values across both fields on all
20 rows in the fixture with zero exceptions to the shape.
`monitor/normalise/dates.py`'s own `DATE_FORMATS` has nothing in this field
order (weekday, month name, day, time, zone name, year) at all - it was built
for feeds that publish some rotation of year-month-day. `_from_java_date_string`
below reads the fixed pieces of that one shape with a regex (the weekday is
ignored; `GMT` is always literal, since Ghana runs UTC+0 year-round with no
DST per `sources/ghana.yaml` - never a zone guess) and reassembles them into
`YYYY-MM-DDTHH:MM:SS+00:00`, a shape `dates.py` already parses. This is a
reshape, not a second parser: the regex only reorders fields `dates.py` cannot
read into a shape it already can, the same relationship `senegal.py`'s
`_drop_millis` has to its own source. A value that does not match the measured
shape raises here, naming the row's own resourceId, rather than reaching
`dates.py` as an anonymous warning.

**Both dates are required, and an unparseable or missing one raises rather
than becoming a silent `None`.** `deadline` and `publication_date` are present
and well-formed on all 20 rows measured, and
`monitor/connectors/ghana.py`'s own `parse_listing_page` already raises before
this module ever sees a row missing either one, or whose `publication_date`
fails that connector's own `%a %b %d %H:%M:%S GMT %Y` parse. This module holds
`deadline` to the same standard for the same reason: a missing or malformed
value means the endpoint changed shape, not that a notice was published
without a closing date, so it is reported as a mapping failure with the row's
own resourceId attached rather than shown to a reviewer as an opportunity with
no deadline at all.

**There is no value, no currency, and no CPV code anywhere on this listing.**
`sources/ghana.yaml`'s own grep of a sampled notice PDF and
`monitor/connectors/ghana.py`'s own docstring confirm both for the whole
listing: no price or currency field of any kind exists here (a bid bond or an
estimated value, if GHANEPS states one at all, would sit in the login-walled
document package this connector does not read - see `sources/ghana.yaml`'s
REGISTRATION note), and no CPV or other numbered code appears anywhere;
notices are typed only by the free-text `procedure` column. `estimated_value`
and `value_currency` stay `None` together, and `cpv_codes` stays the model's
own empty default - the free filter falls through to the lexicon for every
notice from this source, the same outcome Mali's and Senegal's uncoded
listings already produce. `procedure` and `status` are read by the connector
to decide what to keep and how to sort (both are checked/typed there, not
here) but `Notice` has no field for either, so neither is carried onto the
record - noted so the absence reads as a decision and not a gap, the same note
`mali.py` makes about `dtpCode`.

**`external_id` is `resource_id`, and `url` is the row's own `notice_pdf_url`,
not a shared listing page.** Unlike Mali, where no per-dossier link exists at
all and every notice shares one constant `NOTICE_URL`, every GHANEPS row
carries its own PDF link - `monitor/connectors/ghana.py`'s own
`parse_listing_page` already cross-checks that this URL's `resourceId` agrees
with the title link's before this module ever sees the row, so the two are
never in conflict here. That per-notice PDF is what a reviewer would actually
open to read the notice, so it is `Notice.url`, the same choice `senegal.py`
makes for its own per-record URL rather than a shared listing constant.

**Everything is national, read from the registry and not inferred from a
buyer's name.** `sources/ghana.yaml` declares `admin_level: national` on the
basis that GHANEPS is, by PPA's own published policy, the mandated e-GP system
for all of Ghana's government procurement, with no separate sub-national
portal - and the 20 buyers measured range from a ministry-linked authority
(Social Security and National Insurance Trust) to municipal and district
assemblies (Tema Metropolitan Assembly, Akatsi South Municipal Assembly,
Sekyere East District Assembly) without any row itself ever stating a level.
Guessing "local" from an assembly's own name would be exactly the judgement
rule 5 leaves to a stage that actually has evidence to make it; here there is
none, so the registry's own declaration is what is read, the same reasoning
`monitor/normalise/mali.py` and `senegal.py` give their own buyers.

**PERSONAL DATA: none found on the fields this module reads** - confirmed
independently by `tests/contract/test_ghana.py`'s own
`test_no_personal_data_is_present_in_either_recorded_page`, which greps all 20
rows' title, procuring entity and description for an email address or a
phone-number-shaped digit run and finds neither. There is nothing here for
this module to strip before a model call (rule 19).
"""

from __future__ import annotations

import re

import structlog

from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "ghana"
COUNTRY = "GH"
LANGUAGE = "en"

# sources/ghana.yaml declares the language, and monitor/connectors/ghana.py's own
# parse_listing_page already checks every fetched page still declares
# <html lang="en"> before this module ever sees a row - so this is enforced
# upstream and read here, not detected, the same 1.0 confidence mali.py and
# senegal.py give their own asserted, undetected language.
LANGUAGE_CONFIDENCE = 1.0

# See the module docstring: nothing on a row distinguishes a sub-national buyer,
# and sources/ghana.yaml declares GHANEPS the one national e-GP system.
ADMIN_LEVEL = "national"

# "Fri Oct 02 10:00:00 GMT 2026" -> weekday(ignored) month day time GMT year.
# Java's Date.toString() format (EEE MMM dd HH:mm:ss 'GMT' yyyy), measured
# against all 40 deadline/publication_date values across both fixture pages;
# see the module docstring. GMT is always literal here - Ghana runs UTC+0
# year-round with no DST (sources/ghana.yaml) - so this is a fixed reorder, not
# a zone guess.
JAVA_DATE = re.compile(
    r"^[A-Za-z]{3} (?P<month>[A-Za-z]{3}) (?P<day>\d{2}) (?P<time>\d{2}:\d{2}:\d{2}) GMT (?P<year>\d{4})$"
)

# Three-letter month name -> two-digit month number. Measured: only these
# twelve names ever appear across all 40 values in the fixture (English-locale
# Java output).
MONTH_NUMBER = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}


def _from_java_date_string(raw: str, *, resource_id: str, field: str) -> str:
    """Java's `Date.toString()` reshaped to the ISO form `dates.py` already reads.

    A regex reorder of a known shape, not a second parser: see the module
    docstring. Raises with the row's own resourceId attached rather than
    handing `dates.py` a shape it has never seen and cannot explain the source
    of.
    """
    match = JAVA_DATE.match(raw.strip()) if raw else None
    if not match:
        raise ValueError(f"ghana row {resource_id} has a {field} of {raw!r}, not Java's Date.toString() GMT form")
    month = MONTH_NUMBER.get(match.group("month"))
    if month is None:
        raise ValueError(f"ghana row {resource_id} has a {field} with unknown month {match.group('month')!r}")
    return f"{match.group('year')}-{month}-{match.group('day')}T{match.group('time')}+00:00"


def _published_at(raw: dict, *, resource_id: str):
    """`publication_date`, reshaped and required. See the module docstring."""
    value = raw.get("publication_date") or ""
    iso = _from_java_date_string(value, resource_id=resource_id, field="publication_date")
    parsed = parse_published(iso, source_id=SOURCE_ID, url=raw.get("notice_pdf_url", ""))
    if parsed is None:
        raise ValueError(f"ghana row {resource_id} has an unparseable publication_date {value!r}")
    return parsed


def _deadline_at(raw: dict, *, resource_id: str):
    """`deadline`, reshaped and required. See the module docstring."""
    value = raw.get("deadline") or ""
    iso = _from_java_date_string(value, resource_id=resource_id, field="deadline")
    parsed = parse_deadline(iso, source_id=SOURCE_ID, url=raw.get("notice_pdf_url", ""))
    if parsed is None:
        raise ValueError(f"ghana row {resource_id} has an unparseable deadline {value!r}")
    return parsed


def _body(title: str, description: str) -> str:
    """The description, unless it only repeats the title. See the module docstring.

    Compares after stripping one trailing "." from each side, which is the
    only variation measured between a genuine repeat and the title itself
    across the fixture's 20 rows.
    """
    description = description.strip()
    if description.rstrip(".") == title.rstrip("."):
        return ""
    return description


def map_notice(raw: dict) -> MappedNotice:
    """One GHANEPS Current Tenders row to a Notice."""
    resource_id = (raw.get("resource_id") or "").strip()
    if not resource_id:
        raise ValueError("ghana row has no resource_id")

    title = (raw.get("title") or "").strip()
    if not title:
        raise ValueError(f"ghana row {resource_id} has no title")

    buyer = (raw.get("procuring_entity") or "").strip()
    if not buyer:
        raise ValueError(f"ghana row {resource_id} has no procuring_entity")

    url = (raw.get("notice_pdf_url") or "").strip()
    if not url:
        raise ValueError(f"ghana row {resource_id} has no notice_pdf_url")

    body = _body(title, raw.get("description") or "")

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=resource_id,
        url=url,
        title=title,
        buyer=buyer,
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=_published_at(raw, resource_id=resource_id),
        deadline_at=_deadline_at(raw, resource_id=resource_id),
        language=LANGUAGE,
        language_confidence=LANGUAGE_CONFIDENCE,
        # No value or currency field anywhere on this listing; see the module
        # docstring. Left at the model's own None/None default rather than
        # passed explicitly, the same choice mali.py and senegal.py make for
        # the same reason.
        body=body,
    )
    # English published; no separate rendering to fill for step 14.
    return MappedNotice(notice=notice)
