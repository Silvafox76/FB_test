"""Build the Opportunity-shaped record from a candidate. Architecture v0.4 appendix E.

`build_record` is pure: a candidate, its source cluster, the function map and the
record defaults in, a dict of 73 columns out. No database, no model call, no clock
beyond the approval date it is handed. That is what makes it testable and what
makes the export reproducible.

The three rules appendix E exists to enforce, and none of them is optional:

  - **A BD-only column gets the placeholder the CRM already shows, never a guess
    and never a blank.** 39 of the 73 columns are BD judgement. An imported Monitor
    record should read like an early record a person started, not a machine's guess
    dressed up as fact. A database NULL rendering as an empty cell is the failure
    mode this avoids: it tells the reader nothing about whether anyone looked.
  - **FreeBalance Products Required is a set.** It comes from the component map's
    New Marketecture sheet through `config/function_map.yaml`, and some functions
    map to more than one product. Product Gaps names any matched product whose
    status is not Available.
  - **Account resolution happens at import, not here.** The builder proposes the
    buyer name as text and nothing more. The pipeline never creates, guesses or
    looks up an Account link, and under D31 it has no CRM scope with which to try.

A fourth rule arrived with migration 012/013: **the published amount is the
record, and USD is a derivation stamped with the rate that produced it.** A
notice's value now travels as `estimated_value` and `value_currency`, exactly as
the publisher stated it, with `estimated_value_usd` present only when a rate
covers that currency (`value_rate`, units of `value_currency` per one USD, and
`value_rate_date`, the day the rate is from). Which of the two is honest to put
in the export's single amount column is `value_basis` in
`config/record_defaults.yaml`, and this module decides nothing on its own: it
reads that key, defaults to `published` when the key is absent, and raises on
anything else (rule 6 all the way down to a typo in the config).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from monitor.fx.config import load as load_fx
from monitor.registry.load import VALUE_BASES


@dataclass(frozen=True)
class ClusterSource:
    """One source behind a candidate, as the record builder needs it."""

    id: str
    name: str
    stream: str
    admin_level: str

    @property
    def is_donor(self) -> bool:
        """A donor-stream source is one publishing on the financier's behalf."""
        return self.admin_level == "donor"


@dataclass(frozen=True)
class RecordCandidate:
    """The candidate fields appendix E derives from. A subset, stated explicitly."""

    id: str
    title_en: str
    buyer: str
    country: str
    country_name: str
    region: str
    admin_level: str
    score: int
    summary_en: str
    matched_function_ids: tuple[str, ...]
    system_names: tuple[str, ...]
    procurement_type: str
    # The amount exactly as published, and in which currency. Both None together
    # when the notice states no value (the DB enforces that pairing; this module
    # trusts it rather than re-checking it).
    estimated_value: Decimal | int | None
    value_currency: str | None
    # Derived at staging: None exactly when no fx_rates row covers value_currency,
    # which is a documented absence and not an error.
    estimated_value_usd: int | None
    # Units of value_currency per ONE USD. estimated_value / value_rate reproduces
    # estimated_value_usd exactly, which is why it is carried at all: a reviewer
    # can check the arithmetic instead of trusting it.
    value_rate: Decimal | float | None
    value_rate_date: date | None
    eligibility_flags: tuple[str, ...]
    deadline_at: date | None
    finance_project_id: str = ""
    # What the source published about a value that carries no procedure total
    # (BOAMP's lot-only notices; migration 016). Empty whenever a total exists or
    # the notice states no value at all. Read verbatim, never reworded here: the
    # sentence itself is the source's own phrasing, composed in the normaliser
    # from `sources/<id>.yaml`'s `value_note` (rule 6), and this module's job is
    # only to decide where it is honest to show it in place of "not stated".
    value_note: str = ""


class RecordDefaultsError(ValueError):
    """`config/record_defaults.yaml` holds a value this module does not recognise."""


