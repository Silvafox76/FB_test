"""When a source is next due, from the five-field cron in its registry entry.

WHY THIS EXISTS. `monitor/fetch.py`'s `fetch()` selected every enabled source on every
call and never looked at `Source.schedule`, while the scheduler wakes hourly. So a source
asking for `30 10 * * *` - once a day - was fetched twenty-four times a day. RUNBOOK.md
line 898 recorded the gap; what made it urgent was EBRD, whose host serves one pass and
then refuses, so the cadence was breaking rule 21's "one polite pass per schedule" on our
side rather than theirs (decision 49).

THE QUESTION THIS ANSWERS is not "what time is it" but "has this source been fetched
since the last time its schedule said to". That phrasing is what makes an hourly wake
correct: every wake asks each source the question, and only the ones whose answer is no
are fetched. It also means a missed wake catches up at the next one instead of skipping a
day, and that nothing needs to remember which wakes have happened.

LAST ATTEMPT, NOT LAST SUCCESS. `fetch_runs.started_at` is the signal, and a failed
attempt counts. A source that timed out at 10:30 has spent its polite pass for the day;
fetching it again at 11:30 would be a retry, which rule 2 forbids and which is exactly
what a host that just refused us does not need. The cost is that one bad day is one lost
day for that source, which is the right trade against hammering.

TIMES ARE UTC. `monitor/models.py` describes `schedule` as "five-field cron, in the
source's own timezone", and that is not implementable: `Source` carries no timezone field
and never has. Every schedule comment in `sources/*.yaml` that names a zone says UTC
(`'0 5 * * *'  # 05:00 UTC daily, 07:00 Berlin`), so UTC is what the registry means and
what this reads. The model comment is corrected in the same change.

THE SUPPORTED SUBSET IS SMALL AND ANYTHING ELSE RAISES (rule 4). All 19 schedules in the
registry are a fixed minute and hour with day-of-week either `*`, one day, or a range.
Step values (`*/4`), lists of hours, and day-of-month constraints are rejected at parse
rather than approximated, because a cron expression that is silently mis-read schedules a
source wrongly and nothing downstream would ever report it. A parser that quietly handles
two thirds of cron is worse than one that handles a third and says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

FIELDS = 5
LOOKBACK_DAYS = 8  # a week plus one, so a day-of-week constraint always resolves


class ScheduleError(Exception):
    """A cron expression this module will not read. Never a guess at what was meant."""


@dataclass(frozen=True)
class Cron:
    """A parsed schedule: one fire time a day, on the days-of-week it names."""

    minute: int
    hour: int
    days_of_week: frozenset[int]  # cron numbering, Sunday 0 through Saturday 6


def _days_of_week(field: str) -> frozenset[int]:
    """`*`, `3`, `1-5`, or `1,3,5`. Cron numbering, and 7 means Sunday as cron does."""
    if field == "*":
        return frozenset(range(7))

    days: set[int] = set()
    for part in field.split(","):
        if "-" in part:
            start, _, end = part.partition("-")
            # Compared BEFORE 7 is folded to 0, which is the whole subtlety. `5-7` is
            # Friday to Sunday and perfectly ordinary; folding first turns its end into
            # 0, makes it look like `5-0`, and rejects it as backwards alongside a
            # genuine typo like `5-1`. Nothing in the registry writes `5-7` today, so
            # this was a latent wrong answer rather than a visible one.
            first, last = _number(start), _number(end)
            if first > last:
                raise ScheduleError(
                    f"day-of-week range {part!r} runs backwards. Cron ranges do not wrap; "
                    "write the days as a list, for example '5,6,0'."
                )
            days.update(_fold(day) for day in range(first, last + 1))
        else:
            days.add(_fold(_number(part)))
    return frozenset(days)


def _number(value: str) -> int:
    """A day-of-week as written, 0 to 7, before Sunday's two spellings are reconciled."""
    if not value.isdigit() or not 0 <= int(value) <= 7:
        raise ScheduleError(f"day-of-week {value!r} is not 0 to 7")
    return int(value)


def _fold(day: int) -> int:
    """Cron writes Sunday as either 0 or 7; this module counts it once, as 0."""
    return day % 7


def parse(expression: str) -> Cron:
    """One cron expression, or a ScheduleError naming what is unsupported."""
    fields = expression.split()
    if len(fields) != FIELDS:
        raise ScheduleError(f"{expression!r} has {len(fields)} fields; a cron expression has {FIELDS}")

    minute, hour, day_of_month, month, day_of_week = fields

    for name, value in (("minute", minute), ("hour", hour)):
        if not value.isdigit():
            raise ScheduleError(
                f"{name} {value!r} in {expression!r} is not a plain number. This reads a fixed "
                "minute and hour only: steps like '*/15' and lists like '0,30' are rejected rather "
                "than approximated, because a mis-read schedule is invisible once it is running."
            )
    if not 0 <= int(minute) <= 59:
        raise ScheduleError(f"minute {minute!r} is not 0 to 59")
    if not 0 <= int(hour) <= 23:
        raise ScheduleError(f"hour {hour!r} is not 0 to 23")
    if day_of_month != "*" or month != "*":
        raise ScheduleError(
            f"{expression!r} constrains day-of-month or month. No source needs that yet, so it is "
            "rejected rather than half-implemented; add it here with its tests when one does."
        )

    return Cron(minute=int(minute), hour=int(hour), days_of_week=_days_of_week(day_of_week))


def previous_fire(expression: str, now: datetime) -> datetime:
    """The most recent moment this schedule fired, at or before `now`. UTC."""
    cron = parse(expression)
    candidate = now.astimezone(UTC).replace(hour=cron.hour, minute=cron.minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)

    for _ in range(LOOKBACK_DAYS):
        if (candidate.weekday() + 1) % 7 in cron.days_of_week:  # Monday is 0 in Python, 1 in cron
            return candidate
        candidate -= timedelta(days=1)

    # Unreachable while days_of_week is non-empty, and _days_of_week cannot return empty.
    # Raising rather than returning a wrong time, because a schedule that resolves to
    # nothing must not read as "due now" and fetch every wake (rule 4).
    raise ScheduleError(f"{expression!r} named no day in {LOOKBACK_DAYS} days")


def is_due(expression: str, last_attempt: datetime | None, now: datetime) -> bool:
    """Has this source gone unfetched since its schedule last said to?

    `last_attempt` is the newest `fetch_runs.started_at` for the source, or None if it
    has never been fetched. Never fetched is always due: a source enabled today should
    not wait for tomorrow's fire before it is read once.
    """
    if last_attempt is None:
        return True
    return last_attempt < previous_fire(expression, now)
