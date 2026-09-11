"""The typed contracts between modules.

One model per stage boundary (rule 5), validated at every crossing (rule 4).
Unknown fields raise rather than being ignored, so a misspelling in a source YAML
is a loud failure naming the field rather than a silently missing value.

`Score` is the model's own output and nothing else: it is passed to the Anthropic
API as the `record_score` tool's input schema at step 6, so it holds what the
model is asked to produce and not the call metadata (tokens, cost, latency,
prompt version) that `monitor/score/run.py` records alongside it. Asking a model
to report its own token count in a tool call would be nonsense. The shape is
Architecture v0.4 appendix C, field for field.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --- vocabularies ------------------------------------------------------------
# Closed sets, because an unknown value is a defect and not a new category the
# pipeline should invent for itself.

AdminLevel = Literal["national", "regional", "local", "donor"]
Stream = Literal["feed", "portal", "mail"]
AccessType = Literal["api", "rss", "public_listing", "registered", "mail"]
ConnectorClass = Literal["FeedConnector", "PageConnector", "BrowserConnector", "MailConnector"]
TosStatus = Literal["reviewed_ok", "reviewed_restricted", "pending"]
# detected      fetched and stored, not yet filtered
# filtered_out  the free filter dropped it, with filter_result saying why
# filtered_in   the free filter passed it; waiting for the scorer
# scored        a model call produced a Score for it
# parked        the model failed schema validation twice (step 6)
NoticeStatus = Literal["detected", "filtered_out", "filtered_in", "scored", "parked"]
CandidateStatus = Literal["detected", "filtered_out", "scored", "staged", "pending_review", "approved", "rejected"]
ProcurementType = Literal["system", "services", "advisory", "other"]
Confidence = Literal["high", "medium", "low"]
EligibilityFlag = Literal["national_only", "local_registration", "consortium_required", "tax_clearance_required"]
MatchMethod = Literal["content_hash", "title_fuzzy", "system_name"]

# Architecture v0.4 appendix C caps the summary at 120 words. The cap is enforced
# here rather than in the prompt, because a prompt is a request and a validator is
# a contract.
SUMMARY_MAX_WORDS = 120

Relevance = Annotated[int, Field(ge=0, le=100)]

STRICT = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class SourceHealth(BaseModel):
    """The registry's own statement of what a healthy run looks like for a source.

    `expected_items_per_run` is what makes a zero-yield run a failure state rather
    than an empty success (rule 4).
    """

    model_config = STRICT

    expected_items_per_run: tuple[int, int]
    max_consecutive_failures: int = Field(ge=1)

    @field_validator("expected_items_per_run")
    @classmethod
    def min_not_above_max(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if low > high:
            raise ValueError(f"expected_items_per_run low {low} is above high {high}")
        return value


class Source(BaseModel):
    """One row of `sources/`, validated on load.

    Field names match the `sources` table; the YAML keys that differ (`access`,
    `connector`) are aliases, and `health` is flattened by the validator below.
    `covers`, `schedule`, `list_url`, `api_url` and `owner` live only in the YAML:
    the registry is authoritative for the schedule (BUILD_ORDER step 11) and the
    table has no column for them.
    """

    model_config = STRICT

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1)
    country: str  # ISO 3166-1 alpha-2, or 'EU' for TED, or 'multi' for donor sources
    covers: list[str] = Field(default_factory=list)  # ISO codes, for multi-country sources only
    admin_level: AdminLevel
    language: str
    stream: Stream
    access_type: AccessType = Field(alias="access")
    connector_class: ConnectorClass = Field(alias="connector")
    wave: int = Field(ge=1, le=3)
    schedule: str  # five-field cron, in the source's own timezone; the registry is authoritative
    list_url: str = ""  # one of list_url or api_url is set; checked below
    api_url: str = ""
    tos_status: TosStatus
    enabled: bool = False
    owner: str = Field(min_length=1)
    expected_min: int = Field(ge=0)
    expected_max: int = Field(ge=0)
    max_consecutive_failures: int = Field(ge=1)

    @model_validator(mode="before")
    @classmethod
    def flatten_health(cls, data: Any) -> Any:
        """Accept the YAML's nested `health` block and store it flat, as the table does."""
        if not isinstance(data, dict) or "health" not in data:
            return data
        data = dict(data)
        health = SourceHealth.model_validate(data.pop("health"))
        data["expected_min"], data["expected_max"] = health.expected_items_per_run
        data["max_consecutive_failures"] = health.max_consecutive_failures
        return data

    @model_validator(mode="after")
    def one_url_at_least(self) -> Source:
        if not (self.list_url or self.api_url):
            raise ValueError("a source needs list_url or api_url")
        if self.country == "multi" and not self.covers:
            raise ValueError("a source with country 'multi' must say which countries it covers")
        return self