def build_record(
    candidate: RecordCandidate,
    sources: list[ClusterSource],
    function_map: list[dict],
    defaults: dict,
    *,
    reviewer: str,
    approved_on: date,
) -> dict:
    """Every appendix E column, in order, for one candidate."""
    placeholders = defaults["placeholders"]
    suggested = defaults["suggested"]
    sentences = defaults["sentences"]

    value_basis = defaults.get("value_basis", "published")
    if value_basis not in VALUE_BASES:
        raise RecordDefaultsError(
            f"config/record_defaults.yaml: value_basis is {value_basis!r}, must be one of {sorted(VALUE_BASES)}"
        )

    donors = [source for source in sources if source.is_donor]
    products, gaps = products_and_gaps(candidate.matched_function_ids, function_map)
    funding = funding_source(sources, defaults)
    currency, amount = value_for_basis(candidate, value_basis, suggested, placeholders)
    pricing_value = pricing_value_note(candidate, value_basis, sentences)

    derived = {
        "Opportunity Name": candidate.title_en,
        "Finance Project ID": candidate.finance_project_id or "",
        "Level of Government": defaults["level_of_government"].get(
            candidate.admin_level, defaults["level_of_government"]["national"]
        ),
        "Partners Involved": ", ".join(source.name for source in donors),
        "Industry": defaults["industry_by_region"].get(candidate.region, defaults["industry_by_region"]["default"]),
        "Shipping Country": candidate.country_name,
        "Funding Source": funding,
        "Eligibility Requirements Comments": eligibility_comment(candidate.eligibility_flags, sentences),
        "Partner Required Comments": ", ".join(source.name for source in donors),
        "Pricing Notes": pricing_note(funding, candidate.finance_project_id, pricing_value, sentences),
        "FreeBalance Products Required": "; ".join(products),
        "Product Gaps": "; ".join(gaps),
        # Zoho sets Created By to the import operator. The reviewer's name travels in
        # Next Steps and in the audit trail, which is where it belongs: the person
        # who approved is not the person who imported.
        "Created By": "",
        "Next Steps": sentences["next_steps"].format(reviewer=reviewer, date=approved_on.isoformat()),
        "monitor_candidate_id": candidate.id,
        # Stamped by the export at step 11, not here.
        "monitor_export_batch": "",
        "Proposal Due or Submitted": deadline_for(candidate, "tender", placeholders),
    }

    suggestions = {
        "Stage": placeholders["tbd"],
        "Account Name": sentences["account_name_proposal"].format(buyer=candidate.buyer or "unknown buyer"),
        "Is this a Partner or Reseller-led opportunity?": "Yes" if donors else suggested["partner_led_default"],
        "Proposal Type": suggested["proposal_type_default"],
        "Lead Source": suggested["lead_source"],
        "Pipeline": suggested["pipeline"],
        "Currency": currency,
        "Deal Tier": deal_tier(candidate.score, defaults),
        "Delivery Model": (
            suggested["delivery_model_partner_supported"] if donors else suggested["delivery_model_default"]
        ),
        "Customer Type": suggested["customer_type_default"],
        "Eligibility Requirements Met?": "Yes" if not candidate.eligibility_flags else "Review needed",
        "Partner Required?": "Yes" if donors else placeholders["unknown"],
        "Expected Release of RFP/EOI?": deadline_for(candidate, "pipeline", placeholders),
        "Do we need to register our interest?": register_interest(candidate.eligibility_flags, defaults),
        "Total Opportunity Amount": amount,
        "Probability (%)": suggested["probability_pct"],
        "Standard or Custom Product Required?": suggested["standard_or_custom"],
    }

    record: dict = {}
    for column in defaults["columns"]:
        name, category = column["name"], column["category"]
        if category == "D":
            record[name] = derived.get(name, "")
        elif category == "S":
            record[name] = suggestions.get(name, placeholders["tbd"])
        else:
            record[name] = bd_placeholder(name, placeholders)
    return record


# BD-only columns whose placeholder is not "TBD". Appendix E names each one.
DASH_COLUMNS = frozenset(
    {
        "Proposal Documents Received",
        "Format of Bid Bond",
        "Tax Amount",
        "Hardware/Hosting",
        "Available Budget",
    }
)
UNKNOWN_COLUMNS = frozenset({"Legal Support Required?", "Is Bid Bond Required"})
ZERO_COLUMNS = frozenset(
    {
        "Licenses",
        "Implementation Services",
        "Software Maintenance",
        "Academy-Training",
        "Sustainability-Help Desk",
        "Third Party Licenses",
        "FreeBalance Amount",
    }
)


