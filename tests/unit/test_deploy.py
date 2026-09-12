"""The scheduler's arithmetic, because it is arithmetic nobody re-checks by reading.

`deploy/docker-compose.cron.yml` schedules by sleeping to the next multiple of a
period counted from a fixed epoch offset, rather than by running a cron daemon in
the image - a second way to schedule the same command would be a second place to
look when a run did not happen (rule 1). The cost of that choice is that the
cadence is a number rather than a calendar expression, and a number in a shell
loop is not read by anyone after the day it is written.

It was wrong when written. Monday 06:30 UTC was worked out by hand as 279000 and
that lands on Sunday 05:30: the Unix epoch fell on a Thursday, so Monday is 4 days
out (345600) and the 6.5 hours to 06:30 are added (23400), not subtracted. Nothing
would have failed. The weekly report would simply have been collected on Sunday
morning, a day early, describing a week that had not finished - and the comment
above it would have gone on explaining that it ran on Monday.

So these tests read the numbers out of the file that is actually deployed and run
the same arithmetic the container runs, and they check the systemd timer agrees,
because the two are meant to be the same schedule expressed twice for two kinds of
host. A drift between them is the defect this file exists to catch.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from monitor.registry.load import REPO

COMPOSE = REPO / "deploy" / "docker-compose.cron.yml"
SYSTEMD = REPO / "deploy" / "systemd"

WEEK = 604800
HOUR = 3600

# Sample instants spread across a week, so a schedule that is right only when
# computed from a Saturday afternoon does not pass.
STARTS = [
    datetime(2026, 9, 12, 14, 0, tzinfo=UTC),  # Saturday afternoon
    datetime(2026, 9, 14, 6, 29, tzinfo=UTC),  # a minute before a tick
    datetime(2026, 9, 14, 6, 31, tzinfo=UTC),  # a minute after one
    datetime(2026, 9, 15, 23, 59, tzinfo=UTC),  # Tuesday, just before midnight
    datetime(2026, 12, 31, 12, 0, tzinfo=UTC),  # across a year boundary
]


def sleep_expressions() -> dict[str, tuple[int, int]]:
    """service name -> (period, offset) from each `sleep $(( P - (date - O) % P ))`.

    Read out of the deployed file rather than restated here. A test that carries its
    own copy of the number tests the copy.
    """
    # Two shapes are in use. Without an offset the grid starts at the epoch, which
    # is midnight UTC on the hour; with one the grid is shifted by that many seconds.
    plain = re.compile(r"sleep \$\$\(\( (\d+) - \$\$\(date \+%s\) % (\d+) \)\)")
    offsetted = re.compile(r"sleep \$\$\(\( (\d+) - \( \$\$\(date \+%s\) - (\d+) \) % (\d+) \)\)")

    text = COMPOSE.read_text(encoding="utf-8")
    found = {}
    for block in re.split(r"\n  (?=[a-z][a-z0-9-]*:\n)", text):
        name = block.strip().split(":", 1)[0]
        if match := offsetted.search(block):
            period, offset, modulus = int(match.group(1)), int(match.group(2)), int(match.group(3))
        elif match := plain.search(block):
            period, offset, modulus = int(match.group(1)), 0, int(match.group(2))
        else:
            continue
        assert period == modulus, f"{name}: sleeps to a {modulus}s grid but subtracts from {period}"
        found[name] = (period, offset)
    return found


def lands_at(period: int, offset: int, now: datetime) -> datetime:
    """Exactly what the container computes: sleep to the next multiple, then run."""
    stamp = int(now.timestamp())
    return now + timedelta(seconds=period - (stamp - offset) % period)


def test_every_cron_service_has_a_readable_sleep_expression():
    """If this stops matching, every other test here silently tests nothing."""
    assert set(sleep_expressions()) == {"pipeline-cron", "status-cron", "metrics-cron"}


@pytest.mark.parametrize("now", STARTS)
def test_the_metrics_service_runs_on_monday_at_half_past_six_utc(now):
    """Monday so the week's numbers precede the weekly tuning session; :30 so the
    report is not collected in the middle of the hourly pass it partly describes."""
    period, offset = sleep_expressions()["metrics-cron"]
    landed = lands_at(period, offset, now)

    assert period == WEEK
    assert landed.strftime("%A") == "Monday"
    assert (landed.hour, landed.minute) == (6, 30), landed.isoformat()


@pytest.mark.parametrize("now", STARTS)
def test_the_pipeline_service_runs_on_the_hour(now):
    period, offset = sleep_expressions()["pipeline-cron"]
    landed = lands_at(period, offset, now)

    assert period == HOUR
    assert (landed.minute, landed.second) == (0, 0), landed.isoformat()


@pytest.mark.parametrize("now", STARTS)
def test_the_status_service_runs_at_half_past(now):
    """Offset from the pass on purpose: a reading taken mid-pass describes half of one."""
    period, offset = sleep_expressions()["status-cron"]
    landed = lands_at(period, offset, now)

    assert period == HOUR
    assert (landed.minute, landed.second) == (30, 0), landed.isoformat()


@pytest.mark.parametrize("now", STARTS)
def test_no_service_ever_sleeps_zero(now):
    """A zero sleep is a loop that spins and runs the command thousands of times."""
    for name, (period, offset) in sleep_expressions().items():
        stamp = int(now.timestamp())
        assert 0 < period - (stamp - offset) % period <= period, name


# --- the two hosts must agree ------------------------------------------------


def test_the_systemd_timer_and_the_compose_loop_schedule_the_same_week():
    """Two expressions of one schedule, for a host with systemd and one without.

    They are written in different languages - an OnCalendar expression and an epoch
    offset - so nothing but a test makes them agree, and a drift between them means
    two hosts disagree about which week the numbers describe.
    """
    calendar = (SYSTEMD / "monitor-metrics.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon *-*-* 06:30:00 UTC" in calendar

    period, offset = sleep_expressions()["metrics-cron"]
    landed = lands_at(period, offset, STARTS[0])
    assert (landed.strftime("%A"), landed.hour, landed.minute) == ("Monday", 6, 30)


def test_the_weekly_timer_catches_up_and_the_hourly_ones_do_not():
    """Persistent= is the difference between work and a reading, and it is deliberate.

    A missed status reading has nothing to recover: replaying it would describe the
    moment it was replayed, which the next tick describes anyway. A missed week of
    numbers is work - the week happened, and its row is missing from the table the
    week 14 gate reads - so it is taken late rather than skipped.
    """
    assert "Persistent=true" in (SYSTEMD / "monitor-metrics.timer").read_text(encoding="utf-8")
    assert "Persistent=false" in (SYSTEMD / "monitor-status.timer").read_text(encoding="utf-8")


def test_the_metrics_unit_carries_both_database_urls():
    """Rule 11 in the unit file: collection reads as readonly, the write is pipeline.

    `monitor_pipeline` has no privilege at all on `approved_records`, and the export
    backlog lives there, so a metrics unit given only the pipeline URL would fail on
    the one number the week 14 gate most wants.
    """
    unit = (SYSTEMD / "monitor-metrics.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=/etc/monitor/monitor.env" in unit
    assert "monitor_readonly" in unit

    compose = COMPOSE.read_text(encoding="utf-8")
    metrics_block = compose.split("  metrics-cron:")[1]
    assert "DATABASE_URL_READONLY" in metrics_block
    assert "DATABASE_URL_PIPELINE" in metrics_block


def test_no_cron_service_restarts_itself():
    """Rule 2. Docker's restart backoff is a retry loop wearing a different hat."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count('restart: "no"') == 3
    assert "restart: always" not in text
    assert "restart: unless-stopped" not in text
