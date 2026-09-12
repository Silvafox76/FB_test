"""One rule for reading a published amount, shared by every source that states one.

Each normaliser finds the amount and the currency in its own source's shape - that
part is source-specific and stays in the source's own module (rule 5). What is
common is what counts as a price, and that was worth writing once: the guard
started life in `liberia.py` for three OCDS releases carrying `0`, and then TED
turned out to publish 19 of them too.

WHAT IS NOT A PRICE:
  - no amount, or no currency. A bare number with no currency cannot be converted
    or displayed honestly, and a currency with no number is nothing at all.
  - zero or less. Publishers use 0 for "not stated": Liberia on 3 of 14 releases
    ("Construction of Two District Offices", "FY2026 Procurement of Transport
    Equipment", "Procurement of Office Equipment" - none of which costs nothing),
    and TED on 19 of 404. Carried through, a zero becomes USD 0 on a CRM record
    for a building, which is exactly the plausible-looking wrong value CLAUDE.md's
    export rules exist to prevent.

A currency that is not a three-letter alphabetic code IS an error rather than a
quiet drop: every source checked states ISO 4217, so a different shape is the
source changing rather than a notice being silent (rule 4).

Note on VAT: sources differ on whether a stated amount includes it - Prozorro
carries `valueAddedTaxIncluded` and it is False on 130 and True on 70 of the 200
sampled - and nothing here adjusts for that. The published number is the record
(rule 9); a VAT-normalised figure would be this module's own invention.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

CURRENCY_LENGTH = 3


def published_value(amount: object, currency: object, *, source_id: str) -> tuple[Decimal | None, str | None]:
    """The amount and currency as published, or (None, None) when none is stated."""
    if amount is None or currency is None:
        return None, None

    code = str(currency).strip().upper()
    if not code:
        return None, None
    if len(code) != CURRENCY_LENGTH or not code.isalpha():
        raise ValueError(f"{source_id}: {currency!r} is not an ISO 4217 alpha-3 currency code")

    # str() first: sources publish the amount as a JSON string (TED, on all 404) or
    # as a JSON number (Prozorro, Liberia), and going through float would put
    # binary rounding error into a figure that ends up on a record.
    try:
        stated = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError) as cause:
        raise ValueError(f"{source_id}: {amount!r} is not a number") from cause

    if stated <= 0:
        return None, None

    # The column is numeric(18, 2) and a minor unit is real money in every currency
    # here; anything finer than that is noise a publisher did not mean.
    return stated.quantize(Decimal("0.01")), code
