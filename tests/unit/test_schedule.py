"""The cron arithmetic, because nothing downstream would notice it being wrong.

A mis-read schedule does not raise and does not fail a test that only checks a source was
fetched. It fetches on the wrong day, or every hour, and the only symptom is a portal's
logs - which we never see - or a source going quiet for a week. That asymmetry is why this
file tests the boundaries rather than the happy path: fetching one minute before the fire
time and one minute after are the two cases that decide whether an hourly wake behaves.

Every time here is UTC and written out in full, because a cron bug that only appears on
Sundays is not something a test should have to be run on a Sunday to find.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from monitor.schedule import Cron, ScheduleError, is_due, parse, previous_fire

# 2026-09-12 is a Saturday. The whole week is written out so a reader can check the
# weekday arithmetic without a calendar, and so the Sunday/Monday wrap is visible.
MONDAY = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
SUNDAY = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def test_the_week_is_the_week_this_file_claims_it_is():
    """If this fails every other assertion here is measuring the wrong days."""
    assert [day.strftime("%A") for day in (MONDAY, FRIDAY, SATURDAY, SUNDAY)] == [
        "Monday",
        "Friday",
        "Saturday",
        "Sunday",
    ]


# --- parsing, and what it refuses ---------------------------------------------


def test_a_daily_schedule_parses():
    assert parse("30 10 * * *") == Cron(minute=30, hour=10, days_of_week=frozenset(range(7)))


def test_a_weekday_range_parses_to_monday_through_friday():
    """Cron numbers Sunday 0, so 1-5 is Monday to Friday and excludes the weekend."""
    assert parse("0 11 * * 1-5").days_of_week == frozenset({1, 2, 3, 4, 5})


def test_sunday_can_be_written_as_0_or_7():
    """Both spellings are standard cron. Rejecting 7 would surprise whoever wrote it."""
    assert parse("0 9 * * 0").days_of_week == parse("0 9 * * 7").days_of_week == frozenset({0})


def test_a_list_of_days_parses():
    assert parse("0 9 * * 1,3,5").days_of_week == frozenset({1, 3, 5})


def test_a_range_ending_on_sunday_as_7_is_not_backwards():
    """`5-7` is Friday to Sunday, and rejecting it was a real bug caught in review.

    Sunday has two spellings in cron, 0 and 7. Folding 7 to 0 before comparing the
    ends of a range turns `5-7` into `5-0`, which looks like a backwards range and was
    refused alongside genuine typos like `5-1`. Nothing in the registry writes `5-7`
    today, so this was a wrong answer waiting for the first source that wanted a
    Friday-to-Sunday window - a West African portal publishing before a weekend, say.
    """
    assert parse("0 9 * * 5-7").days_of_week == frozenset({5, 6, 0})


def test_a_full_week_written_as_0_to_7_is_every_day():
    assert parse("0 9 * * 0-7").days_of_week == frozenset(range(7))


def test_a_genuinely_backwards_range_is_still_refused_and_says_what_to_write():
    """The distinction the fix has to preserve: `5-7` is fine, `5-1` is a typo."""
    with pytest.raises(ScheduleError, match="do not wrap"):
        parse("0 9 * * 5-1")


@pytest.mark.parametrize(
    ("expression", "because"),
    [
        ("*/15 * * * *", "step values are not read"),
        ("0,30 10 * * *", "a list of minutes is not read"),
        ("30 * * * *", "an hour of * is not a fixed hour"),
        ("30 10 1 * *", "day-of-month is not read"),
        ("30 10 * 6 *", "month is not read"),
        ("30 10 * *", "four fields is not a cron expression"),
        ("30 10 * * * *", "six fields is not a cron expression"),
        ("60 10 * * *", "minute 60 does not exist"),
        ("30 24 * * *", "hour 24 does not exist"),
        ("30 10 * * 8", "day-of-week 8 does not exist"),
        ("30 10 * * 5-1", "a backwards range is a typo, not a wrap"),
    ],
)
def test_an_unsupported_expression_raises_rather_than_being_approximated(expression, because):
    """Rule 4. The alternative is a source silently fetched on the wrong cadence.

    This is the whole reason the supported subset is small and explicit. A parser that
    read `*/15` as "every 15 minutes" would be useful; one that read it as "minute 15"
    would be a source hammered four times an hour with nothing to show for it, and no
    test anywhere would go red.
    """
    with pytest.raises(ScheduleError):
        parse(expression)


def test_the_refusal_says_what_it_will_not_read():
    with pytest.raises(ScheduleError, match="steps like"):
        parse("*/15 10 * * *")


# --- the previous fire time ----------------------------------------------------


def test_a_daily_schedule_fires_today_when_the_hour_has_passed():
    assert previous_fire("30 10 * * *", SATURDAY) == datetime(2026, 9, 12, 10, 30, tzinfo=UTC)


def test_a_daily_schedule_fires_yesterday_when_the_hour_has_not():
    morning = datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
    assert previous_fire("30 10 * * *", morning) == datetime(2026, 9, 11, 10, 30, tzinfo=UTC)


def test_the_fire_time_itself_counts_as_fired():
    """At exactly 10:30 the schedule has fired. A `>` here would delay it a day."""
    exactly = datetime(2026, 9, 12, 10, 30, tzinfo=UTC)
    assert previous_fire("30 10 * * *", exactly) == exactly


def test_one_minute_before_the_fire_time_is_still_yesterday():
    before = datetime(2026, 9, 12, 10, 29, tzinfo=UTC)
    assert previous_fire("30 10 * * *", before) == datetime(2026, 9, 11, 10, 30, tzinfo=UTC)


def test_a_weekday_schedule_on_a_saturday_reaches_back_to_friday():
    assert previous_fire("0 11 * * 1-5", SATURDAY) == datetime(2026, 9, 11, 11, 0, tzinfo=UTC)


def test_a_weekday_schedule_on_a_sunday_still_reaches_friday():
    """Two days back, which is the case a one-day step would get wrong."""
    assert previous_fire("0 11 * * 1-5", SUNDAY) == datetime(2026, 9, 11, 11, 0, tzinfo=UTC)


def test_a_monday_only_schedule_reaches_back_up_to_six_days():
    assert previous_fire("30 9 * * 1", SATURDAY) == datetime(2026, 9, 7, 9, 30, tzinfo=UTC)


def test_a_monday_only_schedule_on_monday_morning_reaches_the_previous_monday():
    """Before Monday's own fire time, the last fire was a week ago, not today."""
    monday_early = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    assert previous_fire("30 9 * * 1", monday_early) == datetime(2026, 9, 7, 9, 30, tzinfo=UTC)


