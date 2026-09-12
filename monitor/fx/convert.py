"""The conversion. Pure arithmetic over a rate table; no I/O, no database, no clock.

Everything policy-shaped lives in `config/fx.yaml` and arrives as `FxConfig`
(rule 6). What is here is the arithmetic and the three currency KINDS the
publisher's table implies. Those are not three attempts at one conversion, which
rule 1 would forbid - they are three different relationships to the publisher's
base, each with exactly one correct formula:

  the base itself   the publisher quotes every other currency against UAH, so
                    there is no UAH row to look up; the USD row already says how
                    many UAH make a dollar and that IS the answer.
  a pegged currency XOF and XAF have no market rate to publish because they do
                    not float. The peg is a treaty constant in config, and the
                    conversion goes through the publisher's own EUR leg, so there
                    is still one rate publisher and no second source.
  everything else   a direct row in the table.

A currency in none of the three gets no figure. That is a documented absence, not
a failure: the record still shows the amount exactly as the publisher stated it,
and NGN, GHS, GMD, SLE and MRU sit here today.

THE RATE IS STORED IN THE DIRECTION THAT REPRODUCES THE ANSWER. `units_per_usd`
is rounded to the scale the column holds BEFORE the amount is divided by it, so
`estimated_value / value_rate` on the stored row returns `estimated_value_usd`
exactly. Computing at full precision and storing a rounded rate would put a
figure on the record that its own stated rate does not produce, which is the
unattributable number this whole change exists to remove (migration 013).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from monitor.fx.config import FxConfig

# The scale of `candidates.value_rate`, migration 012. Named rather than inlined
# so the rounding here and the column cannot drift apart silently.
RATE_SCALE = Decimal("0.000001")
WHOLE = Decimal("1")


@dataclass(frozen=True)
class RateTable:
    """One publisher's day, as published: UAH per one unit of each currency.

    The base currency is deliberately absent from `uah_per_unit` - the publisher
    does not quote its own currency against itself - and `base` names it so the
    conversion does not have to infer it from a missing key.
    """

    rate_date: date
    base: str
    uah_per_unit: dict[str, Decimal]


@dataclass(frozen=True)
class Converted:
    """A figure, the rate that produced it, and the day that rate is from."""

    amount: int
    rate: Decimal
    rate_date: date


def units_per_usd(currency: str, table: RateTable, config: FxConfig) -> Decimal | None:
    """How many units of `currency` make one USD, or None if nothing covers it.

    Raises rather than returning None when the TABLE is broken rather than merely
    silent about one currency: a table with no row for the target currency cannot
    convert anything at all, and a peg that needs the EUR leg cannot use a table
    that has no EUR row (rule 4).
    """
    target = config.target_currency
    if target not in table.uah_per_unit:
        raise ValueError(f"rate table for {table.rate_date} has no {target} row, so nothing can be converted")

    per_target = table.uah_per_unit[target]

    if currency == table.base:
        # The publisher's target row already says how many base units make one
        # target unit. No lookup, and no division by a row that does not exist.
        rate = per_target
    elif currency in config.pegged_to_eur:
        if "EUR" not in table.uah_per_unit:
            raise ValueError(
                f"rate table for {table.rate_date} has no EUR row, so {currency} cannot be pegged through it"
            )
        # units per EUR, times EUR per target unit.
        rate = config.pegged_to_eur[currency] * (per_target / table.uah_per_unit["EUR"])
    elif currency in table.uah_per_unit:
        rate = per_target / table.uah_per_unit[currency]
    else:
        return None

    quantised = rate.quantize(RATE_SCALE)
    if quantised <= 0:
        # Only reachable if a currency is worth so little that its rate underflows
        # the column's scale. Better to say so than to divide by zero downstream.
        raise ValueError(f"{currency} per {target} is {rate}, which does not fit {RATE_SCALE} without becoming zero")
    return quantised


def convert(amount: Decimal, currency: str, table: RateTable, config: FxConfig) -> Converted | None:
    """One published amount to the target currency, or None when no rate covers it."""
    if amount <= 0:
        # A non-positive amount is a publisher's "not stated", never a price. The
        # normalisers drop it before it reaches a Notice; this is the backstop that
        # keeps a zero from being dressed up as a converted figure.
        raise ValueError(f"{amount} is not an amount to convert")

    rate = units_per_usd(currency, table, config)
    if rate is None:
        return None

    converted = (amount / rate).quantize(WHOLE, rounding=config.rounding)
    return Converted(amount=int(converted), rate=rate, rate_date=table.rate_date)
