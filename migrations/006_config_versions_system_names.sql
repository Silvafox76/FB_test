-- 006_config_versions_system_names.sql
--
-- config_versions.kind is checked against a fixed vocabulary, and
-- monitor/registry/load.py's CONFIG_KINDS is checked against the same one. Two
-- enforcement points is deliberate: the database is read by reporting and by the
-- ops-analyst, neither of which goes through the loader. But two points that must
-- agree can drift, so adding config/system_names.yaml failed the seed until this
-- landed, which is the mechanism working rather than a defect.
--
-- The drift is now a test as well (tests/unit/test_registry.py), so the next
-- config kind fails in the suite rather than at the next `make up`.

alter table config_versions drop constraint config_versions_kind_check;

alter table config_versions
    add constraint config_versions_kind_check check (
        kind in ('source', 'lexicon', 'function_map', 'thresholds', 'record_defaults', 'system_names')
    );
