"""The one connector base. There is no second one until step 17 adds a browser.

Contract: `fetch()` returns what the source published, as fetched, and nothing
derived. Mapping to a `Notice` is the normaliser's job (rule 5) and filtering is
the filter's; a connector that translates or drops anything is a finding.

Failure policy, and it is deliberate (rules 1 to 3):

  - One attempt. No retry, no backoff, no second endpoint, no second selector.
    A source that fails is unhealthy and a person decides what to do about it.
  - Every failure raises `ConnectorError` carrying the source id. That is the one
    permitted `try/except` in this codebase: it re-raises with the id attached and
    swallows nothing.
  - An empty result is not an error here, but it is a failure state one layer up:
    `monitor/health/source_health.py` treats zero yield on a source that normally
    yields as a failure, not an empty success (rule 4).

Acquisition ethics (rule 21): the user agent identifies the client and carries a
contact address, one polite pass per scheduled run, no disguise.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from monitor.models import RawNotice, Source

TIMEOUT_SECONDS = 30.0


class ConnectorError(Exception):
    """A source failed to yield. Carries the source id so health can be updated."""

    def __init__(self, source_id: str, cause: BaseException) -> None:
        super().__init__(f"{source_id}: {type(cause).__name__}: {cause}")
        self.source_id = source_id
        self.cause = cause


def user_agent() -> str:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        raise RuntimeError("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
    return agent


class FeedConnector:
    """Base for APIs and structured feeds. Subclasses implement `fetch_raw`."""

    def __init__(self, source: Source) -> None:
        self.source = source

    @property
    def source_id(self) -> str:
        return self.source.id

    def fetch(self) -> list[RawNotice]:
        """One pass. Any failure becomes a ConnectorError naming the source."""
        try:
            with httpx.Client(
                timeout=TIMEOUT_SECONDS,
                headers={"User-Agent": user_agent()},
                follow_redirects=True,
            ) as client:
                return self.fetch_raw(client)
        except ConnectorError:
            raise
        except Exception as cause:  # noqa: BLE001 - re-raised with the source id, never swallowed
            raise ConnectorError(self.source_id, cause) from cause

    def fetch_raw(self, client: httpx.Client) -> list[RawNotice]:
        raise NotImplementedError(f"{type(self).__name__} must implement fetch_raw")

    def raw_notice(self, url: str, payload: str, mime: str) -> RawNotice:
        """Build a RawNotice stamped with now. Subclasses use this rather than the model."""
        return RawNotice(
            source_id=self.source_id,
            url=url,
            fetched_at=datetime.now(UTC),
            mime=mime,
            payload=payload,
        )