def test_the_answer_is_always_in_the_past():
    """Swept across a fortnight and every hour, because off-by-one days hide in corners."""
    start = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    for expression in ("30 10 * * *", "0 11 * * 1-5", "30 9 * * 1", "0 0 * * *"):
        for hours in range(24 * 14):
            now = start + timedelta(hours=hours)
            fired = previous_fire(expression, now)
            assert fired <= now
            assert now - fired < timedelta(days=8)


# --- due, which is the question fetch actually asks -----------------------------


def test_a_source_never_fetched_is_due():
    """A source enabled today should be read once, not wait for tomorrow's fire."""
    assert is_due("30 10 * * *", None, SATURDAY) is True


def test_a_source_fetched_before_the_last_fire_is_due():
    yesterday = datetime(2026, 9, 11, 10, 31, tzinfo=UTC)
    assert is_due("30 10 * * *", yesterday, SATURDAY) is True


def test_a_source_fetched_after_the_last_fire_is_not_due():
    """The case that stops twenty-four fetches a day: read at 10:31, asked again at 12:00."""
    today = datetime(2026, 9, 12, 10, 31, tzinfo=UTC)
    assert is_due("30 10 * * *", today, SATURDAY) is False


def test_an_hourly_wake_settles_to_one_fetch_a_day():
    """The whole point, asserted as the scheduler would experience it.

    Before this module, twenty-four hourly wakes produced twenty-four fetches of a
    daily source. This sweeps three days of hourly wakes and counts.

    The first day has TWO, and that is correct rather than a leak. A source that has
    never been fetched is due on the first wake it sees - waiting until tomorrow's
    fire before reading a source someone enabled today would be worse - and then its
    real fire time comes round later the same day. Every day after is one. Written as
    a per-day count rather than a total so the shape is visible instead of averaged
    into a number that happens to look right.
    """
    schedule = "30 10 * * *"
    last_attempt = None
    per_day: list[int] = []
    for day in (12, 13, 14):
        fetched = 0
        for hour in range(24):
            now = datetime(2026, 9, day, hour, 30, tzinfo=UTC)
            if is_due(schedule, last_attempt, now):
                fetched += 1
                last_attempt = now
        per_day.append(fetched)

    assert per_day == [2, 1, 1]


