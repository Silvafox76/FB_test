"""config/fx.yaml's validation, and the rate store's staleness rule."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
import yaml
from pydantic import ValidationError

from monitor.fx.config import load
from monitor.fx.nbu import Rate
from monitor.fx.store import StaleRatesError, latest, store
from monitor.schedule import ScheduleError

# A publisher id no real run uses, so these rows cannot collide with `monitor fx`.
TEST_PUBLISHER = "test-publisher"


def _document(tmp_path, **overrides):
    document = yaml.safe_load(open("config/fx.yaml", encoding="utf-8"))
    document.update(overrides)
    path = tmp_path / "fx.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


# --- the config --------------------------------------------------------------


def test_the_shipped_config_loads_and_says_what_it_should():
    config = load()

    assert config.publisher.base_currency == "UAH"
    assert config.target_currency == "USD"
    assert config.pegged_to_eur == {"XOF": Decimal("655.957"), "XAF": Decimal("655.957")}
    assert config.max_rate_age_days > 0
    assert config.rounding == "ROUND_HALF_UP"


def test_an_unknown_rounding_mode_is_refused_on_load_not_at_the_first_conversion(tmp_path):
    with pytest.raises(ValidationError, match="ROUND_HALF_UP"):
        load(_document(tmp_path, rounding="ROUND_TOWARDS_THE_SALESPERSON"))


def test_a_peg_that_is_not_a_rate_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="not a rate"):
        load(_document(tmp_path, pegged_to_eur={"XOF": 0}))


def test_a_currency_that_is_not_iso_4217_is_refused(tmp_path):
    with pytest.raises(ValidationError, match="alpha-3"):
        load(_document(tmp_path, target_currency="DOLLARS"))


def test_a_bad_schedule_fails_on_load_rather_than_at_the_first_scheduled_run(tmp_path):
    """ScheduleError, not ValidationError: it does not subclass ValueError, so
    pydantic lets it through with its own message. Same behaviour as `Source`'s
    schedule validator, deliberately - the point is that it fails when the YAML is
    read rather than on a live wake weeks later."""
    document = yaml.safe_load(open("config/fx.yaml", encoding="utf-8"))
    document["publisher"]["schedule"] = "every morning please"
    path = tmp_path / "fx.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")

    with pytest.raises(ScheduleError):
        load(path)


def test_an_unknown_key_is_refused_rather_than_ignored(tmp_path):
    """extra="forbid": a typo in a config key is a loud failure (rule 4)."""
    with pytest.raises(ValidationError):
        load(_document(tmp_path, roundingg="ROUND_HALF_UP"))


# --- the store ---------------------------------------------------------------


@pytest.fixture
def conn():
    """A pipeline connection, with the rows it writes cleaned up afterwards.

    `store` commits - `conn.transaction()` on a non-autocommit connection is the
    outermost block, so a rollback at teardown would have nothing left to undo -
    and `monitor_pipeline` holds no delete on `fx_rates`, because the table is
    append only by grant for the same reason `events` is. So the teardown runs as
    the owner, exactly as the review app's fixtures do, and it deletes only the
    test publisher's own rows.
    """
    url = os.environ.get("DATABASE_URL_PIPELINE")
    owner_url = os.environ.get("DATABASE_URL_OWNER")
    if not url or not owner_url:
        pytest.fail("DATABASE_URL_PIPELINE and DATABASE_URL_OWNER must be set; run 'make up'")

    with psycopg.connect(url) as connection:
        yield connection
        connection.rollback()

    with psycopg.connect(owner_url, autocommit=True) as owner:
        owner.execute("delete from fx_rates where source = %s", (TEST_PUBLISHER,))


@pytest.fixture
def config(tmp_path):
    document = yaml.safe_load(open("config/fx.yaml", encoding="utf-8"))
    document["publisher"]["id"] = TEST_PUBLISHER
    path = tmp_path / "fx.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return load(path)


def _rates(day: date) -> list[Rate]:
    return [
        Rate(currency="USD", rate_date=day, uah_per_unit=Decimal("44.5483")),
        Rate(currency="EUR", rate_date=day, uah_per_unit=Decimal("51.6386")),
    ]


def test_storing_the_same_day_twice_writes_nothing_the_second_time(conn, config):
    """A stamped figure has to stay reproducible, so a day already held is not revised."""
    day = date(2026, 9, 14)

    assert store(conn, _rates(day), config) == 2
    assert store(conn, _rates(day), config) == 0


def test_the_newest_day_is_the_one_read_back(conn, config):
    store(conn, _rates(date(2026, 9, 11)), config)
    store(conn, _rates(date(2026, 9, 14)), config)

    table = latest(conn, config, today=date(2026, 9, 14))

    assert table.rate_date == date(2026, 9, 14)
    assert table.base == "UAH"
    assert table.uah_per_unit["USD"] == Decimal("44.548300")


def test_rates_past_the_tolerance_raise_rather_than_stamping_a_stale_figure(conn, config):
    """The failure that does not announce itself is the one worth raising on."""
    day = date(2026, 9, 14)
    store(conn, _rates(day), config)
    too_late = day + timedelta(days=config.max_rate_age_days + 1)

    with pytest.raises(StaleRatesError, match="past the"):
        latest(conn, config, today=too_late)


def test_the_edge_of_the_tolerance_is_still_usable(conn, config):
    day = date(2026, 9, 14)
    store(conn, _rates(day), config)

    table = latest(conn, config, today=day + timedelta(days=config.max_rate_age_days))

    assert table.rate_date == day


def test_no_rates_at_all_says_what_to_run(conn, config):
    with pytest.raises(StaleRatesError, match="monitor fx"):
        latest(conn, config, today=date(2026, 9, 14))


def test_a_rate_for_a_day_that_has_not_happened_is_never_the_newest(conn, config):
    """NBU publishes tomorrow's rate this afternoon. Held, but not chosen until tomorrow.

    Before this rule `latest` picked the calendar-newest row and `monitor status`
    read "-2 days old"; a candidate staged on a Saturday would have carried
    Monday's rate.
    """
    store(conn, _rates(date(2026, 9, 12)), config)
    store(conn, _rates(date(2026, 9, 14)), config)

    assert latest(conn, config, today=date(2026, 9, 12)).rate_date == date(2026, 9, 12)
    assert latest(conn, config, today=date(2026, 9, 13)).rate_date == date(2026, 9, 12)
    assert latest(conn, config, today=date(2026, 9, 14)).rate_date == date(2026, 9, 14)


def test_only_future_rows_held_reads_as_no_rates_at_all(conn, config):
    store(conn, _rates(date(2026, 9, 14)), config)

    with pytest.raises(StaleRatesError, match="monitor fx"):
        latest(conn, config, today=date(2026, 9, 12))
