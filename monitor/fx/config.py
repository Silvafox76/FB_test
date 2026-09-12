"""Load and validate `config/fx.yaml`. Wrong config is a loud failure, not a default."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from monitor.schedule import parse as parse_schedule

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "fx.yaml"

STRICT = ConfigDict(extra="forbid", frozen=True)

CURRENCY_LENGTH = 3

# The only rounding modes this pipeline will use. Naming them here rather than
# passing the string straight to Decimal means a typo in the config is caught on
# load with a message that lists what is allowed, not at the first conversion.
ROUNDING_MODES = ("ROUND_HALF_UP", "ROUND_HALF_EVEN")


def _currency(value: str) -> str:
    if len(value) != CURRENCY_LENGTH or not value.isalpha() or value != value.upper():
        raise ValueError(f"{value!r} is not an ISO 4217 alpha-3 code")
    return value


class Publisher(BaseModel):
    """The one source of rates. There is deliberately no list of them (rule 1)."""

    model_config = STRICT

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    query_date_format: str = Field(min_length=1)
    base_currency: str
    schedule: str
    minimum_rows: int = Field(gt=0)

    @field_validator("base_currency")
    @classmethod
    def _base(cls, value: str) -> str:
        return _currency(value)

    @field_validator("url")
    @classmethod
    def _url_takes_a_date(cls, value: str) -> str:
        # The undated endpoint forward-dates after 15:30 Kyiv; see config/fx.yaml.
        if "{date}" not in value:
            raise ValueError("publisher url must carry a {date} placeholder; the undated endpoint forward-dates")
        return value

    @field_validator("schedule")
    @classmethod
    def _schedule(cls, value: str) -> str:
        # Validated at the config boundary rather than at the first scheduled run,
        # so a bad cron fails on load like every other config error (rule 4).
        parse_schedule(value)
        return value


class FxConfig(BaseModel):
    model_config = STRICT

    publisher: Publisher
    target_currency: str
    pegged_to_eur: dict[str, Decimal]
    max_rate_age_days: int = Field(gt=0)
    rounding: str

    @field_validator("target_currency")
    @classmethod
    def _target(cls, value: str) -> str:
        return _currency(value)

    @field_validator("pegged_to_eur")
    @classmethod
    def _pegs(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        for currency, rate in value.items():
            _currency(currency)
            if rate <= 0:
                raise ValueError(f"peg for {currency} is {rate}, which is not a rate")
        return value

    @field_validator("rounding")
    @classmethod
    def _rounding(cls, value: str) -> str:
        if value not in ROUNDING_MODES:
            raise ValueError(f"rounding {value!r} is not one of {', '.join(ROUNDING_MODES)}")
        return value


@lru_cache(maxsize=4)
def load(path: Path | None = None) -> FxConfig:
    """The config, validated once per path per process.

    The path is an argument so a test can point at a copy. Cached because the
    record builder asks for the target currency for every candidate it renders
    and a queue page renders dozens; without this each row re-read and
    re-validated the YAML (design-cop on 67954a7). The model is frozen, so a
    cached instance cannot be mutated by a caller. maxsize 4 rather than 1 so a
    test loading a tmp copy does not evict the real file for the next call.
    """
    document = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))
    return FxConfig.model_validate(document)
