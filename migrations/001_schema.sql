-- 001_schema.sql
-- The pilot's whole schema. Applied once by monitor/migrate.py as the owner role.
-- Roles and grants are 002; nothing here grants anything to anyone.
--
-- Conventions: timestamptz everywhere (the pipeline reads 47 countries and every
-- notice carries its own offset), text rather than varchar(n), jsonb for the
-- model's own output and for the Opportunity-shaped record.

-- Human-readable ids. Candidates are C000001, approved records R000001, export
-- batches B0001. They appear in the export, in the audit log and in BD's CRM, so
-- they are typed and read aloud by people; a uuid would not survive that.
create sequence if not exists candidate_id_seq;
create sequence if not exists approved_record_id_seq;
create sequence if not exists export_batch_id_seq;

create table sources (
    id                      text primary key,
    name                    text not null,
    country                 text not null,            -- ISO code, 'EU' for TED, 'multi' for donor sources
    admin_level             text not null,            -- national | regional | local | donor
    language                text not null,
    stream                  text not null,            -- feed | portal | mail
    access_type             text not null,            -- api | rss | html | pdf | email
    connector_class         text not null,            -- FeedConnector | PageConnector | BrowserConnector | MailConnector
    wave                    int  not null,
    tos_status              text not null,            -- cleared by source-onboarder before the connector is written
    enabled                 boolean not null default false,
    expected_min            int  not null,            -- zero yield below this is a failure state, not an empty success
    expected_max            int  not null,
    max_consecutive_failures int not null
);

create table fetch_runs (
    id          uuid primary key default gen_random_uuid(),
    source_id   text not null references sources (id),
    started_at  timestamptz not null default now(),
    finished_at timestamptz,
    status      text not null,                        -- running | ok | failed
    items_seen  int  not null default 0,
    items_new   int  not null default 0,
    error       text
);

create index fetch_runs_source_started_idx on fetch_runs (source_id, started_at desc);

-- The notice exactly as fetched. content_hash is sha256 of the normalised title
-- and body and is the change-detection key (step 4).
create table notices_raw (
    content_hash text primary key,
    source_id    text not null references sources (id),
    url          text not null,
    fetched_at   timestamptz not null default now(),
    storage_path text not null,
    mime         text not null
);

-- Rule 9: this is the record, in the language it was published in. title and body
-- are never overwritten with a translation; English renderings live in
-- translations and scores, stamped with the model and prompt version.
create table notices (
    id                  uuid primary key default gen_random_uuid(),
    content_hash        text not null unique references notices_raw (content_hash),
    source_id           text not null references sources (id),
    external_id         text,
    url                 text not null,
    title               text not null,
    buyer               text,
    country             text not null,
    admin_level         text not null,
    published_at        timestamptz,
    -- null when the original string could not be parsed by rule. Rule 10: it is
    -- never filled in from a translation; the raw string is logged instead.
    deadline_at         timestamptz,
    language            text not null,
    language_confidence real,
    cpv_codes           text[] not null default '{}',
    estimated_value_usd bigint,
    body                text,
    filter_result       text,                         -- why it was dropped, or what it matched
    status              text not null,                -- vocabulary enforced by 005_notice_filtered_in.sql
    fetched_at          timestamptz not null default now()
);

create index notices_source_status_idx on notices (source_id, status);
create index notices_country_idx on notices (country);

create table translations (
    notice_id      uuid not null references notices (id),
    title_en       text not null,
    body_en        text,
    model          text not null,
    prompt_version text not null,
    latency_ms     int not null,
    cost_usd       numeric(10, 6) not null,
    created_at     timestamptz not null default now(),
    primary key (notice_id, prompt_version)
);

-- One row per model scoring call that returned a valid Score. raw_json is the
-- tool input exactly as returned, kept so a prompt change can be re-measured
-- against what the model actually said.
create table scores (
    id                  uuid primary key default gen_random_uuid(),
    notice_id           uuid not null references notices (id),
    model               text not null,
    prompt_version      text not null,
    relevance           int  not null check (relevance between 0 and 100),
    title_en            text not null,
    matched_functions   jsonb not null,
    system_names        text[] not null default '{}',
    procurement_type    text not null,
    estimated_value_usd bigint,
    eligibility_flags   text[] not null default '{}',
    deadline_at         timestamptz,
    summary_en          text not null,
    confidence          real not null,
    raw_json            jsonb not null,
    tokens_in           int not null,
    tokens_out          int not null,
    latency_ms          int not null,
    cost_usd            numeric(10, 6) not null,
    created_at          timestamptz not null default now()
);

