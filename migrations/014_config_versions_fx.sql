-- 014_config_versions_fx.sql
--
-- config/fx.yaml holds the rate publisher, the base and target currencies, the
-- statutory CFA pegs, the staleness tolerance and the rounding mode. Every one of
-- those is a number or a name the conversion depends on, so rule 6 keeps them out
-- of monitor/fx/*.py and this makes them version-hashed like the rest of the
-- configuration: a figure stamped on a candidate in week 3 stays attributable to
-- the peg and the tolerance that were in force when it was stamped.
--
-- Same two enforcement points as 006 and 009, and the same reason for both: the
-- loader checks CONFIG_KINDS, the database checks the constraint, and reporting
-- reads the database without going through the loader.

alter table config_versions drop constraint config_versions_kind_check;

alter table config_versions
    add constraint config_versions_kind_check check (
        kind in ('source', 'lexicon', 'function_map', 'thresholds', 'record_defaults',
                 'system_names', 'review', 'fx')
    );
