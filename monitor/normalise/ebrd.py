"""EBRD notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/ebrd.json`, recorded 2026-09-12: one
listing response and the eleven notice pages that run fetched. The request that
produced it, and why it is shaped that way, is in `monitor/connectors/ebrd.py`'s
docstring.

**Everything mapped here comes from the notice's own field table**, the `detail`
half of the payload. The `listing` half is read for one value, `notice_id`, which
is the only place it exists: it is the `displayNoticeId` in the notice's URL, and
the field table's `ECEPP ID` is a different number - the *exercise* the notice
belongs to (42939486 where the notice is 42939610), which several notices share.
The listing is otherwise not read, because it disagrees with the notice page in two
measured ways and the notice page is right both times: it renders the noon hour as
midnight, and it shows "N/A" as the closing date for any notice that is
information-only while the page states the real one.

Five things the recorded run and two separately probed General Procurement Notices
settled.

1. **The title comes from `Procurement Exercise Name` where the notice names an
   exercise and from `Project Name` where it does not.** That is one rule read off
   one field's presence, not a second selector for the same value (rule 1): the two
   fields name different things, and the notice types that carry no exercise name
   carry no exercise. A General Procurement Notice announces a project's whole
   procurement programme before any single contract is tendered, so it has a
   project and nothing else - neither of the two probed GPNs has an exercise name,
   a description, a procurement method, an issue date or a closing date, and GPNs
   are 715 of the 4,050 archived notices. All eleven notices in the window carried
   both fields. Written out rather than looped over a list of field names for the
   same reason `monitor/normalise/worldbank.py` writes its title rule out.

2. **`Closing Date` is stored as published even when it is already past** (rule 10).
   Both Shortlist Notices in the run close before the day they were published on:
   one published 21/08/2026 closed 26/02/2026, the other published 11/09/2026
   closed 09/06/2026. That is not an error in the page. A Shortlist Notice names
   the firms shortlisted for an exercise whose expression-of-interest stage closed
   months earlier, so the closing date it states is the real one and the
   competition really is over. Moving such a date to something more plausible is
   the invention rule 10 exists to forbid; the reviewer sees the real one and can
   see at a glance that this is intelligence rather than an opportunity. Whether
   such notices are worth reading at all is `exclude_notice_types` in
   sources/ebrd.yaml, where a person can change it.

3. **The body is `Procurement Exercise Description` and nothing else, and the rest
   of the page is deliberately left unread.** Absent on 2 of the 11 - both
   Shortlist Notices - and on both GPNs, so `Notice.body` is empty for them and the
   title is what the lexicon and the scorer see. The page does carry more prose
   below the table, and `monitor/connectors/ebrd.py` explains why none of it is
   fetched into the record: on a tender notice it is four paragraphs of funding and
   registration boilerplate identical on every notice, and its only
   notice-specific content is the client's postal address with a named officer's
   phone and personal email, or - on a Shortlist Notice - the names and countries
   of the shortlisted firms. A named contact or a third party's business record
   reaching a model call is blocking (rule 19), and the scorer reads `body`.

4. **No CPV codes and no stated value exist anywhere in this source**, so every
   notice reaches the lexicon stage - `monitor/filter/cpv.py` already treats an
   absent code as "not a failed match" - and `estimated_value_usd` is never set
   from here. `Business Sector` is the nearest thing to a classification and it is
   not one: across the recorded eleven it runs "Municipal and Environmental
   Infrastructure", "Transport", "Power and Energy", "Infra Europe", "Infra EMEA",
   "Energy Efficiency and Climate Change" and "Information & Communication
   Technologies", which are two overlapping vocabularies from different eras of the
   Bank's own reporting rather than a code list anything could be mapped onto.

5. **The language is English and the portal says so rather than it being detected**,
   which is why `language_confidence` is 1.0: every notice page declares
   `<html lang="en_GB">`, and ECEPP's own dialog tells clients that "any data
   entered into the system must only be entered in English". 4,048 of the 4,050
   titles in the archive are English. The two that are not are Cyrillic tender
   numbers that reached the portal double-encoded - one of them is in this fixture,
   as `Tender No. \xc3\x90\xc2\x90 2.1` - and they are stored exactly as published,
   mojibake included (rule 9). Repairing them would be guessing at bytes the
   publisher got wrong, and the reviewer follows the URL to a page showing the same
   thing.

`EBRD Project ID` ("54575") is the Bank's operation number and is the Finance
Project ID of Architecture v0.4 appendix E, which `monitor/stage/record.py` carries
as `finance_project_id` and currently leaves empty. Nothing here can pass it on:
the `notices` table and the `Notice` model have no column for it, and both are
outside this connector's lane. It is noted so the step that adds the column knows
the value is already in hand and does not go looking for it. It is also not always
an operation number: one of the two probed GPNs states `2026` in that field, which
is a year somebody typed into the wrong box.
"""

from __future__ import annotations

import structlog

from monitor.connectors.ebrd import (
    DETAIL_CLIENT,
    DETAIL_CLOSING,
    DETAIL_COUNTRY,
    DETAIL_DESCRIPTION,
    DETAIL_EXERCISE_NAME,
    DETAIL_PROJECT_NAME,
    DETAIL_PUBLISHED,
    ISO2_BY_EBRD_NAME,
    NOTICE_URL,
    iso_timestamp,
)
from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "ebrd"