def bd_placeholder(column: str, placeholders: dict) -> str | int:
    """The placeholder this BD column shows in the CRM when nobody has filled it in."""
    if column in ZERO_COLUMNS:
        return placeholders["zero"]
    if column in DASH_COLUMNS:
        return placeholders["dash"]
    if column in UNKNOWN_COLUMNS:
        return placeholders["unknown"]
    return placeholders["tbd"]


def products_and_gaps(function_ids: tuple[str, ...], function_map: list[dict]) -> tuple[list[str], list[str]]:
    """The products the matched functions need, and the ones that are not Available.

    A set, not a string: some functions map to more than one product. Pillar 8 has
    no product mapping in component map 4.2, so a candidate matching only those
    functions yields no products and no gaps, which is the honest answer rather
    than a placeholder.
    """
    by_id = {function["function_id"]: function for function in function_map}
    products: dict[str, str] = {}

    for function_id in function_ids:
        function = by_id.get(function_id)
        if not function:
            continue
        for product, status in (function.get("product_status") or {}).items():
            # Best status wins, the same rule the component map export uses: if any
            # matched function has the product Available, it is not a gap.
            if product not in products or status == "Available":
                products[product] = status

    gaps = [f"{product} ({status})" for product, status in sorted(products.items()) if status != "Available"]
    return sorted(products), gaps


def funding_source(sources: list[ClusterSource], defaults: dict) -> str:
    """The donor stream behind the candidate, or a government's own budget."""
    by_stream = defaults["funding_source_by_stream"]
    for source in sources:
        if source.is_donor and source.id in by_stream:
            return by_stream[source.id]
    return by_stream["default"]


def eligibility_comment(flags: tuple[str, ...], sentences: dict) -> str:
    if not flags:
        return sentences["eligibility_none_detected"]
    return "Detected in the notice text: " + ", ".join(flag.replace("_", " ") for flag in flags) + "."


def pricing_note(funding: str, project_id: str, value_note: str, sentences: dict) -> str:
    return sentences["pricing_notes"].format(
        funding_source=funding,
        project_id=f", project {project_id}" if project_id else "",
        value=value_note,
    )


def value_narrative(
    currency: str | None,
    amount: Decimal | int | None,
    usd: int | None,
    rate: Decimal | float | None,
    rate_date: date | None,
    sentences: dict,
    note: str = "",
) -> str:
    """The one honest sentence describing a value, wherever it is shown.

    Five cases, matched to what a reviewer must be able to tell apart at a
    glance: no value in the notice at all and nothing else published about it
    either; no procedure total but a per-source note on what was published
    instead (BOAMP's lot-only notices, `note`, carried verbatim rather than
    reworded here — the sentence is the source's own phrasing, not this
    module's); a value published in the fx target currency itself
    (`monitor/fx/config.py`'s `target_currency`), which converts through the
    identity rate and so has nothing to derive — "USD 4,200,000 ≈ USD
    4,200,000 at 1.0000 USD/USD" is the same number twice with an equation in
    between, not information; a value with no USD figure because no
    `fx_rates` row covers the currency (a documented absence, not an error);
    and a value in another currency with the USD figure and the exact rate
    that produced it, so a reviewer can check the arithmetic rather than trust
    it. The candidate page, the queue list and (through `pricing_value_note`)
    the export's Pricing Notes all read this, so the five cannot say
    different things about the same candidate (rule 1). Never "USD" as a
    literal anywhere else: the currency always comes from the row, and the
    target to compare it against always comes from `config/fx.yaml` (rule 6).
    """
    if amount is None:
        return note if note else sentences["value_not_stated"]
    if usd is None:
        return sentences["value_no_usd"].format(currency=currency, amount=amount)
    if currency == load_fx().target_currency:
        return sentences["value_in_target"].format(currency=currency, amount=amount)
    return sentences["value_with_usd"].format(
        currency=currency,
        amount=amount,
        usd=usd,
        rate=rate,
        rate_date=rate_date.isoformat() if rate_date is not None else "",
    )


