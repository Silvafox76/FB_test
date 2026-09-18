"""The TED backfill walks a window day by day through the real connector and insert.

Against the committed TED fixture served by an httpx MockTransport, never the
network: what is under test is the window each day asks for, that every request
carries the identified user agent, that a day's notices go through the same
`_store_notices` as a scheduled pass, and that a second walk over the same days
inserts nothing.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from monitor.connectors.ted import TedConnector
from monitor.fetch import build_connector
from monitor.registry import load_sources
from scripts.backfill_ted import backfill_day, days_between

FIXTURE = Path(__file__).parent.parent / "contract" / "fixtures" / "ted.json"


@pytest.fixture
def source():
    return next(s for s in load_sources() if s.id == "ted")


@pytest.fixture
def recorded():
    """Every request the mock served: (query, page) pairs, and the user agent seen."""
    return {"queries": [], "agents": set()}


@pytest.fixture
def client(recorded):
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # The fixture is exactly one full page, so the connector asks for a second;
    # serve it empty, the way the API ends a result set.
    empty = {**document, "notices": []}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        recorded["queries"].append((body["query"], body["page"]))
        recorded["agents"].add(request.headers["User-Agent"])
        return httpx.Response(200, json=document if body["page"] == 1 else empty)

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "FreeBalance-OpportunityMonitor/0.1 (+test)"},
    )


def test_days_between_is_inclusive_and_refuses_a_backwards_range():
    three = [date(2026, 8, 24), date(2026, 8, 25), date(2026, 8, 26)]
    assert days_between(date(2026, 8, 24), date(2026, 8, 26)) == three
    assert days_between(date(2026, 8, 24), date(2026, 8, 24)) == [date(2026, 8, 24)]
    with pytest.raises(ValueError, match="before"):
        days_between(date(2026, 8, 26), date(2026, 8, 24))


def test_the_window_names_one_day_on_both_sides_and_keeps_the_registrys_exclusions(source):
    query = TedConnector(source, ["48", "72", "79"]).query(since=date(2026, 8, 24), until=date(2026, 8, 24))

    assert "publication-date>=20260824" in query
    assert "publication-date<=20260824" in query
    assert "classification-cpv=48*" in query
    for excluded in source.exclude_notice_types:
        assert excluded in query


def test_the_scheduled_query_is_unchanged_when_no_window_is_given(source):
    """The pass never passes a window; its query is the trailing two days, no upper bound."""
    query = TedConnector(source, ["48"]).query(today=date(2026, 9, 12))

    assert "publication-date>=20260910" in query
    assert "publication-date<=" not in query


def test_a_day_goes_through_the_real_insert_and_a_second_walk_is_seen_not_new(db_conn, source, client, recorded):
    connector, mapper = build_connector(source)

    first = backfill_day(db_conn, source, connector, mapper, client, date(2026, 8, 24))
    second = backfill_day(db_conn, source, connector, mapper, client, date(2026, 8, 24))

    # The fixture's notices may already be held from the pass that recorded it, so
    # `new` on the first walk is whatever is not yet there; the second walk is the
    # assertion that matters.
    assert first.fetched == len(json.loads(FIXTURE.read_text(encoding="utf-8"))["notices"])
    assert first.seen == first.fetched
    assert 0 <= first.new <= first.seen
    assert second.seen == first.seen
    assert second.new == 0
    expected = connector.query(since=date(2026, 8, 24), until=date(2026, 8, 24))
    assert {q for q, _ in recorded["queries"]} == {expected}
    assert [page for _, page in recorded["queries"]] == [1, 2, 1, 2]
    assert recorded["agents"] == {"FreeBalance-OpportunityMonitor/0.1 (+test)"}
