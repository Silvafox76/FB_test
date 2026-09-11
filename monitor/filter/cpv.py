"""The free filter's first stage: CPV codes.

A notice carrying CPV codes none of which start with a pass prefix is dropped
before any model call. That is the whole point of this stage: it costs nothing and
it removes the road-building and catering tenders that make up most of TED.

Two rules worth stating because they are asymmetric:

  - Codes present, none passing -> dropped. The buyer classified the notice and
    said it is not software, IT services or consultancy.
  - No codes at all -> not dropped. Absence is not a failed match; it means the
    source did not classify, and the lexicon stage gets its turn.

The prefixes come from `config/thresholds.yaml` (rule 6). Nothing here knows that
they are 48, 72 and 79.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CpvVerdict:
    """Whether the CPV stage passes a notice, and the sentence explaining why."""

    passed: bool
    reason: str
    matched_prefix: str = ""


def check(cpv_codes: list[str], pass_prefixes: list[str]) -> CpvVerdict:
    """Does this notice's classification let it through to the lexicon stage?"""
    if not cpv_codes:
        return CpvVerdict(passed=True, reason="no cpv code")

    for code in cpv_codes:
        for prefix in pass_prefixes:
            if code.startswith(prefix):
                return CpvVerdict(passed=True, reason=f"cpv {prefix}", matched_prefix=prefix)

    allowed = "/".join(pass_prefixes)
    return CpvVerdict(passed=False, reason=f"cpv {cpv_codes[0]} outside {allowed}")