def test_a_source_already_fetched_today_is_read_once_across_a_full_day():
    """The steady state, with no first-wake allowance to account for."""
    schedule = "30 10 * * *"
    last_attempt = datetime(2026, 9, 11, 10, 30, tzinfo=UTC)
    fetched = 0
    for hour in range(24):
        now = datetime(2026, 9, 12, hour, 30, tzinfo=UTC)
        if is_due(schedule, last_attempt, now):
            fetched += 1
            last_attempt = now
    assert fetched == 1


def test_a_failed_attempt_still_counts_as_the_pass_for_that_day():
    """Rule 2: the next wake must not retry a source that just refused us.

    `is_due` is given the last ATTEMPT, not the last success, and this asserts the
    consequence rather than the plumbing: a source read at 10:31 is not due at 11:31
    whether that read worked or not.
    """
    attempted_and_failed = datetime(2026, 9, 12, 10, 31, tzinfo=UTC)
    an_hour_later = datetime(2026, 9, 12, 11, 31, tzinfo=UTC)
    assert is_due("30 10 * * *", attempted_and_failed, an_hour_later) is False


# --- the registry has to keep parsing ------------------------------------------


def test_every_schedule_in_the_registry_parses():
    """The guard that matters. A new source with `*/30` would otherwise raise in
    production, on the first scheduled wake, inside the pipeline rather than here."""
    from monitor.registry import load_sources

    for source in load_sources():
        try:
            parse(source.schedule)
        except ScheduleError as error:
            pytest.fail(f"sources/{source.id}.yaml: {error}")


def test_every_registry_schedule_resolves_to_a_time_in_the_past():
    from monitor.registry import load_sources

    now = datetime.now(UTC)
    for source in load_sources():
        assert previous_fire(source.schedule, now) <= now, source.id


# --- the registry boundary rejects a bad schedule ------------------------------


def test_a_source_with_an_unreadable_schedule_fails_to_load():
    """Rule 4, at the boundary rather than on a live wake, and it matters more than it looks.

    Before this validator the first thing to parse a schedule was `is_due()`, called
    from inside `fetch()`'s loop over enabled sources - outside `fetch_source`'s
    try/except. One mistyped cron line in one YAML would therefore raise past every
    remaining source and abort the pass, so a typo against one portal stopped eight
    healthy ones being read. That is exactly the isolation `monitor/fetch.py`'s
    docstring promises, and the schedule was the one field able to break it.
    """
    import yaml
    from pydantic import ValidationError

    from monitor.models import Source
    from monitor.registry.load import SOURCES_DIR

    entry = yaml.safe_load((SOURCES_DIR / "ted.yaml").read_text(encoding="utf-8"))
    entry["schedule"] = "*/30 * * * *"

    with pytest.raises(ValidationError, match="steps like"):
        Source.model_validate(entry)


def test_a_source_with_a_readable_schedule_still_loads():
    """The validator must not reject what the registry actually writes."""
    import yaml

    from monitor.models import Source
    from monitor.registry.load import SOURCES_DIR

    entry = yaml.safe_load((SOURCES_DIR / "ted.yaml").read_text(encoding="utf-8"))

    assert Source.model_validate(entry).schedule == entry["schedule"]