# From sources/ebrd.yaml, which is the authority; the contract test asserts the two
# agree. The notice is published through the financier's portal rather than by the
# buyer's own system, and that is what lets an EBRD notice and the national notice
# for the same tender land in one candidate cluster instead of two.
ADMIN_LEVEL = "donor"

# ECEPP's declared publication language: `<html lang="en_GB">` on every notice page,
# and the portal's own rule that data may only be entered in English. Point 5 of the
# module docstring.
LANGUAGE = "en"


def map_notice(raw: dict) -> MappedNotice:
    """One EBRD procurement notice to a Notice. Raises on anything unmappable."""
    listing, detail = raw["listing"], raw["detail"]
    notice_id = listing["notice_id"]
    title = _title(detail, notice_id=notice_id)
    body = detail.get(DETAIL_DESCRIPTION, "")
    url = NOTICE_URL.format(notice_id=notice_id)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=notice_id,
        url=url,
        title=title,
        # Present on all thirteen pages read. This is the buyer the record builder
        # proposes as text - EBRD's borrower, not EBRD - and no Account is resolved
        # here or anywhere in the pipeline (CLAUDE.md, export target).
        buyer=detail[DETAIL_CLIENT],
        country=country_alpha2(detail[DETAIL_COUNTRY]),
        admin_level=ADMIN_LEVEL,
        published_at=published(detail, notice_id=notice_id, url=url),
        deadline_at=deadline(detail, notice_id=notice_id, url=url),
        language=LANGUAGE,
        language_confidence=1.0,
        # No CPV and no stated value anywhere in this source; the lexicon stage
        # decides these. Point 4 of the module docstring.
        body=body,
        status="detected",
    )
    # The notice is published in English and no separate English rendering comes
    # with it. Empty means "no English was published", never "the original was
    # English" (monitor/normalise/mapped.py), and duplicating an English original
    # into title_en would be noise - the same call sources/euft.yaml's connector
    # makes. Step 14's translator has nothing to do for this source.
    return MappedNotice(notice=notice)


def _title(detail: dict, *, notice_id: str) -> str:
    """The exercise where the notice names one, the project where it does not.

    See point 1 of the module docstring. A notice with neither cannot be titled and
    raises, because `Notice.title` is what the reviewer, the lexicon and the deduper
    all read - and `Project Name` is on all thirteen pages read, so reaching that
    raise means the page changed.
    """
    exercise = detail.get(DETAIL_EXERCISE_NAME, "")
    if exercise:
        return exercise

    project = detail.get(DETAIL_PROJECT_NAME, "")
    if project:
        return project

    raise ValueError(f"{notice_id}: notice states neither {DETAIL_EXERCISE_NAME!r} nor {DETAIL_PROJECT_NAME!r}")


def country_alpha2(name: str) -> str:
    """EBRD's own country spelling to ISO2: "Macedonia FYR" -> "MK". Anything else raises.

    Unreachable for a notice this connector fetched - `fetch_detail` has already
    checked this value against the covered name the scope matched - and kept because
    that check is the thing that could be removed by someone who did not know this
    one was leaning on it.
    """
    if name not in ISO2_BY_EBRD_NAME:
        raise ValueError(
            f"unknown EBRD country name {name!r}; add it to EBRD_COUNTRY_NAMES in monitor/connectors/ebrd.py"
        )
    return ISO2_BY_EBRD_NAME[name]


def published(detail: dict, *, notice_id: str, url: str):
    """`Publication Date`: when EBRD published this notice.

    Not `Issue Date`, which is when the procurement exercise opens or opened and is
    a different day on 8 of the 11 recorded notices - by one day on three of them,
    and by 235 days on the Shortlist Notice whose exercise was issued in December
    and whose notice was published the following August. It runs in both directions:
    three of the eight are issued *after* the notice announcing them. The pilot's
    promise is that a notice arrives a day after publication, so publication is
    what this is.

    The clock carries no timezone; the listing labels the same stamps "UK Time".
    They are read as UTC, which is exactly one hour generous between late March and
    late October and exact the rest of the year, and never early. The alternative is
    a tz database this image does not declare as a dependency, and one hour on a
    six-week tender is not worth adding one for. `monitor/normalise/worldbank.py`
    makes the same call for a worse spread (three hours).
    """
    return parse_published(
        iso_timestamp(detail[DETAIL_PUBLISHED], notice_id=notice_id, label=DETAIL_PUBLISHED) or "",
        source_id=SOURCE_ID,
        url=url,
    )


def deadline(detail: dict, *, notice_id: str, url: str):
    """`Closing Date`, as published, even where it is already past (rule 10).

    None where the notice states no closing date, which is what a General
    Procurement Notice is: neither of the two probed GPNs carries the field, and
    they are 715 of the 4,050 archived notices. A missing deadline is visible on
    the candidate page; an invented one sends a reviewer to a closed tender.

    Read as UTC for the reason in `published`, and a stated 00:00 becomes the end of
    that day for the reason in `monitor/connectors/ebrd.py`'s `iso_timestamp`.
    """
    stated = iso_timestamp(detail.get(DETAIL_CLOSING, ""), notice_id=notice_id, label=DETAIL_CLOSING)
    if stated is None:
        return None
    return parse_deadline(stated, source_id=SOURCE_ID, url=url)
