"""The record builder: Architecture v0.4 appendix E, column by column.

This is the export's contract. Zoho's import mapper matches on headers, so a
renamed or reordered column is a column someone hand-keys, and appendix E's own
words for that are "it is reported as a defect".

The case in the step's acceptance is the first test: a candidate with a matched
IFMIS function and a World Bank source in its cluster.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import yaml

from monitor.fx.config import load as load_fx
from monitor.registry.load import CONFIG_DIR, load_function_map
from monitor.stage.record import ClusterSource, RecordCandidate, RecordDefaultsError, build_record


@pytest.fixture(scope="module")
def defaults() -> dict:
    return yaml.safe_load((CONFIG_DIR / "record_defaults.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def function_map() -> list[dict]:
    return load_function_map()


@pytest.fixture(scope="module")
def ifmis_function_id(function_map) -> str:
    """2.3 Budget Execution maps to Core Financial Execution and Reporting, Available."""
    return next(f["function_id"] for f in function_map if f["name"].startswith("2.3"))


def candidate(**overrides) -> RecordCandidate:
    payload = {
        "id": "C000123",
        "title_en": "Supply and implementation of an integrated financial management system",
        "buyer": "Ministry of Finance",
        "country": "GH",
        "country_name": "Ghana",
        "region": "West Africa",
        "admin_level": "national",
        "score": 78,
        "summary_en": "Ghana's Ministry of Finance is replacing its IFMIS.",
        "matched_function_ids": (),
        "system_names": ("IFMIS", "GIFMIS"),
        "procurement_type": "system",
        # Published natively in USD, so the identity rate: this is what a
        # dollar-denominated notice actually looks like after migration 012/013,
        # and it keeps every pre-existing test's assumption ("the value is
        # 4,200,000") true under the default value_basis, "published".
        "estimated_value": Decimal("4200000.00"),
        "value_currency": "USD",
        "estimated_value_usd": 4_200_000,
        "value_rate": Decimal("1.0"),
        "value_rate_date": date(2026, 9, 1),
        "eligibility_flags": (),
        "deadline_at": date(2026, 11, 30),
        "finance_project_id": "P123456",
    }
    payload.update(overrides)
    return RecordCandidate(**payload)


WORLD_BANK = ClusterSource(id="worldbank", name="World Bank procurement notices", stream="feed", admin_level="donor")
TED = ClusterSource(id="ted", name="Tenders Electronic Daily", stream="feed", admin_level="national")


def record_for(candidate_obj, sources, function_map, defaults) -> dict:
    return build_record(
        candidate_obj, sources, function_map, defaults, reviewer="Ryan Dear", approved_on=date(2026, 9, 12)
    )


# --- the acceptance case -----------------------------------------------------


def test_an_ifmis_candidate_with_a_world_bank_source(defaults, function_map, ifmis_function_id):
    """The case BUILD_ORDER step 10 names, asserted field by field."""
    record = record_for(candidate(matched_function_ids=(ifmis_function_id,)), [TED, WORLD_BANK], function_map, defaults)

    assert record["Funding Source"] == "World Bank (IDA/IBRD)"
    assert "Core Financial Execution and Reporting" in record["FreeBalance Products Required"]
    assert record["Product Gaps"] == "", "that product is Available, so there is no gap"
    assert record["Partners Involved"] == "World Bank procurement notices"


def test_every_bd_only_column_is_the_configured_placeholder(defaults, function_map, ifmis_function_id):
    """39 of the 73 columns. Never a blank, never an invented value."""
    record = record_for(candidate(matched_function_ids=(ifmis_function_id,)), [TED, WORLD_BANK], function_map, defaults)
    placeholders = set(defaults["placeholders"].values())

    bd_columns = [c["name"] for c in defaults["columns"] if c["category"] == "B"]
    assert len(bd_columns) == 39

    for name in bd_columns:
        assert record[name] in placeholders, f"{name} is {record[name]!r}, not a configured placeholder"
        assert record[name] != "", f"{name} is blank; a blank says nothing about whether anyone looked"
        assert record[name] is not None


# --- the column set ----------------------------------------------------------


def test_every_appendix_e_column_is_present_and_in_order(defaults, function_map):
    record = record_for(candidate(), [TED], function_map, defaults)
    expected = [column["name"] for column in defaults["columns"]]

    assert list(record) == expected
    assert len(record) == 73


def test_the_traceability_columns_exist(defaults, function_map):
    """Not CRM fields. They are how a row is traced back to a notice and a batch."""
    record = record_for(candidate(), [TED], function_map, defaults)

    assert record["monitor_candidate_id"] == "C000123"
    assert record["monitor_export_batch"] == "", "stamped by the export at step 11, not here"


# --- products and gaps -------------------------------------------------------


def test_a_function_whose_product_is_not_available_becomes_a_gap(defaults, function_map):
    """Product Gaps names the product and its status, from the same sheet."""
    low_priority = next(
        f["function_id"]
        for f in function_map
        if f.get("product_status") and all(s != "Available" for s in f["product_status"].values())
    )

    record = record_for(candidate(matched_function_ids=(low_priority,)), [TED], function_map, defaults)

    assert record["Product Gaps"], "a non-Available product must be named as a gap"
    assert "(" in record["Product Gaps"], "the gap names its status"


def test_a_pillar_eight_function_yields_no_product_and_no_gap(defaults, function_map):
    """Component map 4.2 has no product mapping for Government Service Delivery."""
    pillar_eight = next(f["function_id"] for f in function_map if f["name"].startswith("8."))

    record = record_for(candidate(matched_function_ids=(pillar_eight,)), [TED], function_map, defaults)

    assert record["FreeBalance Products Required"] == ""
    assert record["Product Gaps"] == ""


def test_products_are_a_set_across_several_matched_functions(defaults, function_map, ifmis_function_id):
    other = next(f["function_id"] for f in function_map if f["name"].startswith("5.1"))

    record = record_for(candidate(matched_function_ids=(ifmis_function_id, other)), [TED], function_map, defaults)

    assert "Electronic Public Procurement" in record["FreeBalance Products Required"]
    assert "Core Financial Execution and Reporting" in record["FreeBalance Products Required"]


# --- account resolution ------------------------------------------------------


def test_the_account_is_proposed_as_text_and_never_resolved(defaults, function_map):
    """D31: the pipeline has no CRM scope with which to look one up."""
    record = record_for(candidate(), [TED], function_map, defaults)

    assert record["Account Name"] == "proposed: Ministry of Finance"


def test_created_by_is_left_for_zoho(defaults, function_map):
    """Zoho sets it to the import operator. The reviewer travels in Next Steps."""
    record = record_for(candidate(), [TED], function_map, defaults)

    assert record["Created By"] == ""
    assert "Ryan Dear" in record["Next Steps"]
    assert "2026-09-12" in record["Next Steps"]


# --- the heuristics ----------------------------------------------------------


@pytest.mark.parametrize(("score", "tier"), [(85, "Tier 1"), (84, "Tier 2"), (60, "Tier 2")])
def test_deal_tier_follows_the_score_band(score, tier, defaults, function_map):
    assert record_for(candidate(score=score), [TED], function_map, defaults)["Deal Tier"] == tier


def test_a_donor_source_makes_the_delivery_model_partner_supported(defaults, function_map):
    with_donor = record_for(candidate(), [TED, WORLD_BANK], function_map, defaults)
    without = record_for(candidate(), [TED], function_map, defaults)

    assert with_donor["Delivery Model"] == "Direct (Partner-Supported)"
    assert without["Delivery Model"] == "Direct"
    assert without["Partners Involved"] == ""
    assert without["Funding Source"] == "Government budget"


def test_eligibility_flags_drive_three_columns(defaults, function_map):
    flagged = record_for(candidate(eligibility_flags=("local_registration",)), [TED], function_map, defaults)
    clear = record_for(candidate(), [TED], function_map, defaults)

    assert flagged["Eligibility Requirements Met?"] == "Review needed"
    assert "local registration" in flagged["Eligibility Requirements Comments"]
    assert flagged["Do we need to register our interest?"] == "Yes"
    assert clear["Eligibility Requirements Met?"] == "Yes"
    assert clear["Eligibility Requirements Comments"].startswith("No eligibility barriers")


def test_a_tender_deadline_goes_in_the_submission_column(defaults, function_map):
    record = record_for(candidate(procurement_type="system"), [TED], function_map, defaults)

    assert record["Proposal Due or Submitted"] == "2026-11-30"
    assert record["Expected Release of RFP/EOI?"] == "TBD"


def test_a_donor_pipeline_deadline_goes_in_the_release_column(defaults, function_map):
    """appendix E: procurement_type 'other' is a pipeline signal, not a tender."""
    record = record_for(candidate(procurement_type="other"), [TED], function_map, defaults)

    assert record["Expected Release of RFP/EOI?"] == "2026-11-30"
    assert record["Proposal Due or Submitted"] == "TBD"


def test_a_candidate_with_no_deadline_gets_the_placeholder(defaults, function_map):
    record = record_for(candidate(deadline_at=None), [TED], function_map, defaults)

    assert record["Proposal Due or Submitted"] == "TBD"


def test_an_unstated_value_is_zero_not_blank(defaults, function_map):
    """appendix E's default for Total Opportunity Amount is 0, matching the CRM."""
    no_value = candidate(
        estimated_value=None, value_currency=None, estimated_value_usd=None, value_rate=None, value_rate_date=None
    )
    record = record_for(no_value, [TED], function_map, defaults)

    assert record["Total Opportunity Amount"] == 0
    assert record["Currency"] == "USD", "the suggested default, since nothing was published to read a currency from"
    assert "not stated" in record["Pricing Notes"]


