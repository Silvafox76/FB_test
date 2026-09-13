"""`config/record_defaults.yaml`, validated with Pydantic like every other config kind.

Until 2026-09-13 the registry checked this file by hand - `value_basis`, a list of
sentence keys, and that `columns` was non-empty - and left `placeholders`,
`suggested`, `deal_tier`, `level_of_government`, `industry_by_region`,
`funding_source_by_stream`, `register_interest_flags` and each column's shape
unchecked until `monitor/stage/record.py` or `review/export.py` read a key that
was not there (design-cop on dbe6e71). Rule 4 asks for Pydantic validation at
the module boundary, and `monitor/fx/config.py` already does it that way for the
rate config; this is the same treatment for the record builder's.

Every model is `extra="forbid"` and frozen: a key the record builder does not
read is a typo or a stale line, and either should fail `make up` and the review
app's first request rather than sit in the file looking intentional. The keys
here are pinned to what `monitor/stage/record.py` actually reads - the sentence
list by a test that reads its source, the rest by the builder's own subscripts.

`load_record_defaults()` in `load.py` returns the validated document as a plain
dict, because the builder, the review app and the export all index into it and
none of them needs a model object; the model is the check, not the interface.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRICT = ConfigDict(extra="forbid", frozen=True)

# Which figure goes in appendix E's single amount column. Owned here, at the config
# boundary; monitor/stage/record.py imports VALUE_BASES rather than redefining it.
ValueBasis = Literal["published", "usd"]
VALUE_BASES = frozenset(get_args(ValueBasis))

# Appendix E's three column categories.
Category = Literal["D", "S", "B"]


class Placeholders(BaseModel):
    """The literal strings the CRM already uses for an unfilled field."""

    model_config = STRICT

    tbd: str = Field(min_length=1)
    unknown: str = Field(min_length=1)
    dash: str = Field(min_length=1)
    zero: int


class Suggested(BaseModel):
    """S-field defaults for every record, editable by the reviewer."""

    model_config = STRICT

    currency: str = Field(min_length=3, max_length=3)
    probability_pct: int = Field(ge=0, le=100)
    pipeline: str = Field(min_length=1)
    standard_or_custom: str = Field(min_length=1)
    delivery_model_default: str = Field(min_length=1)
    delivery_model_partner_supported: str = Field(min_length=1)
    customer_type_default: str = Field(min_length=1)
    lead_source: str = Field(min_length=1)
    proposal_type_default: str = Field(min_length=1)
    partner_led_default: str = Field(min_length=1)


class DealTier(BaseModel):
    model_config = STRICT

    tier_1_min_score: int = Field(ge=0, le=100)
    tier_1: str = Field(min_length=1)
    tier_2: str = Field(min_length=1)


class LevelOfGovernment(BaseModel):
    """One label per admin_level the candidate can carry; `national` is the fallback."""

    model_config = STRICT

    national: str = Field(min_length=1)
    regional: str = Field(min_length=1)
    local: str = Field(min_length=1)
    donor: str = Field(min_length=1)


class Sentences(BaseModel):
    """Every `sentences[...]` key monitor/stage/record.py reads, and no others.

    The field names ARE the contract: tests/unit/test_registry.py reads record.py's
    source and asserts its subscripts equal this model's fields, so a sentence
    added to the code without a line here fails, and one left here after the code
    stops reading it fails too.
    """

    model_config = STRICT

    account_name_proposal: str = Field(min_length=1)
    eligibility_none_detected: str = Field(min_length=1)
    next_steps: str = Field(min_length=1)
    pricing_notes: str = Field(min_length=1)
    value_in_target: str = Field(min_length=1)
    value_no_usd: str = Field(min_length=1)
    value_not_stated: str = Field(min_length=1)
    value_usd_basis: str = Field(min_length=1)
    value_with_usd: str = Field(min_length=1)


RECORD_SENTENCE_KEYS = frozenset(Sentences.model_fields)


class Column(BaseModel):
    """One appendix E column: its exact CRM header and which category it is."""

    model_config = STRICT

    name: str = Field(min_length=1)
    category: Category


def _needs_default(mapping: dict[str, str], what: str) -> dict[str, str]:
    """A lookup table the builder falls back on `default` for must carry one."""
    if "default" not in mapping:
        raise ValueError(f"{what} has no 'default' entry; record.py falls back to it")
    for key, value in mapping.items():
        if not key or not value:
            raise ValueError(f"{what} carries an empty key or value: {key!r}: {value!r}")
    return mapping


class RecordDefaults(BaseModel):
    model_config = STRICT

    placeholders: Placeholders
    value_basis: ValueBasis
    suggested: Suggested
    deal_tier: DealTier
    level_of_government: LevelOfGovernment
    # Keyed by region name, which carries spaces ("West Africa"), so a mapping
    # rather than a model; the one key the builder relies on is checked.
    industry_by_region: dict[str, str]
    # Keyed by the donor stream tag on a source; `default` is a government's own budget.
    funding_source_by_stream: dict[str, str]
    sentences: Sentences
    register_interest_flags: list[str] = Field(min_length=1)
    columns: list[Column] = Field(min_length=1)

    @field_validator("industry_by_region")
    @classmethod
    def _industry(cls, value: dict[str, str]) -> dict[str, str]:
        return _needs_default(value, "industry_by_region")

    @field_validator("funding_source_by_stream")
    @classmethod
    def _funding(cls, value: dict[str, str]) -> dict[str, str]:
        return _needs_default(value, "funding_source_by_stream")

    @field_validator("columns")
    @classmethod
    def _unique_headers(cls, value: list[Column]) -> list[Column]:
        # Zoho's import mapper matches on headers; two columns with one name would
        # silently double a CSV header and map one of them to nothing.
        seen: set[str] = set()
        for column in value:
            if column.name in seen:
                raise ValueError(f"column {column.name!r} appears twice; headers are the import contract")
            seen.add(column.name)
        return value
