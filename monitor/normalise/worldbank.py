"""World Bank notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/worldbank.json`, recorded 2026-09-11.
The request that produced it, and why it is shaped that way, is in
`monitor/connectors/worldbank.py`'s docstring.

Four things the recorded page settled.

1. **The deadline is `submission_deadline_date`, not `submission_date`.** The
   obvious-looking field is the date the notice reached the Bank: on the notices
   recorded here it equals `noticedate` on all 100 rows. The bid deadline is
   `submission_deadline_date` ("2026-10-28T00:00:00Z") paired with
   `submission_deadline_time`, a bare wall clock ("10:00"). Building the deadline
   from `submission_date` would have handed every reviewer a date already in the
   past, and it would have looked entirely plausible on the candidate page.
   The deadline pair is not always in the future either: 12 of the 91 recorded
   pairs close on the notice's own day, each with a real clock time, because the
   Bank posted the notice the day the bid closed. That is what was published, so
   it is what is stored; moving such a date to something more plausible is the
   invention rule 10 exists to forbid, and the reviewer sees the real one.

2. **The field set varies by notice type.** All 9 General Procurement Notices in
   the fixture carry no `bid_description`, no `bid_reference_no`, no
   `procurement_group` and no deadline pair: a GPN announces a project's whole
   procurement programme, so it has no single bid and no single closing date. The
   title therefore comes from `bid_description` where the notice names a bid and
   from `project_name` where it does not. That is one rule read off one field's
   presence, not a second selector for the same value (rule 1): the two fields
   name different things, and `project_name` is on 100 of 100 rows while
   `bid_description` is on 90. Absence is not perfectly predicted by type - one
   Request for Expression of Interest in the fixture omits `bid_description` too -
   which is why the rule is written on the field and not on the type.

3. **No CPV codes exist anywhere in this source**, so every notice reaches the
   lexicon stage; `monitor/filter/` already treats an absent code as "not a failed
   match" rather than a drop. And no notice states a value, so
   `estimated_value_usd` is never set from here.

4. **`notice_text` is HTML** and it is the body. It is stripped to text, in the
   notice's own language, and nothing is translated here (rule 9): 67 of the 100
   recorded notices are English, 31 French, 2 Spanish.

`project_id` ("P176769") is on every row and is the Finance Project ID of
Architecture v0.4 appendix E, which `monitor/stage/record.py` carries as
`finance_project_id` and currently leaves empty. Nothing here can pass it on: the
`notices` table and the `Notice` model have no column for it, and both are outside
this connector's lane. It is noted so the step that adds the column knows the
value is already in hand and does not go looking for it.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time

import structlog
from selectolax.parser import HTMLParser

from monitor.connectors.worldbank import ISO2_BY_BANK_NAME, NOTICE_URL, notice_date
from monitor.models import Notice
from monitor.normalise.dates import parse_deadline
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "worldbank"

# From sources/worldbank.yaml, which is the authority; the contract test asserts
# the two agree. The notice is published by the financier rather than the buyer,
# and that is what lets a World Bank notice and the national notice for the same
# tender land in one candidate cluster instead of two.
ADMIN_LEVEL = "donor"

# `notice_lang_name` is the ISO 639-2 English language name, which is why two of
# them arrive as a semicolon-separated pair of synonyms ("Spanish; Castilian").
# The lexicons and the geography weights are all keyed on ISO 639-1, so the name
# is converted here. Source-specific, like the country names in the connector:
# no other source publishes a language this way. An unmapped name raises, because
# a notice classified under the wrong language is filtered against the wrong
# lexicon and dropped for the wrong reason.
LANGUAGE_ALPHA1 = {
    "English": "en",
    "French": "fr",
    "Spanish": "es",
    "Portuguese": "pt",
    "Russian": "ru",
    "Arabic": "ar",
}

# A clock with no date part, as `submission_deadline_time` publishes it.
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")

# 4 of the 91 deadline pairs in the fixture state 00:00, which is a data-entry
# default rather than a tender that closes as its closing day begins. It is read
# as "no time of day stated" and takes the same rule
# `monitor/normalise/dates.py` already applies to a bare date: the end of that
# day, because a tender closing on the 30th is open on the 30th and midnight would
# drop a day of runway from what the reviewer sees.
NO_TIME_STATED = "00:00"
END_OF_DAY = (23, 59, 59)

# Runs of blank lines left behind when the HTML's layout paragraphs are removed.
BLANK_LINES = re.compile(r"\n{3,}")


def map_notice(raw: dict) -> MappedNotice:
    """One World Bank procurement notice to a Notice. Raises on anything unmappable."""
    external_id = raw["id"]
    url = NOTICE_URL.format(id=external_id)
    title = _title(raw)
    body = strip_html(raw["notice_text"])

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        # Present and non-empty on all 100 recorded rows. This is the buyer the
        # record builder proposes as text; no Account is resolved here or anywhere
        # in the pipeline (CLAUDE.md, export target).
        buyer=raw["contact_organization"].strip(),
        # Where the project is, not where the contact sits: `contact_ctry_name` is
        # the address on the notice and is sometimes a different country.
        country=country_alpha2(raw["project_ctry_name"]),
        admin_level=ADMIN_LEVEL,
        # `noticedate` is a day with no time. Midnight UTC is what
        # `monitor/normalise/dates.py` gives a bare publication date, and building
        # the datetime here rather than letting pydantic read a date string is what
        # keeps it aware: a naive value would be read in the session's timezone on
        # its way into a timestamptz column.
        published_at=datetime.combine(notice_date(raw), time.min, tzinfo=UTC),
        deadline_at=deadline(raw, url=url),
        language=language_alpha2(raw["notice_lang_name"]),
        # The notice states its own language rather than it being detected.
        language_confidence=1.0,
        # No CPV anywhere in this source; the lexicon stage decides these.
        body=body,
        status="detected",
    )
    # The notice is published in one language and no English rendering comes with
    # it. Empty means "no English was published", never "the original was English"
    # (monitor/normalise/mapped.py), so the English two thirds are not marked as
    # translated and the French third reaches step 14.
    return MappedNotice(notice=notice)


def _title(raw: dict) -> str:
    """`bid_description` where the notice names a bid, `project_name` where not.

    See point 2 of the module docstring. Written out rather than looped over a
    list of field names, because these are two different things and not two
    selectors for one: the first is the bid, the second is the project the bid
    belongs to. A notice with neither cannot be titled and raises, because
    `Notice.title` is what the reviewer, the lexicon and the deduper all read.
    """
    bid = (raw.get("bid_description") or "").strip()
    if bid:
        return bid

    project = (raw.get("project_name") or "").strip()
    if project:
        return project

    raise ValueError(f"{raw['id']}: notice has neither bid_description nor project_name")


def country_alpha2(name: str) -> str:
    """The Bank's own country spelling to ISO2: "Gambia, The" -> "GM". Anything else raises."""
    if name not in ISO2_BY_BANK_NAME:
        raise ValueError(
            f"unknown World Bank country name {name!r}; add it to BANK_COUNTRY_NAMES in monitor/connectors/worldbank.py"
        )
    return ISO2_BY_BANK_NAME[name]


