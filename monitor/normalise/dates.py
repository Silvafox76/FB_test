"""Deadline parsing, by rule, from the original notice (rule 10).

Two properties this module exists to hold:

  - It never reads a translation. A deadline parsed from `title_en` or `body_en`
    is blocking, because a translation can move a date and nobody would see it.
    Nothing here takes an English rendering; it takes the original string.
  - A string it cannot parse yields None and the caller logs the raw string. It
    never guesses, and it never reaches for a natural-language date library: a
    wrong deadline is worse than a missing one, because a missing one is visible
    on the candidate page and a wrong one sends the reviewer to a closed tender.

The formats here are the ones structured feeds actually emit: ISO 8601 with and
without a zone, and the two unambiguous date-only forms. Formats where day and
month are ambiguous (01/02/2026) are deliberately absent: a source that publishes
them needs its own mapper deciding which it means, not a guess made here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import structlog

log = structlog.get_logger(__name__)

# Tried in order. Every one of these is unambiguous about which number is the day.
DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M%z",
    "%Y-%m-%dT%H:%M",
    # TED publishes a date with an offset and no time at all ("2026-09-09+02:00"),
    # for both the publication date and the per-lot deadlines. Recorded from the
    # live API on 2026-09-11.
    "%Y-%m-%d%z",
    "%Y-%m-%d",
    "%Y%m%d",
)

# The formats that carry no time of day. A deadline in one of these is the end of
# that day; a publication date is its midnight.
DATE_ONLY_FORMATS = ("%Y-%m-%d%z", "%Y-%m-%d", "%Y%m%d")


def parse_deadline(raw: str, *, source_id: str = "", url: str = "") -> datetime | None:
    """The deadline as an aware UTC datetime, or None with the raw string logged.

    A date with no time is the end of that day: a tender closing on the 30th is
    open on the 30th, and treating it as 00:00 would drop a day of runway.
    """
    if not raw or not raw.strip():
        return None

    text = raw.strip().replace("Z", "+0000")
    # '+01:00' is ISO but strptime's %z wants '+0100' on older formats; both parse
    # once the colon is removed, and a colon cannot appear elsewhere in an offset.
    if len(text) > 6 and text[-3] == ":" and text[-6] in "+-":
        text = text[:-3] + text[-2:]

    for fmt in DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt in DATE_ONLY_FORMATS:
            parsed = datetime.combine(parsed.date(), datetime.max.time(), tzinfo=parsed.tzinfo)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    log.warning("deadline_unparsed", raw=raw, source_id=source_id, url=url)
    return None


def parse_published(raw: str, *, source_id: str = "", url: str = "") -> datetime | None:
    """Same rules for the publication date. A date with no time is its midnight."""
    if not raw or not raw.strip():
        return None

    parsed = parse_deadline(raw, source_id=source_id, url=url)
    if parsed is None:
        return None
    # parse_deadline pushes a bare date to end of day, which is right for a
    # closing date and wrong for a publication date.
    if parsed.time() == datetime.max.time():
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    return parsed


def as_date(value: datetime | None) -> date | None:
    return None if value is None else value.date()
