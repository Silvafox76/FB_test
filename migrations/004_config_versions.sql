-- 004_config_versions.sql
--
-- Rule 6 says sources, both lexicons, thresholds, geography weights, the 33
-- functions, the product mapping and the record defaults are "version-hashed in
-- the database". Only the lexicons were: 001 defined lexicon_versions and nothing
-- else. From step 5 the thresholds decide what is dropped before any model call,
-- and from step 10 the record defaults decide what is exported, so a run has to be
-- traceable to the numbers that produced it.
--
-- config_versions generalises lexicon_versions rather than sitting beside it:
-- one table, one row per config file per content hash, one way to ask the
-- question (rule 1). The lexicon rows are carried across and the old table is
-- dropped in the same transaction.

create table config_versions (
    version      text primary key,            -- '<file stem>-<first 12 of the hash>', readable in a log line
    path         text not null,               -- repository-relative, e.g. config/thresholds.yaml
    kind         text not null check (
                     kind in ('source', 'lexicon', 'function_map', 'thresholds', 'record_defaults')),
    language     text,                        -- lexicons only; null for everything else
    content_hash text not null,               -- sha256 of the file as read
    applied_at   timestamptz not null default now(),
    unique (path, content_hash)
);

create index config_versions_path_applied_idx on config_versions (path, applied_at desc);

-- The version string is rebuilt on the new scheme, '<file stem>-<hash>', so every
-- row in the table reads the same way whatever kind of config it records.
insert into config_versions (version, path, kind, language, content_hash, applied_at)
select 'lexicon_' || language || '-' || left(content_hash, 12),
       'config/lexicon_' || language || '.yaml',
       'lexicon',
       language,
       content_hash,
       applied_at
from lexicon_versions;

drop table lexicon_versions;

grant select, insert on config_versions to monitor_pipeline;
grant select on config_versions to monitor_review, monitor_readonly;
