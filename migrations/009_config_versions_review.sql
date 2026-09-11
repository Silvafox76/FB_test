-- 009_config_versions_review.sql
--
-- config/review.yaml holds the rejection reason picklist, which rule 6 keeps out
-- of review/app.py and which the week 14 gate reads back when it asks why
-- candidates were rejected. Like every other config file it is version-hashed, so
-- a reason added in week 7 is distinguishable from the list the pilot started with.
--
-- Same two enforcement points as 006, and the same reason for both: the loader
-- checks CONFIG_KINDS, the database checks the constraint, and reporting reads the
-- database without going through the loader.

alter table config_versions drop constraint config_versions_kind_check;

alter table config_versions
    add constraint config_versions_kind_check check (
        kind in ('source', 'lexicon', 'function_map', 'thresholds', 'record_defaults',
                 'system_names', 'review')
    );