def test_the_pricing_note_says_it_is_unverified(defaults, function_map):
    record = record_for(candidate(), [TED, WORLD_BANK], function_map, defaults)

    assert "Monitor-estimated, unverified" in record["Pricing Notes"]
    assert "World Bank" in record["Pricing Notes"]
    assert "P123456" in record["Pricing Notes"]


def test_industry_comes_from_the_region(defaults, function_map):
    west_africa = record_for(candidate(region="West Africa"), [TED], function_map, defaults)
    europe = record_for(candidate(region="Europe"), [TED], function_map, defaults)

    assert west_africa["Industry"] == "North & West Africa"
    assert europe["Industry"] == "TBD", "open decision 2: the picklist value is unconfirmed"


@pytest.mark.parametrize(
    ("admin_level", "expected"),
    [("national", "Central"), ("regional", "Local/Regional"), ("local", "Local/Regional"), ("donor", "Central")],
)
def test_level_of_government_maps_from_admin_level(admin_level, expected, defaults, function_map):
    record = record_for(candidate(admin_level=admin_level), [TED], function_map, defaults)

    assert record["Level of Government"] == expected


# --- value_basis: the CRM's one amount column, and where the derivation goes ---


UAH_VALUE = {
    "estimated_value": Decimal("4428444.00"),
    "value_currency": "UAH",
    "estimated_value_usd": 99_408,
    "value_rate": Decimal("44.5483"),
    "value_rate_date": date(2026, 9, 14),
}