class RawNotice(BaseModel):
    """What a connector returns: the response as fetched and nothing derived.

    `content_hash` and `storage_path` are not here because the connector cannot
    know them. The hash is taken from the normalised title and body at step 4 and
    the storage path is assigned when the payload is written, so both are set on
    the `notices_raw` row rather than on this model.
    """

    model_config = STRICT

    source_id: str
    url: str
    fetched_at: datetime
    mime: str
    payload: str


class Notice(BaseModel):
    """A notice as published, in its own language (rule 9)."""

    model_config = STRICT

    content_hash: str
    source_id: str
    external_id: str = ""
    url: str
    title: str = Field(min_length=1)
    buyer: str = ""
    country: str
    admin_level: AdminLevel
    published_at: datetime | None = None
    # None when the original string could not be parsed by rule. Rule 10: never
    # filled in from a translation; the raw string is logged instead.
    deadline_at: datetime | None = None
    language: str
    language_confidence: float = Field(ge=0.0, le=1.0)
    cpv_codes: list[str] = Field(default_factory=list)
    estimated_value_usd: int | None = None  # None when the notice states no value
    body: str = ""
    filter_result: str = ""
    status: NoticeStatus = "detected"


class Translation(BaseModel):
    """A derived English rendering, stamped with what produced it (rule 9)."""

    model_config = STRICT

    notice_id: str
    title_en: str = Field(min_length=1)
    body_en: str = ""
    model: str
    prompt_version: str
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)


class MatchedFunction(BaseModel):
    """A function the model matched, with the text that made it say so."""

    model_config = STRICT

    function_id: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class Score(BaseModel):
    """The `record_score` tool's input schema. Architecture v0.4 appendix C."""

    model_config = STRICT

    relevance: Relevance
    title_en: str = Field(min_length=1)  # English title; copied through when already English
    matched_functions: list[MatchedFunction] = Field(default_factory=list)
    system_names: list[str] = Field(default_factory=list)
    procurement_type: ProcurementType
    estimated_value_usd: int | None = None
    eligibility_flags: list[EligibilityFlag] = Field(default_factory=list)
    deadline_at: date | None = None
    summary_en: str = Field(min_length=1)
    confidence: Confidence

    @field_validator("summary_en")
    @classmethod
    def summary_is_short(cls, value: str) -> str:
        words = len(re.findall(r"\S+", value))
        if words > SUMMARY_MAX_WORDS:
            raise ValueError(f"summary_en is {words} words, over the {SUMMARY_MAX_WORDS} word limit")
        return value


class Candidate(BaseModel):
    """The reviewer's unit of work: one or more notices about the same procurement."""

    model_config = STRICT

    id: str = Field(pattern=r"^C[0-9]{6}$")
    primary_notice_id: str
    score: Relevance
    status: CandidateStatus
    region: str
    language: str
    title_en: str = Field(min_length=1)
    buyer: str = ""
    country: str
    admin_level: AdminLevel
    summary_en: str = Field(min_length=1)
    matched_functions: list[MatchedFunction] = Field(default_factory=list)
    system_names: list[str] = Field(default_factory=list)
    procurement_type: ProcurementType
    estimated_value_usd: int | None = None
    eligibility_flags: list[EligibilityFlag] = Field(default_factory=list)
    deadline_at: datetime | None = None
    # Set only by the reviewer's decision transaction. The trigger in
    # migrations/002_roles.sql refuses a decision from any other role.
    reviewer: str = ""
    rejection_reason: str = ""
    approved_record_id: str = ""
