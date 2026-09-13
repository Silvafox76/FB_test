"""The registry loads or it fails naming the file and the field. No third outcome.

Configuration feeds almost every later stage, so a bad field has to surface here
rather than three stages downstream as a missing key (rule 4).
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from monitor.registry import (
    RegistryError,
    config_files,
    load_function_map,
    load_lexicon,
    load_sources,
    load_system_names,
)
from monitor.registry.load import CONFIG_DIR, SOURCES_DIR

# The four the weekend slice proved end to end against their live APIs. Later waves
# add more, so this is a floor and not the whole set: the test below asserts these
# are present AND that every file in sources/ loads, rather than pinning a list that
# every new connector would have to come back and edit.
WEEKEND_SOURCES = {"ted", "prozorro", "fts", "worldbank"}


@pytest.fixture
def sources_copy(tmp_path):
    """A writable copy of sources/, so a test can break one file and see what happens."""
    directory = tmp_path / "sources"
    shutil.copytree(SOURCES_DIR, directory)
    return directory


def test_every_registry_file_loads_and_the_weekend_four_are_among_them():
    """Two things, because either alone would miss the point.

    Every YAML in sources/ has to load and validate, so a malformed entry added by a
    later wave fails here rather than three stages downstream (rule 4). And the four
    sources the weekend slice proved against live APIs have to still be there, so a
    later edit cannot quietly drop one.
    """
    sources = {source.id for source in load_sources()}
    on_disk = {path.stem for path in SOURCES_DIR.glob("*.yaml")}

    assert sources == on_disk, "every registry file loads, and the loader invents none"
    assert WEEKEND_SOURCES <= sources, f"missing: {sorted(WEEKEND_SOURCES - sources)}"


def test_every_enabled_source_is_a_feed_connector():
    """Scoped to enabled sources, because that is what "running" means.

    A registry entry may be written ahead of its connector - source-onboarder
    assesses a source before anyone builds it, and that assessment is the entry - so
    a disabled `BrowserConnector` row is the onboarding having happened, not a
    browser having arrived. What would break the rule is one of those running, and
    `enabled` is what decides that.

    This test used to also assert that no `BrowserConnector` existed in the registry
    at all, which contradicted the paragraph above and was really a "Playwright
    arrives at step 17 and not before" guard. Step 17 has arrived:
    `monitor/connectors/browser_base.py` is built and the portals that need it are
    being onboarded, so the guard is gone and only the scoped assertion remains.
    Enabling a browser source is still a deliberate act that fails this test until
    the line below is changed with it.
    """
    running = {source.connector_class for source in load_sources() if source.enabled}

    assert running == {"FeedConnector"}


def test_a_misspelled_field_fails_naming_the_field(sources_copy):
    path = sources_copy / "ted.yaml"
    document = yaml.safe_load(path.read_text())
    document["admin_levle"] = document.pop("admin_level")
    path.write_text(yaml.safe_dump(document))

    with pytest.raises(RegistryError) as raised:
        load_sources(sources_copy)

    message = str(raised.value)
    assert "ted.yaml" in message
    assert "admin_levle" in message or "admin_level" in message


def test_an_out_of_vocabulary_value_fails_naming_the_field(sources_copy):
    path = sources_copy / "fts.yaml"
    document = yaml.safe_load(path.read_text())
    document["connector"] = "ScrapyConnector"
    path.write_text(yaml.safe_dump(document))

    with pytest.raises(RegistryError) as raised:
        load_sources(sources_copy)

    assert "fts.yaml" in str(raised.value)
    assert "connector" in str(raised.value)


def test_a_duplicate_id_fails_naming_both_files(sources_copy):
    document = yaml.safe_load((sources_copy / "ted.yaml").read_text())
    document["name"] = "A second registry entry claiming the same id"
    (sources_copy / "ted_copy.yaml").write_text(yaml.safe_dump(document))

    with pytest.raises(RegistryError) as raised:
        load_sources(sources_copy)

    message = str(raised.value)
    assert "duplicate source id" in message
    assert "ted" in message


def test_an_empty_directory_fails(tmp_path):
    """Zero sources is a broken checkout, not an empty success (rule 4)."""
    with pytest.raises(RegistryError):
        load_sources(tmp_path)


def test_the_function_map_has_the_thirty_three_functions():
    functions = load_function_map()

    assert len(functions) == 33
    assert len({function["pillar"] for function in functions}) == 8


def test_every_function_has_a_weight_from_the_lookup():
    type_weights = set(yaml.safe_load((CONFIG_DIR / "function_map.yaml").read_text())["type_weights"].values())

    assert {function["type_weight"] for function in load_function_map()} <= type_weights | {1.0}


def test_pillar_eight_has_no_product_and_says_why():
    """The workbook has no product mapping for Government Service Delivery."""
    unmapped = [function for function in load_function_map() if not function["product"]]

    assert unmapped, "expected at least the pillar 8 functions to have no product"
    for function in unmapped:
        assert function["product_note"], f"{function['function_id']} has no product and no note"


def test_both_lexicons_cover_every_function():
    function_ids = {function["function_id"] for function in load_function_map()}

    for language in ("en", "fr"):
        phrases, content_hash = load_lexicon(language)
        assert set(phrases) == function_ids
        assert len(content_hash) == 64


def test_every_config_and_source_file_is_version_hashed():
    """Rule 6: every file under sources/ and config/ is traceable, not just the lexicons."""
    on_disk = set(SOURCES_DIR.glob("*.yaml")) | set(CONFIG_DIR.glob("*.yaml"))

    assert {path for path, _, _ in config_files()} == on_disk


def test_the_config_kinds_match_the_databases_own_vocabulary():
    """Two enforcement points that must agree, so the disagreement is a test.

    config_versions.kind is constrained in the database because reporting and the
    ops-analyst read it without going through the loader. Adding a config kind
    therefore needs both a CONFIG_KINDS entry and a migration; this fails in the
    suite rather than at the next `make up`.
    """
    from pathlib import Path as _Path

    from monitor.registry.load import CONFIG_KINDS, REPO

    migrations = sorted((_Path(REPO) / "migrations").glob("*.sql"))
    constraint = ""
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        if "config_versions_kind_check" in text:
            constraint = text  # the last migration to define it wins, as in the database

    allowed = {kind for kind in set(CONFIG_KINDS.values()) | {"source"} if f"'{kind}'" in constraint}

    assert allowed == set(CONFIG_KINDS.values()) | {"source"}, (
        "a config kind is missing from the config_versions check constraint; add a migration"
    )


def test_a_config_file_with_no_declared_kind_fails(tmp_path):
    """A new file in config/ has to be classified, or it stops being traceable silently.

    Run against a copy. Writing the stray file into the real config/ and cleaning
    up in a finally works until a run is killed, and then it fails for everyone.
    """
    config_copy = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, config_copy)
    (config_copy / "stray_for_test.yaml").write_text("nothing: here\n")

    with pytest.raises(RegistryError) as raised:
        config_files(SOURCES_DIR, config_copy)

    assert "CONFIG_KINDS" in str(raised.value)
    assert "stray_for_test.yaml" in str(raised.value)


def test_a_lexicon_declaring_the_wrong_language_fails(tmp_path):
    path = tmp_path / "lexicon_fr.yaml"
    path.write_text(yaml.safe_dump({"language": "en", "functions": {"policy_management": ["policy"]}}))

    with pytest.raises(RegistryError) as raised:
        load_lexicon("fr", path)

    assert "language" in str(raised.value)


def test_every_system_name_in_claude_md_appears_in_the_english_lexicon():
    """The system names are the strongest single signal the free filter has.

    A name in config/system_names.yaml but not in a lexicon is checked by the
    translation's acronym check and never matched by the filter, which is a real
    gap rather than a tidiness one.
    """
    system_names = load_system_names()
    phrases, _ = load_lexicon("en")
    blob = " ".join(phrase for phrases_for_function in phrases.values() for phrase in phrases_for_function).lower()

    missing = [name for name in system_names if name.lower() not in blob]
    assert not missing, f"system names absent from the English lexicon: {missing}"


def test_product_mapping_reproduces_the_appendix_e_examples():
    """Architecture v0.4 appendix E names these pairs; the generator must agree.

    Each function has many components and each component carries its own status for
    the same product, so the generator keeps the best status across the function.
    Appendix E reads 4.2 as "Available; not Available" across its two products,
    which is only true under that rule.
    """
    by_number = {function["name"].split()[0].rstrip("."): function for function in load_function_map()}

    assert by_number["4.2"]["product_status"] == {
        "Public Debt and Guarantee Management": "Available",
        "Public Financial Investments": "Low Priority",
    }
    assert by_number["6.1"]["product"] == [
        "Revenue and Receipts Mobilization",
        "Tax Administration Mobilization",
    ]
    assert by_number["7.3"]["product"] == [
        "Payroll and Wage Bills",
        "Public Service Talent and Capital Human Capital",
    ]
    assert by_number["5.1"]["product_status"]["Electronic Public Procurement"] == "Available"
    assert by_number["2.3"]["product_status"]["Core Financial Execution and Reporting"] == "Available"


def test_no_config_key_parses_as_a_boolean():
    """`NO: Europe` is `False: Europe` in YAML 1.1, and Norway vanishes silently.

    YAML 1.1 treats NO, ON, OFF, YES, TRUE and FALSE as booleans when unquoted. `NO`
    is the only ISO 3166-1 alpha-2 code that collides, and it is a real one: Norway is
    in the EEA, carries 26 notices in the corpus, and belongs to the 0.6 band.

    This happened while `regions` was being given an explicit Europe list, and it
    failed the way config errors do — nothing raised, the key was simply a different
    key, and Norway fell through to the default. Pinned here rather than in a comment
    because a comment does not fail a build.
    """
    document = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))

    for block in ("geography", "regions"):
        coerced = [key for key in document[block] if not isinstance(key, str)]
        assert not coerced, f"{block} has non-string keys {coerced}; quote them"

    assert document["regions"]["NO"] == "Europe"


# --- record_defaults.yaml is validated on load like every other config file ------


def test_the_shipped_record_defaults_load():
    from monitor.registry.load import load_record_defaults

    document = load_record_defaults()

    assert document["value_basis"] in ("published", "usd")
    assert len(document["columns"]) == 73


def test_a_bad_value_basis_fails_on_load_not_on_a_reviewers_page(tmp_path):
    """design-cop on 7f11b3f: the check lived only in build_record, so a typo in the
    YAML passed `make up` and surfaced when a reviewer opened a candidate."""
    import yaml

    from monitor.registry.load import RECORD_DEFAULTS, RegistryError, load_record_defaults

    document = yaml.safe_load(RECORD_DEFAULTS.read_text(encoding="utf-8"))
    document["value_basis"] = "dollars please"
    path = tmp_path / "record_defaults.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    # Pydantic names the field and lists the allowed values; the file name comes
    # from the registry's own wrapper.
    with pytest.raises(RegistryError, match=r"record_defaults\.yaml: invalid field\(s\) value_basis"):
        load_record_defaults(path)


def test_a_key_the_record_builder_does_not_read_is_refused(tmp_path):
    """extra="forbid" on every section: a stale or misspelt key fails on load (rule 4)."""
    import yaml

    from monitor.registry.load import RECORD_DEFAULTS, RegistryError, load_record_defaults

    document = yaml.safe_load(RECORD_DEFAULTS.read_text(encoding="utf-8"))
    document["suggested"]["currncy"] = "USD"
    path = tmp_path / "record_defaults.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RegistryError, match="suggested.currncy"):
        load_record_defaults(path)


def test_two_columns_with_one_header_are_refused(tmp_path):
    """Zoho's import mapper matches on headers; a duplicate would map one of them to nothing."""
    import yaml

    from monitor.registry.load import RECORD_DEFAULTS, RegistryError, load_record_defaults

    document = yaml.safe_load(RECORD_DEFAULTS.read_text(encoding="utf-8"))
    document["columns"].append({"name": "Currency", "category": "S"})
    path = tmp_path / "record_defaults.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RegistryError, match="appears twice"):
        load_record_defaults(path)