create index scores_notice_idx on scores (notice_id);

-- The reviewer's unit of work. One candidate, one or more notices.
create table candidates (
    id                  text primary key,
    primary_notice_id   uuid not null references notices (id),
    score               int  not null check (score between 0 and 100),
    status              text not null check (
                            status in ('detected', 'filtered_out', 'scored', 'staged',
                                       'pending_review', 'approved', 'rejected')),
    region              text not null,
    language            text not null,
    title_en            text not null,
    buyer               text,
    country             text not null,
    admin_level         text not null,
    summary_en          text not null,
    matched_functions   jsonb not null,
    system_names        text[] not null default '{}',
    procurement_type    text not null,
    estimated_value_usd bigint,
    eligibility_flags   text[] not null default '{}',
    deadline_at         timestamptz,
    -- Set only by the reviewer's decision transaction (review/decisions.py).
    -- The trigger in 002 refuses an approved or rejected status from any other role.
    reviewer            text,
    rejection_reason    text,
    approved_record_id  text,                         -- fk added at the end of this file
    detected_run        uuid references fetch_runs (id),
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    constraint candidates_id_format check (id ~ '^C[0-9]{6}$')
);

create index candidates_status_score_idx on candidates (status, score desc);

create table candidate_notices (
    candidate_id text not null references candidates (id),
    notice_id    uuid not null references notices (id),
    match_method text not null,                       -- content_hash | title_fuzzy | system_name
    match_score  int  not null,
    primary key (candidate_id, notice_id)
);

-- The audit log. Append only by convention and by grant: no role has delete.
create table events (
    id          bigserial primary key,
    entity_type text not null,
    entity_id   text not null,
    action      text not null,
    actor       text not null,
    before      text,
    after       text,
    at          timestamptz not null default now()
);

create index events_entity_idx on events (entity_type, entity_id, at desc);

create table source_health (
    source_id           text primary key references sources (id),
    last_success_at     timestamptz,
    consecutive_failures int not null default 0,
    median_items        real,
    last_zero_yield_at  timestamptz,
    zero_yield_runs     int not null default 0,
    state               text not null default 'unknown'  -- healthy | watch | unhealthy | unknown
);

create table lexicon_versions (
    version      text primary key,
    language     text not null,
    content_hash text not null,
    applied_at   timestamptz not null default now()
);

create table function_map (
    function_id  text primary key,
    name         text not null,
    pillar       text not null,
    type_weight  real not null,
    keywords_en  text[] not null default '{}',
    keywords_fr  text[] not null default '{}'
);

-- The checkpoint's table. monitor_pipeline holds no privilege of any kind on it
-- (002_roles.sql), and exactly one code path inserts into it: review/decisions.py,
-- inside the reviewer's decision transaction. approved_by is not null because a
-- record without a named reviewer is the thing this whole design exists to prevent.
create table approved_records (
    id           text primary key,
    candidate_id text not null unique references candidates (id),
    record       jsonb not null,
    approved_by  text not null check (btrim(approved_by) <> ''),
    edited       boolean not null default false,
    created_at   timestamptz not null default now(),
    exported_at  timestamptz,
    export_batch text,                                -- fk added at the end of this file
    constraint approved_records_id_format check (id ~ '^R[0-9]{6}$')
);

create table export_batches (
    batch_id      text primary key,
    created_at    timestamptz not null default now(),
    operator      text not null check (btrim(operator) <> ''),
    row_count     int  not null,
    range_from    timestamptz not null,
    range_to      timestamptz not null,
    file_path     text not null,
    manifest_path text not null,
    sha256        text not null,
    constraint export_batches_id_format check (batch_id ~ '^B[0-9]{4}$')
);

-- Every model call, including the ones that failed validation. caps.py counts
-- today's rows and sums cost before each call (rule 22). No request body is ever
-- written here (rule 20).
create table model_calls (
    id             bigserial primary key,
    purpose        text not null,                     -- score | translate | rescore
    model          text not null,
    prompt_version text not null,
    tokens_in      int not null default 0,
    tokens_out     int not null default 0,
    cost_usd       numeric(10, 6) not null default 0,
    latency_ms     int not null default 0,
    at             timestamptz not null default now()
);

create index model_calls_at_idx on model_calls (at);

-- The two references that close the loop between a candidate, its approved record
-- and the batch that took the record out of the system. Added here because the
-- tables reference each other.
alter table candidates
    add constraint candidates_approved_record_fk
    foreign key (approved_record_id) references approved_records (id);

alter table approved_records
    add constraint approved_records_export_batch_fk
    foreign key (export_batch) references export_batches (batch_id);
