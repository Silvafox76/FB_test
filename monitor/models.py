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
from decimal import Decimal
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
    `covers`, `schedule`, `list_url`, `api_url`, `row_selector` and `owner` live only
    in the YAML: the registry is authoritative for the schedule (BUILD_ORDER step 11)
    and the table has no column for them.
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
    # Five-field cron, UTC, and the registry is authoritative for it. This said "in the
    # source's own timezone" until 2026-09-12, which was never implementable: there is no
    # timezone field on this model and never has been, so nothing could have read one.
    # Every schedule comment in sources/*.yaml that names a zone says UTC - `'0 5 * * *'
    # # 05:00 UTC daily, 07:00 Berlin` - so UTC is what the registry has always meant.
    # `monitor/schedule.py` reads it, documents the subset of cron it accepts, and
    # validates it below.
    schedule: str
    list_url: str = ""  # one of list_url or api_url is set; checked below
    api_url: str = ""
    # The CSS selector naming one notice row in a rendered listing. BrowserConnector
    # sources only, and required for them (checked below). It is in the registry and
    # not in the connector because a selector is the thing about a portal most likely
    # to change without warning, and changing it should be a config edit with a
    # version hash rather than a deployment (rule 6). One selector per source: it is
    # both what the render waits for and what the parser reads, so there is no second
    # selector to fall back to (BUILD_ORDER step 17).
    row_selector: str = ""
    tos_status: TosStatus
    # Notice types to exclude at the query, where the source classifies them.
    # Acquisition scope rather than a filter stage: it decides what to ask for, not
    # what to keep, so nothing is read and then thrown away. Empty means ask for
    # everything the other query terms match.
    exclude_notice_types: list[str] = Field(default_factory=list)
    enabled: bool = False
    owner: str = Field(min_length=1)
    expected_min: int = Field(ge=0)
    expected_max: int = Field(ge=0)
    max_consecutive_failures: int = Field(ge=1)

    @field_validator("schedule")
    @classmethod
    def _schedule_is_readable(cls, value: str) -> str:
        """Reject an unreadable cron here, at the registry boundary, not on a live wake.

        Rule 4 wants validation at the module boundary, and this is the boundary: every
        path into the registry goes through `Source`, so `make seed`, `monitor fetch`
        and the test suite all reject a bad schedule the moment the YAML is read.

        Without this the first thing to parse a schedule was `is_due()`, called from
        inside `fetch()`'s loop over enabled sources - which is OUTSIDE `fetch_source`'s
        try/except. A single unreadable cron would therefore raise past every remaining
        source and abort the whole pass, so one mistyped line in one YAML file would
        stop eight healthy sources being read. That is precisely the isolation
        `monitor/fetch.py`'s docstring promises ("one broken source must mark itself
        unhealthy without taking the others down with it"), and the schedule was the one
        field that could break it.
        """
        from monitor.schedule import ScheduleError, parse

        try:
            parse(value)
        except ScheduleError as error:
            raise ValueError(f"schedule {value!r}: {error}") from error
        return value

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
        # A browser source without a row_selector would render a page and then have
        # nothing to assert about it, which is the silent-zero this connector class
        # exists to prevent.
        #
        # Required of an *enabled* browser source only, for the same reason
        # `test_every_enabled_source_is_a_feed_connector` scopes itself that way.
        # source-onboarder decides a portal needs a browser by fetching its HTML and
        # finding the rows absent from it; working out what the rows are *called*
        # needs the page rendered, which is the connector's job and happens later.
        # Requiring the selector at onboarding would mean one unbuilt source made the
        # whole registry unloadable. `enabled` is what decides whether anything
        # fetches, so it is where the requirement binds — and `browser_base`'s
        # `row_selector()` raises again at fetch time, which is the guard that
        # actually stands between a blank selector and a silent zero.
        if self.enabled and self.connector_class == "BrowserConnector" and not self.row_selector.strip():
            raise ValueError(f"{self.id} is an enabled BrowserConnector and must declare row_selector")
        if self.connector_class != "BrowserConnector" and self.row_selector.strip():
            raise ValueError(f"row_selector is for BrowserConnector sources; {self.id} is a {self.connector_class}")
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
    # The amount exactly as the publisher stated it, and in which currency. Never a
    # converted figure: conversion is a derived, stamped step that happens at
    # staging (rule 9, and migration 012 for why the model no longer supplies it).
    # Both are None together when the notice states no value - "no value" and
    # "an amount in an unknown currency" are not the same thing, and the pair is
    # checked below so the second cannot be stored as the first.
    estimated_value: Decimal | None = None
    value_currency: str | None = None
    body: str = ""
    filter_result: str = ""
    status: NoticeStatus = "detected"

    @model_validator(mode="after")
    def _value_and_currency_travel_together(self) -> "Notice":
        if (self.estimated_value is None) != (self.value_currency is None):
            raise ValueError(
                f"{self.source_id}: estimated_value={self.estimated_value!r} with "
                f"value_currency={self.value_currency!r}; a price needs both or neither"
            )
        if self.estimated_value is not None and self.estimated_value <= 0:
            # Publishers use 0 for "not stated" - Liberia on 3 of 14 releases, TED
            # on 19 - and a zero carried through becomes USD 0 on a CRM record for
            # a building. The normaliser drops it; this is the backstop.
            raise ValueError(f"{self.source_id}: estimated_value={self.estimated_value} is not a price")
        return self


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
    # `estimated_value_usd` WAS HERE AND WAS REMOVED ON 2026-09-12. It asked the
    # model for a figure the model does not have. All eight values it ever produced
    # were checked against the full notice text it was given, and none of the eight
    # appears in that text in any format - they were invented, not misread, and on
    # three of the four notices where the payload carried a real EUR figure the
    # model never saw, the invention sits at 1.09 to 1.19 times it. The number is
    # read from the source's own structured field instead, in the currency the
    # source published, and converted at a stamped rate at staging. See
    # migrations/012_published_value_and_currency.sql.
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
    # As published, carried from the primary notice.
    estimated_value: Decimal | None = None
    value_currency: str | None = None
    # Derived at staging: `estimated_value / value_rate`, at the rate dated
    # `value_rate_date`. None when no rate covers the currency, which is a
    # documented absence - the record still shows the published amount.
    estimated_value_usd: int | None = None
    value_rate: Decimal | None = None
    value_rate_date: date | None = None
    eligibility_flags: list[EligibilityFlag] = Field(default_factory=list)
    deadline_at: datetime | None = None
    # Set only by the reviewer's decision transaction. The trigger in
    # migrations/002_roles.sql refuses a decision from any other role.
    reviewer: str = ""
    rejection_reason: str = ""
    approved_record_id: str = ""