NGN_VALUE = {
    "estimated_value": Decimal("5000000.00"),
    "value_currency": "NGN",
    "estimated_value_usd": None,
    "value_rate": None,
    "value_rate_date": None,
}

NO_VALUE = {
    "estimated_value": None,
    "value_currency": None,
    "estimated_value_usd": None,
    "value_rate": None,
    "value_rate_date": None,
}


def test_published_basis_keeps_the_published_currency_and_amount(defaults, function_map):
    """The default. Rule 9 all the way to the CSV: the export never converts on its own."""
    assert defaults["value_basis"] == "published", "the acceptance below assumes the config default"

    record = record_for(candidate(**UAH_VALUE), [TED], function_map, defaults)

    assert record["Currency"] == "UAH"
    assert record["Total Opportunity Amount"] == Decimal("4428444.00")
    assert "99,408" in record["Pricing Notes"]
    assert "44.5483" in record["Pricing Notes"]
    assert "2026-09-14" in record["Pricing Notes"]


def test_published_basis_with_no_usd_rate_says_so_and_never_writes_zero(defaults, function_map):
    """A documented absence (no fx_rates row for NGN), never an error and never a converted number."""
    record = record_for(candidate(**NGN_VALUE), [TED], function_map, defaults)

    assert record["Currency"] == "NGN"
    assert record["Total Opportunity Amount"] == Decimal("5000000.00")
    assert record["Total Opportunity Amount"] != 0, "a real published value is never replaced by the zero placeholder"
    assert "no USD rate held for NGN" in record["Pricing Notes"]


def test_published_basis_with_no_value_uses_the_zero_placeholder(defaults, function_map):
    record = record_for(candidate(**NO_VALUE), [TED], function_map, defaults)

    assert record["Currency"] == "USD", "the suggested default: nothing was published to read a currency from"
    assert record["Total Opportunity Amount"] == 0
    assert "not stated" in record["Pricing Notes"]


