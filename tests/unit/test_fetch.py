"""The acquire stage's wiring, without a network or a database.

Change detection itself is proven end to end by running `make fetch S=ted` twice
against the live API (BUILD_ORDER step 4, acceptance 3). What is worth a unit test
is the wiring around it: a source with no connector has to fail loudly rather than
quietly doing nothing.
"""

from __future__ import annotations

import pytest
import yaml

from monitor.fetch import CONNECTORS, build_connector, cpv_prefixes
from monitor.models import Source
from monitor.registry.load import SOURCES_DIR


def source(source_id: str) -> Source:
    path = SOURCES_DIR / f"{source_id}.yaml"
    return Source.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_a_source_with_no_connector_raises():
    """There is no generic fallback connector (rule 1).

    The example is fabricated rather than taken from the registry. This test used to
    point at whichever real source was next to be wired - World Bank, until step 11
    wired it - and then failed for the good reason that the work had been done. A
    source id that will never have a connector tests the rule and nothing else.
    """
    unwired = source("ted").model_copy(update={"id": "a-source-nobody-has-written"})

    with pytest.raises(KeyError, match="no connector for source"):
        build_connector(unwired)


def test_ted_builds_with_the_configured_cpv_prefixes():
    """Rule 6: the prefixes come from config/thresholds.yaml, not from the code."""
    connector, mapper = build_connector(source("ted"))

    assert connector.source_id == "ted"
    assert connector.cpv_prefixes == cpv_prefixes()
    assert mapper.__module__ == "monitor.normalise.ted"


def test_only_sources_with_a_recorded_fixture_are_wired():
    """A connector entry without a contract test is a source nobody has proven."""
    from pathlib import Path

    fixtures = Path(__file__).resolve().parents[1] / "contract" / "fixtures"
    for source_id in CONNECTORS:
        assert (fixtures / f"{source_id}.json").exists(), f"{source_id} is wired but has no fixture"


def test_every_wired_source_is_enabled_and_every_enabled_source_is_wired():
    """The registry's enabled flag and the connector table have to agree."""
    from monitor.registry import load_sources

    enabled = {s.id for s in load_sources() if s.enabled}

    assert enabled == set(CONNECTORS)


def test_a_mapper_failure_is_wrapped_with_the_source_and_the_notice():
    """A mapper is built to raise; it has to become that source's failure."""
    import json as json_module
    from datetime import UTC, datetime

    from monitor.fetch import NormaliseError, _store_notices
    from monitor.models import RawNotice

    def exploding_mapper(document):
        raise ValueError("unknown country code 'ZZZ'")

    raw = RawNotice(
        source_id="ted",
        url="https://x.invalid",
        fetched_at=datetime.now(UTC),
        mime="application/json",
        payload=json_module.dumps({"publication-number": "619297-2026"}),
    )

    with pytest.raises(NormaliseError) as raised:
        _store_notices(None, source("ted"), raw, exploding_mapper)

    message = str(raised.value)
    assert "ted" in message
    assert "619297-2026" in message
    assert "unknown country code" in message
    assert isinstance(raised.value.cause, ValueError)


# --- the schedule filter on `fetch all` ----------------------------------------


class Recording:
    """A connection stub that answers the last-attempt query and records nothing else.

    A stub rather than the live database, and the reason is what is under test: the
    decision to skip is made from two values - the source's schedule and its newest
    `fetch_runs.started_at` - and handing those in directly is what lets the test ask
    the question at a chosen moment. `monitor/schedule.py` proves the arithmetic on
    its own; this proves `fetch()` acts on it, and that naming a source bypasses it.
    """

    def __init__(self, attempts: dict[str, object]) -> None:
        self.attempts = attempts

    def execute(self, query, params=None):
        assert "fetch_runs" in query, "the only query fetch() should make here"
        return self

    def fetchall(self):
        return list(self.attempts.items())


def test_fetch_all_skips_a_source_that_has_been_read_since_its_last_fire(monkeypatch):
    """The defect this whole change exists to fix, in one assertion.

    Before it, an hourly wake read every enabled source, so a source asking for
    `30 10 * * *` was fetched twenty-four times a day against a host that asked for
    one (decision 49).
    """
    from datetime import UTC, datetime

    import monitor.fetch as fetch_module

    fetched: list[str] = []

    def record(conn, src):
        fetched.append(src.id)
        return fetch_module.FetchResult(source_id=src.id, seen=1, new=0, failed=False)

    monkeypatch.setattr(fetch_module, "fetch_source", record)

    now = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    read_after_todays_fire = {
        src.id: datetime(2026, 9, 12, 13, 0, tzinfo=UTC) for src in fetch_module.load_sources() if src.enabled
    }

    results = fetch_module.fetch(Recording(read_after_todays_fire), "all", now=now)

    assert results, "every enabled source should still appear in the results"
    assert all(result.skipped for result in results), "none was due; all should be skipped"
    assert fetched == [], "and none should have been fetched"


def test_fetch_all_reads_a_source_that_has_not_been_read_since_its_last_fire(monkeypatch):
    from datetime import UTC, datetime

    import monitor.fetch as fetch_module

    monkeypatch.setattr(
        fetch_module,
        "fetch_source",
        lambda conn, src: fetch_module.FetchResult(source_id=src.id, seen=1, new=0, failed=False),
    )

    now = datetime(2026, 9, 12, 23, 59, tzinfo=UTC)
    stale = {src.id: datetime(2026, 9, 1, 0, 0, tzinfo=UTC) for src in fetch_module.load_sources() if src.enabled}

    results = fetch_module.fetch(Recording(stale), "all", now=now)

    assert results and not any(result.skipped for result in results)


def test_naming_a_source_ignores_the_schedule(monkeypatch):
    """`make fetch S=ted` is a person asking now, and must not answer "not due".

    The politeness rule is about the unattended cadence. A person recording a fixture
    or checking whether a portal is back is inside it, and a command that refused them
    because a cron expression written for a scheduler said so would be obeying the
    wrong audience.
    """
    from datetime import UTC, datetime

    import monitor.fetch as fetch_module

    monkeypatch.setattr(
        fetch_module,
        "fetch_source",
        lambda conn, src: fetch_module.FetchResult(source_id=src.id, seen=7, new=0, failed=False),
    )

    just_read = {"ted": datetime(2026, 9, 12, 13, 59, tzinfo=UTC)}

    results = fetch_module.fetch(Recording(just_read), "ted", now=datetime(2026, 9, 12, 14, 0, tzinfo=UTC))

    assert [(r.source_id, r.seen, r.skipped) for r in results] == [("ted", 7, False)]


def test_a_source_never_fetched_is_read_even_when_nothing_else_is_due(monkeypatch):
    """A source enabled today must not wait for tomorrow's fire to be read once."""
    from datetime import UTC, datetime

    import monitor.fetch as fetch_module

    monkeypatch.setattr(
        fetch_module,
        "fetch_source",
        lambda conn, src: fetch_module.FetchResult(source_id=src.id, seen=1, new=0, failed=False),
    )

    enabled = [src.id for src in fetch_module.load_sources() if src.enabled]
    now = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)
    # everything read an hour ago except one source, which has never been read at all
    attempts = {src: datetime(2026, 9, 12, 13, 0, tzinfo=UTC) for src in enabled[1:]}

    results = fetch_module.fetch(Recording(attempts), "all", now=now)

    read = [result.source_id for result in results if not result.skipped]
    assert read == [enabled[0]]
