"""The scoring system prompt, built from config and cached.

Architecture v0.4 section 8 says what goes in it: the rubric, the 33 functions with
their type weights, both lexicons, the system-name list, eligibility rules, the
geography weights and the output schema. Roughly 7,500 tokens, paid once per cache
window and then at a tenth.

Nothing here is written in Python that belongs in YAML (rule 6). The functions come
from `config/function_map.yaml`, the phrases from the two lexicons, the names from
`config/system_names.yaml` and every weight from `config/thresholds.yaml`. Change a
geography weight and the prompt changes, `prompt_version` changes with it, and a
score made before the change stays distinguishable from one made after. That is the
whole reason the version is a hash of the text rather than a number someone bumps.

The rubric is quoted from the architecture rather than invented here. It is a
description of how to weigh evidence, not a formula the model is asked to compute:
the model returns `relevance` and the rubric tells it what should move that number.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

import yaml

from monitor.registry.load import CONFIG_DIR, load_function_map, load_lexicon, load_system_names

# Architecture v0.4 section 8, "Scoring rubric (initial, to be tuned in weeks 9 to
# 14)". Quoted rather than paraphrased: this is the thing the week 9 to 14 tuning
# changes, and a paraphrase would drift from what was agreed.
RUBRIC = """You score public procurement notices for how well they match FreeBalance's public financial
management business. FreeBalance sells government financial management software: budget preparation and
execution, treasury, public accounts, payroll for the civil service, revenue administration, public
procurement systems and the reporting around them.

Score `relevance` from 0 to 100. It should rise with:

- Function fit. How squarely the notice buys or advises on one of the 33 PFM functions listed below.
  Each function carries a type weight; a notice matching a heavier function is worth more than one
  matching a lighter one. This is the largest single component.
- System signal. A notice that names an actual financial management system is a far stronger signal than
  one that describes software in the abstract. The names are listed below.
- Value band. A larger stated contract value is worth more, where one is stated. No stated value is not
  evidence of a small contract. The value is given to you in the currency the publisher used, and it is
  the publisher's own figure: weigh the band it falls in, and do not convert it or restate it.
- Geography. The per-country weights below. They are a business priority, not a judgement about the
  country.
- Donor financing. A notice financed by the World Bank, AfDB, EU, MCC, IsDB or BOAD is worth more: those
  buyers run competitive international procurements that a vendor outside the country can win.

And it should fall with:

- Eligibility barriers. A tender restricted to national suppliers, or requiring local registration or a
  consortium, is harder to win from outside.
- Notices that are about something else. Office software licences, network cabling, staff training,
  recruitment, advertising and building work are not PFM opportunities however governmental the buyer.
  A public financial management buyer purchasing something unrelated scores low.

Two things that are not relevance and must not raise it: how large or well known the buyer is, and how
well written the notice is.

Judge only from the notice text you are given. If the text does not say something, it is not evidence.
Do not infer a system name, a value or a funding source that is not written down."""

ELIGIBILITY_RULES = """Set `eligibility_flags` only for a barrier the notice text actually states:

- national_only: participation restricted to suppliers of that country.
- local_registration: a supplier must be registered locally, or hold a local licence or tax certificate,
  before bidding.
- consortium_required: bidding requires a consortium, a joint venture or a named local partner.
- tax_clearance_required: a tax clearance or similar fiscal certificate is required to bid.

An absent flag means the notice does not state that barrier, never that you checked and found none."""

PROCUREMENT_TYPES = """Set `procurement_type` to one of:

- system: buying or replacing a software system, including its implementation.
- services: implementation, integration, maintenance or support around a system.
- advisory: consultancy, technical assistance or a study, with no system purchase.
- other: anything else, including a donor pipeline entry that is not yet a tender."""


@lru_cache(maxsize=1)
def _thresholds() -> dict:
    return yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))


def _functions_block() -> str:
    """The 33 functions, each with its pillar, type weight and its own phrases."""
    english, _ = load_lexicon("en")
    french, _ = load_lexicon("fr")

    lines = []
    for function in load_function_map():
        function_id = function["function_id"]
        phrases_en = ", ".join(english.get(function_id, [])[:8])
        phrases_fr = ", ".join(french.get(function_id, [])[:6])
        lines.append(
            f"- {function_id} ({function['name']}, pillar {function['pillar']}, "
            f"type weight {function['type_weight']})\n"
            f"    en: {phrases_en}\n"
            f"    fr: {phrases_fr}"
        )
    return "\n".join(lines)


def _geography_block() -> str:
    geography = dict(_thresholds()["geography"])
    default = geography.pop("default")
    by_weight: dict[float, list[str]] = {}
    for country, weight in geography.items():
        by_weight.setdefault(weight, []).append(country)

    lines = [
        f"- {weight}: {', '.join(sorted(countries))}" for weight, countries in sorted(by_weight.items(), reverse=True)
    ]
    lines.append(f"- {default}: every other country")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """The whole cached block. Deterministic: same config, same bytes, same version."""
    return "\n\n".join(
        [
            RUBRIC,
            "The 33 PFM functions, with the phrases that signal each one:\n\n" + _functions_block(),
            "System names. A notice naming one of these is strong evidence:\n\n" + ", ".join(load_system_names()),
            "Geography weights, by country:\n\n" + _geography_block(),
            ELIGIBILITY_RULES,
            PROCUREMENT_TYPES,
            "Return your answer by calling the record_score tool. Every field is required unless its "
            "schema says otherwise. `title_en` is the notice title in English: copy it through when the "
            "notice is already English, translate it when it is not. `summary_en` is at most 120 words "
            "and says what is being bought, by whom, and why it is or is not a PFM opportunity. "
            "`matched_functions` carries the function_id and the words from the notice that made you "
            "match it, not a restatement of the function name. Do not report a contract value: "
            "the pipeline reads it from the source's own structured field and converts it at a "
            "recorded rate, so any figure you supplied would be discarded.",
        ]
    )


def prompt_version() -> str:
    """A stable id for the prompt that produced a score (rule 9)."""
    return hashlib.sha256(system_prompt().encode("utf-8")).hexdigest()[:12]


def body_budget() -> int:
    return int(_thresholds()["score_body_chars"])