def pricing_value_note(candidate: RecordCandidate, value_basis: str, sentences: dict) -> str:
    """What Pricing Notes says about the value, matching the `value_basis` mapping decision.

    `published` (the default): the derivation used in `value_narrative` — the
    USD equivalent and its rate when one exists, or a plain statement that no
    rate covers the currency, or "not stated". `usd`: the export's amount column
    already holds the converted figure, so this carries the published amount and
    its rate instead — unless there is no USD figure to convert with, in which
    case there is nothing to hold back and this reads exactly as `published`
    would, which is also what keeps `usd` from ever implying "USD 0" for a
    contract that has a real, just un-convertible, value. And unless the notice
    was published in the fx target currency itself, in which case the amount
    column already holds exactly what was published — there is no conversion to
    hold back, and this falls through to `value_narrative`'s plain identity
    sentence rather than "published as USD 4,200,000 at 1.0000 USD/USD", which
    would say nothing `pricing_notes` doesn't already say once. And unless there
    is no procedure total but `candidate.value_note` says what was published
    instead (BOAMP's lot-only notices), in which case `estimated_value` is None
    so neither branch above ever fires and this falls through to
    `value_narrative`, which reads the note verbatim in place of "not stated" —
    `value_basis` has nothing to decide there, because there is no total to
    choose a currency for.
    """
    identity = candidate.value_currency == load_fx().target_currency
    if (
        value_basis == "usd"
        and not identity
        and candidate.estimated_value is not None
        and candidate.estimated_value_usd is not None
    ):
        return sentences["value_usd_basis"].format(
            currency=candidate.value_currency,
            amount=candidate.estimated_value,
            rate=candidate.value_rate,
            rate_date=candidate.value_rate_date.isoformat() if candidate.value_rate_date is not None else "",
        )
    return value_narrative(
        candidate.value_currency,
        candidate.estimated_value,
        candidate.estimated_value_usd,
        candidate.value_rate,
        candidate.value_rate_date,
        sentences,
        note=candidate.value_note,
    )


def value_for_basis(
    candidate: RecordCandidate, value_basis: str, suggested: dict, placeholders: dict
) -> tuple[str, Decimal | int]:
    """The (Currency, Total Opportunity Amount) pair appendix E's mapping decision produces.

    `published`: the amount and currency exactly as stated (rule 9, all the way
    to the CSV). `usd`: the converted figure with Currency "USD" — unless no
    rate covers the currency, in which case there is nothing to convert and this
    falls back to `published` rather than inventing a number or writing "USD 0"
    for a contract whose value is real but not convertible today.
    """
    if candidate.estimated_value is None:
        return suggested["currency"], placeholders["zero"]
    if value_basis == "usd" and candidate.estimated_value_usd is not None:
        return load_fx().target_currency, candidate.estimated_value_usd
    return candidate.value_currency, candidate.estimated_value


def deal_tier(score: int, defaults: dict) -> str:
    tiers = defaults["deal_tier"]
    return tiers["tier_1"] if score >= tiers["tier_1_min_score"] else tiers["tier_2"]


def register_interest(flags: tuple[str, ...], defaults: dict) -> str:
    triggers = set(defaults["register_interest_flags"])
    if set(flags) & triggers:
        return "Yes"
    return defaults["placeholders"]["unknown"]


def deadline_for(candidate: RecordCandidate, kind: str, placeholders: dict) -> str:
    """Appendix E splits the deadline by what the candidate is.

    A donor pipeline entry that is not yet a tender (`procurement_type` "other")
    has an expected release date; an already-published tender has a submission
    date. The same date, in the column that means the right thing.
    """
    if candidate.deadline_at is None:
        return placeholders["tbd"]
    is_pipeline = candidate.procurement_type == "other"
    wanted = "pipeline" if is_pipeline else "tender"
    return candidate.deadline_at.isoformat() if kind == wanted else placeholders["tbd"]
