"""UNDP has no contract to test, and this file asserts that on purpose.

Every other file in this directory proves a parser against a response recorded
from a real call. There is no recorded UNDP response, because
procurement-notices.undp.org answers every robot with `Disallow: /` on every path
and rule 21 says respect robots.txt. The full measurement is in the comment block
at the top of sources/undp.yaml; scripts/record_undp_fixture.py re-runs it.

So what is worth testing is the absence itself. The risk this file exists against
is a specific and likely one: `undp` is in the registry with a name, a geography
and a schedule, so a later session skims it, sees a source that looks ready, and
builds the connector. The prose saying why not is 200 lines up in a YAML comment.
These four tests put the same statement somewhere that fails a run.

They are all offline. A test that fetched UNDP to prove UNDP may not be fetched
would be the thing it is checking for.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from monitor.registry import load_sources

pytestmark = pytest.mark.contract

REPO = Path(__file__).resolve().parents[2]
SOURCE_YAML = REPO / "sources" / "undp.yaml"

# What a build would add, and what therefore must not exist while the source is
# blocked on a terms decision rather than on engineering.
WOULD_EXIST_IF_BUILT = (
    REPO / "monitor" / "connectors" / "undp.py",
    REPO / "monitor" / "normalise" / "undp.py",
)


def test_the_source_is_registered_and_disabled():
    """Registered so the assessment is not lost, disabled because it may not be read."""
    undp = {source.id: source for source in load_sources()}["undp"]

    assert undp.enabled is False, (
        "sources/undp.yaml was enabled. procurement-notices.undp.org disallows every "
        "robot on every path; enabling this source schedules a rule 21 violation. "
        "Read the measurement block in sources/undp.yaml first."
    )
    assert undp.tos_status == "reviewed_restricted"
    assert undp.admin_level == "donor"
    assert undp.country == "multi"


def test_no_connector_was_built():
    """The blocker is permission, not effort, so there is nothing to parse yet."""
    built = [path for path in WOULD_EXIST_IF_BUILT if path.exists()]

    assert not built, (
        f"{[str(path.relative_to(REPO)) for path in built]} exist(s), but sources/undp.yaml "
        "is still reviewed_restricted. A connector pointed at a host that disallows every "
        "robot is a rule 21 finding on the day it is committed, whatever it parses. "
        "Unblock the source first (sources/undp.yaml names the three options), then build."
    )


def test_no_fixture_was_invented():
    """An undp fixture could only have come from a request that must not be made.

    Any file, any extension: json, html, xml. The name is what a later session would
    trust, and there is nothing honest that could be under it.
    """
    invented = sorted(path.name for path in (Path(__file__).parent / "fixtures").glob("undp.*"))

    assert not invented, (
        f"tests/contract/fixtures/{invented} exists. No UNDP response has ever been fetched - "
        "only /robots.txt, which is the request you make in order to obey it. A fixture here "
        "was either fetched against rule 21 or written from documentation memory, and "
        "CLAUDE.md forbids both."
    )


def test_the_advertised_feed_urls_are_not_recorded_as_routes():
    """UNDP advertises RSS feeds for these notices; the registry must not carry one.

    The conflict between a publisher advertising syndication and disallowing every
    robot is real, and sources/undp.yaml sets it out as a decision for a named
    person. What must not happen meanwhile is one of those URLs sitting in
    `api_url` or `list_url`, where a later session would find it and trust it -
    the same reason sources/mcc.yaml keeps the dgMarket URL out of its entry.

    `list_url` is the portal root, which is the source's identity. That is allowed
    and is annotated in the file as not to be fetched; a .cfm or .xml endpoint is
    not.
    """
    entry = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    routes = [entry.get("list_url", ""), entry.get("api_url", "")]

    assert entry.get("list_url") == "https://procurement-notices.undp.org/"
    for route in routes:
        assert not route.endswith((".cfm", ".xml", ".rss")), (
            f"sources/undp.yaml records {route!r} as a route. The advertised UNDP feeds have "
            "never been fetched and sit on an origin that disallows every robot; recording one "
            "here would get it trusted twice over."
        )
