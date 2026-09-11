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
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
    estimated_value_usd: int | None
    eligibility_flags: tuple[str, ...]
    deadline_at: date | None
    finance_project_id: str = ""


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

    donors = [source for source in sources if source.is_donor]
    products, gaps = products_and_gaps(candidate.matched_function_ids, function_map)
    funding = funding_source(sources, defaults)
    value = candidate.estimated_value_usd

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
        "Pricing Notes": pricing_note(funding, candidate.finance_project_id, value, sentences),
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
        "Currency": suggested["currency"],
        "Deal Tier": deal_tier(candidate.score, defaults),
        "Delivery Model": (
            suggested["delivery_model_partner_supported"] if donors else suggested["delivery_model_default"]
        ),
        "Customer Type": suggested["customer_type_default"],
        "Eligibility Requirements Met?": "Yes" if not candidate.eligibility_flags else "Review needed",
        "Partner Required?": "Yes" if donors else placeholders["unknown"],
        "Expected Release of RFP/EOI?": deadline_for(candidate, "pipeline", placeholders),
        "Do we need to register our interest?": register_interest(candidate.eligibility_flags, defaults),
        "Total Opportunity Amount": value if value is not None else placeholders["zero"],
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


def pricing_note(funding: str, project_id: str, value: int | None, sentences: dict) -> str:
    return sentences["pricing_notes"].format(
        funding_source=funding,
        project_id=f", project {project_id}" if project_id else "",
        value=f"USD {value:,}" if value is not None else "not stated",
    )


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