def test_a_lookup_table_without_a_default_is_refused(tmp_path):
    """record.py falls back to `default` for a region or stream it has not seen."""
    import yaml

    from monitor.registry.load import RECORD_DEFAULTS, RegistryError, load_record_defaults

    document = yaml.safe_load(RECORD_DEFAULTS.read_text(encoding="utf-8"))
    del document["industry_by_region"]["default"]
    path = tmp_path / "record_defaults.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RegistryError, match="industry_by_region has no 'default'"):
        load_record_defaults(path)


def test_a_missing_value_sentence_fails_on_load(tmp_path):
    import yaml

    from monitor.registry.load import RECORD_DEFAULTS, RegistryError, load_record_defaults

    document = yaml.safe_load(RECORD_DEFAULTS.read_text(encoding="utf-8"))
    del document["sentences"]["value_in_target"]
    path = tmp_path / "record_defaults.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")

    with pytest.raises(RegistryError, match="value_in_target"):
        load_record_defaults(path)


def test_the_registrys_sentence_list_is_exactly_what_the_record_builder_reads():
    """design-cop on 67954a7: a hand-kept list of five keys was already short of the
    nine record.py reads. Read the source and compare, so the two cannot drift."""
    import re
    from pathlib import Path

    from monitor.registry.load import RECORD_SENTENCE_KEYS

    source = (Path(__file__).parents[2] / "monitor" / "stage" / "record.py").read_text(encoding="utf-8")
    # Sees exactly one form: a literal key subscripted directly. Every read in
    # record.py is written that way today, and this test only holds while that
    # stays true - `sentences.get("x")` or a key held in a variable would add a
    # dependency this regex never sees (design-cop on dbe6e71). If record.py ever
    # needs another form, extend the pattern here in the same change.
    read_in_code = set(re.findall(r'sentences\["([a-z_]+)"\]', source))

    assert read_in_code == RECORD_SENTENCE_KEYS
