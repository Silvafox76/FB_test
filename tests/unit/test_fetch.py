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
    """There is no generic fallback connector (rule 1)."""
    with pytest.raises(KeyError, match="no connector for source"):
        build_connector(source("prozorro"))


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
