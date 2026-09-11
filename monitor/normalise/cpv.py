"""CPV codes, extracted and normalised. Extraction only.

Whether a code passes is `monitor/filter/cpv.py`'s decision at step 5. A
normaliser that also filters is a finding (rule 5), so nothing here knows which
prefixes matter.

A CPV code is eight digits and an optional check digit after a hyphen
(`48000000-8`). Sources write them with and without the check digit, sometimes
padded short, sometimes several to a field. The stored form is the eight digit
code without the check digit, because that is what a prefix test reads and what
two sources describing the same notice will agree on.
"""

from __future__ import annotations

import re

# Eight digits, optionally followed by a hyphen and the single check digit.
CPV_PATTERN = re.compile(r"\b(\d{8})(?:-\d)?\b")


def normalise_code(value: str) -> str:
    """'48000000-8' -> '48000000'. Raises on anything that is not a CPV code."""
    match = CPV_PATTERN.fullmatch(value.strip())
    if not match:
        raise ValueError(f"not a CPV code: {value!r}")
    return match.group(1)


def extract_codes(*values: str) -> list[str]:
    """Every CPV code in the given strings, normalised, de-duplicated, in order seen.

    Takes several strings because sources split the main code and the additional
    ones across separate fields.
    """
    seen: set[str] = set()
    codes: list[str] = []
    for value in values:
        if not value:
            continue
        for match in CPV_PATTERN.finditer(value):
            code = match.group(1)
            if code not in seen:
                seen.add(code)
                codes.append(code)
    return codes
