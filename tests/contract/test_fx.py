"""The rate publisher's contract, against a response recorded from a real call.

`tests/contract/fixtures/nbu.json` is the National Bank of Ukraine's own
`/NBUStatService/v1/statdirectory/exchange?json` for 2026-09-14, saved on
2026-09-12. Everything here is measured against that file rather than against
documentation, per CLAUDE.md.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from monitor.fx.config import load
from monitor.fx.convert import RateTable, convert, units_per_usd
from monitor.fx.nbu import FxFetchError, parse

RECORDED_DAY = date(2026, 9, 14)

FIXTURE = Path(__file__).parent / "fixtures" / "nbu.json"


@pytest.fixture
def document():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def config():
    return load()


@pytest.fixture
def table(document, config):
    rates = parse(document, config, asked_for=RECORDED_DAY)
    return RateTable(
        rate_date=rates[0].rate_date,
        base=config.publisher.base_currency,
        uah_per_unit={rate.currency: rate.uah_per_unit for rate in rates},
    )


# --- the feed's shape --------------------------------------------------------


def test_the_recorded_day_parses_into_every_row_it_carries(document, config):
    rates = parse(document, config, asked_for=RECORDED_DAY)

    assert len(rates) == len(document) == 45
    assert {rate.rate_date for rate in rates} == {date(2026, 9, 14)}


def test_the_date_is_read_by_the_publishers_own_format_not_iso(document, config):
    """`exchangedate` is DD.MM.YYYY. Read as ISO it would raise, or worse, not."""
    assert document[0]["exchangedate"] == "14.09.2026"

    assert parse(document, config, asked_for=RECORDED_DAY)[0].rate_date == date(2026, 9, 14)


def test_rates_are_decimals_read_through_str_rather_than_float(document, config):
    """A float round trip puts binary error into a figure that reaches a record."""
    rates = {rate.currency: rate.uah_per_unit for rate in parse(document, config, asked_for=RECORDED_DAY)}

    assert rates["USD"] == Decimal("44.5483")
    assert all(isinstance(rate, Decimal) for rate in rates.values())


def test_the_base_currency_is_absent_from_the_feed_by_design(table, config):
    """NBU quotes everything against UAH, so there is no UAH row to look up."""
    assert config.publisher.base_currency == "UAH"
    assert "UAH" not in table.uah_per_unit


def test_metals_and_the_imf_unit_are_carried_like_any_other_code(table):
    """XAU, XAG, XPT, XPD and XDR are ISO 4217 codes, not malformed rows."""
    assert {"XAU", "XAG", "XPT", "XPD", "XDR"} <= set(table.uah_per_unit)


# --- what raises, and what does not ------------------------------------------


def test_an_empty_day_raises_rather_than_reporting_a_healthy_zero(config):
    """Rule 4: zero-yield on a source that normally yields is a failure state."""
    with pytest.raises(FxFetchError, match="fewer than"):
        parse([], config, asked_for=RECORDED_DAY)


def test_a_day_with_no_target_currency_raises(document, config):
    without_usd = [row for row in document if row["cc"] != "USD"]

    with pytest.raises(FxFetchError, match="no USD row"):
        parse(without_usd, config, asked_for=RECORDED_DAY)


def test_a_day_with_no_euro_leg_raises_because_the_pegs_need_it(document, config):
    without_eur = [row for row in document if row["cc"] != "EUR"]

    with pytest.raises(FxFetchError, match="no EUR row"):
        parse(without_eur, config, asked_for=RECORDED_DAY)


def test_a_non_positive_rate_raises(document, config):
    broken = [dict(row, rate=0) if row["cc"] == "PLN" else row for row in document]

    with pytest.raises(FxFetchError, match="not a rate"):
        parse(broken, config, asked_for=RECORDED_DAY)


def test_two_dates_in_one_response_raise(document, config):
    mixed = [dict(row, exchangedate="13.09.2026") if row["cc"] == "PLN" else row for row in document]

    with pytest.raises(FxFetchError, match="different dates"):
        parse(mixed, config, asked_for=RECORDED_DAY)


def test_a_row_missing_a_field_names_the_field(document, config):
    stripped = [{k: v for k, v in row.items() if k != "rate"} if row["cc"] == "PLN" else row for row in document]

    with pytest.raises(FxFetchError, match="missing rate"):
        parse(stripped, config, asked_for=RECORDED_DAY)


# --- the arithmetic ----------------------------------------------------------


def test_the_target_currency_converts_through_the_identity_rate(table, config):
    assert units_per_usd("USD", table, config) == Decimal("1.000000")

    converted = convert(Decimal("10625.00"), "USD", table, config)
    assert converted.amount == 10625


def test_the_base_currency_uses_the_target_row_rather_than_a_missing_lookup(table, config):
    """UAH has no row of its own; the USD row already says how many UAH make a dollar."""
    assert units_per_usd("UAH", table, config) == Decimal("44.548300")

    # A real Prozorro figure from the corpus.
    assert convert(Decimal("4428444.00"), "UAH", table, config).amount == 99408


def test_a_pegged_currency_goes_through_the_euro_leg_and_the_treaty_rate(table, config):
    """XOF does not float, so there is no market rate to publish and none is used."""
    assert config.pegged_to_eur["XOF"] == Decimal("655.957")

    rate = units_per_usd("XOF", table, config)
    assert rate == Decimal("565.890036")
    assert convert(Decimal("100000000.00"), "XOF", table, config).amount == 176713


def test_a_currency_with_no_rate_and_no_peg_yields_no_figure(table, config):
    """NGN, GHS, GMD, SLE and MRU. A documented absence, not an error."""
    for currency in ("NGN", "GHS", "GMD", "SLE", "MRU"):
        assert units_per_usd(currency, table, config) is None
        assert convert(Decimal("5000000.00"), currency, table, config) is None


def test_the_stored_rate_reproduces_the_stored_amount_exactly(table, config):
    """The whole reason the rate is stored beside the figure (migration 013).

    The rate is rounded to the column's scale BEFORE the division, so a reviewer
    who multiplies it out gets the number on the record back rather than one a few
    dollars away from it.
    """
    for amount, currency in [
        (Decimal("4428444.00"), "UAH"),
        (Decimal("88650000.00"), "EUR"),
        (Decimal("3944100000.00"), "PLN"),
        (Decimal("100000000.00"), "XOF"),
        (Decimal("1.00"), "GBP"),
    ]:
        converted = convert(amount, currency, table, config)
        reproduced = (amount / converted.rate).quantize(Decimal("1"), rounding=config.rounding)

        assert int(reproduced) == converted.amount, currency


def test_the_euro_cross_rate_agrees_with_the_ecbs_own_published_figure(table):
    """Why one publisher is enough: its cross rates are not idiosyncratic.

    The ECB's published EUR/USD reference rate for 2026-09-11 is 1.1592. NBU's two
    legs give the same number to four decimal places, which is what made it safe to
    drop the ECB rather than run two rate sources and have to pick a winner for the
    currencies both of them quote (rule 1).
    """
    cross = table.uah_per_unit["EUR"] / table.uah_per_unit["USD"]

    assert cross.quantize(Decimal("0.0001")) == Decimal("1.1592")


def test_a_non_positive_amount_never_reaches_a_conversion(table, config):
    """The normalisers drop it; this is the backstop that says so out loud."""
    for amount in (Decimal("0.00"), Decimal("-1.00")):
        with pytest.raises(ValueError, match="not an amount"):
            convert(amount, "EUR", table, config)


def test_a_table_with_no_target_row_refuses_to_convert_anything(config):
    """Distinguished from "this one currency is uncovered": the table is unusable."""
    broken = RateTable(rate_date=date(2026, 9, 14), base="UAH", uah_per_unit={"EUR": Decimal("51.6386")})

    with pytest.raises(ValueError, match="no USD row"):
        units_per_usd("EUR", broken, config)


def test_a_response_for_a_day_other_than_the_one_asked_for_is_refused(document, config):
    """NBU sets tomorrow's rate the afternoon before, and from then on the undated
    endpoint serves it: this very fixture was fetched on the 12th and is dated the
    14th. The client asks for a named day and refuses any other, so a candidate is
    never stamped with a rate from a day that has not happened."""
    with pytest.raises(FxFetchError, match="asked for 2026-09-12 and the response is dated 2026-09-14"):
        parse(document, config, asked_for=date(2026, 9, 12))


def test_a_row_with_a_key_the_feed_has_never_had_is_refused(document, config):
    """Rule 4: unknown fields raise. A new key is the feed changing shape."""
    changed = [dict(row, surprise=1) if row["cc"] == "PLN" else row for row in document]

    with pytest.raises(FxFetchError, match="surprise"):
        parse(changed, config, asked_for=RECORDED_DAY)


def test_the_request_url_carries_the_day_asked_for(config):
    """The dated endpoint, never the undated one (config/fx.yaml explains why)."""
    assert "{date}" in config.publisher.url
    assert config.publisher.url.format(date=date(2026, 9, 12).strftime(config.publisher.query_date_format)).endswith(
        "?date=20260912&json"
    )


# --- the dated endpoint, recorded rather than remembered -----------------------
#
# `nbu_dated_20260912.json` and `nbu_dated_20260913.json` are the DATED endpoint's
# responses for a Friday and the Saturday after it, fetched 2026-09-12. They are
# what the client's date-refusal rule and config/fx.yaml's weekend claim rest on.

DATED = {
    day: Path(__file__).parent / "fixtures" / f"nbu_dated_{day.strftime('%Y%m%d')}.json"
    for day in (date(2026, 9, 12), date(2026, 9, 13))
}


def _usd(rows):
    return next(Decimal(str(row["rate"])) for row in rows if row["cc"] == "USD")


def test_the_dated_endpoint_returns_the_day_asked_for(config):
    for day, path in DATED.items():
        rates = parse(json.loads(path.read_text(encoding="utf-8")), config, asked_for=day)
        assert {rate.rate_date for rate in rates} == {day}
        assert len(rates) == 45


def test_a_weekend_day_carries_the_friday_rate_under_its_own_date(config):
    """The measurement behind config/fx.yaml: no market on Saturday, so Saturday's
    row IS Friday's number, stamped Saturday. `latest` would select it for a
    Saturday staging pass, and a candidate staged then is stamped with the day it
    was staged, not the last banking day."""
    friday = json.loads(DATED[date(2026, 9, 12)].read_text(encoding="utf-8"))
    saturday = json.loads(DATED[date(2026, 9, 13)].read_text(encoding="utf-8"))

    assert _usd(saturday) == _usd(friday)


def test_the_undated_endpoint_really_was_forward_dated(config):
    """Recorded on the 12th, the undated response is dated the 14th and carries a
    DIFFERENT USD rate from the dated 12th - Monday's number, set Friday afternoon.
    This is the defect the dated endpoint exists to avoid."""
    undated = json.loads(FIXTURE.read_text(encoding="utf-8"))
    friday = json.loads(DATED[date(2026, 9, 12)].read_text(encoding="utf-8"))

    assert {row["exchangedate"] for row in undated} == {"14.09.2026"}
    assert _usd(undated) != _usd(friday)
    with pytest.raises(FxFetchError, match="asked for 2026-09-12"):
        parse(undated, config, asked_for=date(2026, 9, 12))