def test_usd_basis_converts_and_puts_the_published_figure_in_pricing_notes(defaults, function_map):
    """Currency here is the fx target currency (`config/fx.yaml`), not a "USD"
    literal in this module — the same string today, but for a different reason,
    and `value_for_basis` must read it from `load_fx()` rather than hardcode it."""
    usd_defaults = {**defaults, "value_basis": "usd"}
    record = record_for(candidate(**UAH_VALUE), [TED], function_map, usd_defaults)

    assert record["Currency"] == load_fx().target_currency
    assert record["Total Opportunity Amount"] == 99_408
    assert "UAH 4,428,444" in record["Pricing Notes"]
    assert "44.5483" in record["Pricing Notes"]


def test_usd_basis_falls_back_to_published_when_no_rate_covers_the_currency(defaults, function_map):
    """Never 'USD 0' for a contract whose value is real but not convertible today."""
    usd_defaults = {**defaults, "value_basis": "usd"}
    record = record_for(candidate(**NGN_VALUE), [TED], function_map, usd_defaults)

    assert record["Currency"] == "NGN"
    assert record["Total Opportunity Amount"] == Decimal("5000000.00")
    assert "no USD rate held for NGN" in record["Pricing Notes"]


def test_usd_basis_with_no_value_uses_the_zero_placeholder_too(defaults, function_map):
    usd_defaults = {**defaults, "value_basis": "usd"}
    record = record_for(candidate(**NO_VALUE), [TED], function_map, usd_defaults)

    assert record["Currency"] == "USD"
    assert record["Total Opportunity Amount"] == 0


def test_published_basis_with_a_value_already_in_the_target_currency_reads_plainly(defaults, function_map):
    """`candidate()`'s default is USD at the identity rate (migration 012/013's
    identity-conversion defect): "USD 4,200,000 ≈ USD 4,200,000 at 1.0000
    USD/USD" is the same number twice with an equation in between, not
    information, and it is not what a notice published in the fx target
    currency should read like on an early-stage record."""
    record = record_for(candidate(), [TED], function_map, defaults)

    assert record["Currency"] == "USD"
    assert record["Total Opportunity Amount"] == Decimal("4200000.00")
    assert "USD 4,200,000" in record["Pricing Notes"]
    assert "1.0000" not in record["Pricing Notes"]
    assert "≈" not in record["Pricing Notes"]


def test_usd_basis_with_a_value_already_in_the_target_currency_reads_plainly(defaults, function_map):
    """Same identity case under value_basis: usd: nothing was converted, so there
    is nothing for Pricing Notes to hold back either — no "published as ... at
    1.0000 USD/USD"."""
    usd_defaults = {**defaults, "value_basis": "usd"}
    record = record_for(candidate(), [TED], function_map, usd_defaults)

    assert record["Currency"] == load_fx().target_currency
    assert record["Total Opportunity Amount"] == 4_200_000
    assert "USD 4,200,000" in record["Pricing Notes"]
    assert "1.0000" not in record["Pricing Notes"]
    assert "published as" not in record["Pricing Notes"]


def test_an_unrecognised_value_basis_raises(defaults, function_map):
    bad_defaults = {**defaults, "value_basis": "eur"}

    with pytest.raises(RecordDefaultsError, match="value_basis"):
        record_for(candidate(), [TED], function_map, bad_defaults)


def test_the_builder_touches_no_database_and_no_model():
    """It is a pure function, which is what makes the export reproducible."""
    from pathlib import Path

    source = Path("monitor/stage/record.py").read_text(encoding="utf-8")

    assert "psycopg" not in source
    assert "anthropic" not in source
    assert "import httpx" not in source


def test_no_usd_literal_outside_comments_and_docstrings():
    """rule 6: the currency this module writes always comes from the row or from
    `config/fx.yaml` through `load_fx()`, never a "USD" string typed into the
    code. The module's docstrings and comments talk *about* that rule in
    prose ("Never 'USD' as a literal anywhere else"), so those are stripped
    first; what must not remain is a "USD"/'USD' token used as a value."""
    import re
    from pathlib import Path

    source = Path("monitor/stage/record.py").read_text(encoding="utf-8")

    # Strip triple-quoted strings (the module docstring and every function's),
    # then strip end-of-line comments, in that order so a "#" inside a
    # docstring is gone before the comment strip runs.
    without_docstrings = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", source)
    without_comments = re.sub(r"#.*", "", without_docstrings)

    assert not re.search(r"USD", without_comments), (
        "monitor/stage/record.py has a 'USD' token outside a comment or docstring; "
        "the currency must come from the row or from load_fx().target_currency"
    )