def language_alpha2(name: str) -> str:
    """The ISO 639-2 language name to ISO 639-1: "Spanish; Castilian" -> "es".

    An unmapped language name raises.

    ISO 639-2 names list their synonyms after a semicolon and the first is the one
    the tables are keyed on, so the pair is split rather than written out.
    """
    primary = name.split(";")[0].strip()
    if primary not in LANGUAGE_ALPHA1:
        raise ValueError(
            f"unknown World Bank language name {name!r}; add it to LANGUAGE_ALPHA1 in monitor/normalise/worldbank.py"
        )
    return LANGUAGE_ALPHA1[primary]


def deadline(raw: dict, *, url: str = ""):
    """The bid deadline: the stated closing date, at the stated time of day.

    None where the notice states no closing date, which is what a General
    Procurement Notice is: 9 of the 100 recorded rows, all of them GPNs. A missing
    deadline is visible on the candidate page; an invented one sends a reviewer to
    a closed tender (rule 10).

    The wall clock carries no timezone. It is read as UTC, which is at most three
    hours generous across the covered countries (UTC+0 to UTC+3) and never stingy,
    and the alternative is inventing a timezone per country. The original strings
    stay in the stored notice for anyone who needs the exact hour.
    """
    stated = (raw.get("submission_deadline_date") or "").strip()
    if not stated:
        return None

    closing = parse_deadline(stated, source_id=SOURCE_ID, url=url)
    if closing is None:
        return None

    hour_minute = clock(raw.get("submission_deadline_time") or "", notice_id=raw["id"])
    if hour_minute is None:
        end_hour, end_minute, end_second = END_OF_DAY
        return closing.replace(hour=end_hour, minute=end_minute, second=end_second)
    hour, minute = hour_minute
    return closing.replace(hour=hour, minute=minute)


def clock(value: str, *, notice_id: str = "") -> tuple[int, int] | None:
    """The stated time of day as (hour, minute): "10:00" -> (10, 0).

    None where the notice states no time of day.

    None covers three cases that mean the same thing to a reader: the field is
    absent, it is 00:00 (the data-entry default, 4 of 91 recorded), or it is a
    string this rule cannot read. The last is logged with the raw value: the
    closing *date* still parsed, and losing a whole deadline over an unreadable
    hour would cost the reviewer a day of runway rather than save them one.
    """
    text = value.strip()
    if not text or text == NO_TIME_STATED:
        return None

    match = CLOCK.match(text)
    if not match:
        log.warning("worldbank_clock_unparsed", raw=value, notice_id=notice_id, source_id=SOURCE_ID)
        return None

    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        log.warning("worldbank_clock_out_of_range", raw=value, notice_id=notice_id, source_id=SOURCE_ID)
        return None
    return hour, minute


def strip_html(markup: str) -> str:
    """`notice_text` as text, in the notice's own language (rule 9).

    Non-breaking spaces become ordinary ones. They are HTML layout rather than the
    publisher's prose, and a phrase written with one in the middle would not match
    the lexicon, which matches whole phrases with ordinary spaces in them: the
    notice would be dropped for containing the phrase in the wrong kind of space.
    """
    text = HTMLParser(markup).text(separator="\n").replace("\xa0", " ")
    return BLANK_LINES.sub("\n\n", text).strip()
