"""One NPPA bid-table row -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/sierra_leone.json`, recorded 2026-09-12,
and against the 32 rows `monitor/connectors/sierra_leone.py` parses out of it.

The row has five fields and no more: `organisation`, `date_posted`,
`expiration_date`, `description`, `document_url`. Three consequences follow, and
they are the whole of what this module decides.

**The description IS the title.** NPPA's table has no separate title column: the
one prose cell carries the whole of what is being bought ("SELECTION OF A PRIVATE
PARTNER FOR THE OPERATION, MAINTENANCE AND MANAGEMENT (OM&M) OF THE NATIONAL
STADIUM"). So it becomes `Notice.title` and `body` stays empty, rather than being
copied into both - a body that merely repeats the title tells the scorer nothing
and doubles what is sent to a paid model call. The real detail is in the linked
PDF, which nothing here fetches: that is a second request per row against a source
whose own registry entry asks for one polite pass, and it is not this module's to
make (rule 5).

**The dates are DD-MM-YYYY, and that was established rather than assumed.**
`monitor/normalise/dates.py` deliberately refuses ambiguous day-month formats and
says a source publishing them "needs its own mapper deciding which it means, not a
guess made here". This is that decision, and the evidence is in the fixture: across
the 64 date values in the 32 rows, **41 have a first component above 12** (up to
31) and **the second component never exceeds 10**. There is no reading of that data
on which the first number is a month. `_parse` therefore reorders to ISO and hands
the result to the shared parser, so the one place that knows how to build an aware
UTC datetime stays the one place that does it.

**Everything is national.** The rows name ministries, agencies and authorities -
Ministry of Sports, National Revenue Authority, Ministry of Technical and Higher
Education - and the table carries no field that would distinguish a district
council. `national` is what the registry entry declares and what every row in the
recorded fixture supports.

No CPV: NPPA publishes none, so `cpv_codes` is empty and the free filter falls
through to the lexicon stage, which is what `monitor/filter/run.py` does for any
notice with no code at all.
"""

from __future__ import annotations

import structlog

from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "sierra_leone"
COUNTRY = "SL"
LANGUAGE = "en"

# The page declares `<html lang="en">` and the connector refuses to parse it
# otherwise, so the language is asserted by acquisition rather than detected here.
# 1.0 rather than a detector's score for that reason.
LANGUAGE_CONFIDENCE = 1.0

# Every row is a central government body. See the module docstring.
ADMIN_LEVEL = "national"

DATE_PARTS = 3
FULL_YEAR = 4  # a two-digit year is not expanded; see _iso


def _iso(raw: str) -> str:
    """`27-08-2026` -> `2026-08-27`. Anything else is returned untouched to fail later.

    Deliberately not a parse. It reorders a known shape into the one unambiguous
    form `monitor/normalise/dates.py` already accepts, so a second datetime-building
    path does not appear here (rule 1) and a string this module has not seen before
    reaches that module's logging rather than being silently dropped in this one.
    """
    parts = [part.strip() for part in raw.strip().split("-")]
    if len(parts) != DATE_PARTS:
        return raw
    day, month, year = parts
    if len(year) != FULL_YEAR or not (day.isdigit() and month.isdigit() and year.isdigit()):
        # A two-digit year reaches here and is deliberately not expanded. Four rows in
        # the recorded fixture are typed loosely - `30- 04-2026`, `23 -02- 2026`,
        # `23-04-26` - and the three with stray spaces are recovered above, because
        # trimming whitespace cannot change which number is the day. Choosing a century
        # can. `23-04-26` is almost certainly 2026, and "almost certainly" is what
        # dates.py refuses on the grounds that a wrong deadline sends a reviewer to a
        # closed tender while a missing one is visible on the candidate page.
        return raw
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def map_notice(raw: dict) -> MappedNotice:
    """One bid-table row to a Notice."""
    title = (raw.get("description") or "").strip()
    if not title:
        # Rule 4. The connector already refuses a row without five cells, so an
        # empty description means the cell itself changed, not that a row is short.
        raise ValueError("NPPA row has no description, which is the only title this source publishes")

    buyer = (raw.get("organisation") or "").strip()
    url = (raw.get("document_url") or "").strip()

    published_at = parse_published(_iso(raw.get("date_posted") or ""), source_id=SOURCE_ID, url=url)
    deadline_at = parse_deadline(_iso(raw.get("expiration_date") or ""), source_id=SOURCE_ID, url=url)

    notice = Notice(
        content_hash=content_hash(title, ""),
        source_id=SOURCE_ID,
        url=url,
        title=title,
        buyer=buyer,
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=published_at,
        deadline_at=deadline_at,
        language=LANGUAGE,
        language_confidence=LANGUAGE_CONFIDENCE,
    )
    # `title_en` and `body_en` stay empty: the notice is already English, and
    # duplicating it into the English rendering would make a machine translation of
    # something nobody translated. `monitor/normalise/mapped.py` says so directly.
    return MappedNotice(notice=notice)
