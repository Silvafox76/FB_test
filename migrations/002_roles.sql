-- 002_roles.sql
-- The checkpoint, as a set of grants rather than a promise about a script.
--
-- Three roles and only three (rule 11):
--   monitor_pipeline  acquires, normalises, filters, scores, dedupes, stages.
--                     No privilege of any kind on approved_records or export_batches.
--   monitor_review    the reviewer's app. Reads everything, updates candidates,
--                     inserts approved records, export batches and events.
--   monitor_readonly  reporting and the ops-analyst agent. Select and nothing else.
--
-- No password appears in this file (rule 20). The roles are created with LOGIN and
-- no password; monitor/migrate.py sets each one from the environment after the
-- migrations have applied, and re-sets them on every run so .env and the database
-- cannot drift apart.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'monitor_pipeline') then
        create role monitor_pipeline login;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'monitor_review') then
        create role monitor_review login;
    end if;
    if not exists (select 1 from pg_roles where rolname = 'monitor_readonly') then
        create role monitor_readonly login;
    end if;
end
$$;

grant usage on schema public to monitor_pipeline, monitor_review, monitor_readonly;

-- Start from nothing. Everything below is granted deliberately and can be read
-- off in one screen, which is the point: the design-cop and the step 22 security
-- review both check the deployed grants, not this file's intentions.
revoke all on all tables in schema public from monitor_pipeline, monitor_review, monitor_readonly;
revoke all on all sequences in schema public from monitor_pipeline, monitor_review, monitor_readonly;

-- monitor_pipeline: every table it has to write, named one by one. A table added
-- by a later migration gets no privilege until that migration grants it, which is
-- a loud failure rather than a quiet widening of the pipeline's reach.
grant select, insert, update on
    sources,
    fetch_runs,
    notices_raw,
    notices,
    translations,
    scores,
    candidates,
    candidate_notices,
    events,
    source_health,
    lexicon_versions,
    function_map,
    model_calls
to monitor_pipeline;

grant usage on sequence candidate_id_seq, events_id_seq, model_calls_id_seq to monitor_pipeline;

-- Said explicitly so it survives a careless later migration and so a reader does
-- not have to infer the absence of a grant from its omission.
revoke all on approved_records, export_batches from monitor_pipeline;
revoke all on sequence approved_record_id_seq, export_batch_id_seq from monitor_pipeline;

-- monitor_review: reads everything, writes only what a decision and an export
-- produce. No delete anywhere; the audit log is append only by grant.
grant select on all tables in schema public to monitor_review;
grant update on candidates to monitor_review;
grant insert on approved_records, export_batches, events to monitor_review;

-- The export stamps a record as exported. It may change those two columns and no
-- others: a reviewer's decision is not editable after the fact, and the record
-- payload that left the system is what BD imported.
grant update (exported_at, export_batch) on approved_records to monitor_review;

grant usage on sequence approved_record_id_seq, export_batch_id_seq, events_id_seq to monitor_review;

-- monitor_readonly: select, and nothing else, ever.
grant select on all tables in schema public to monitor_readonly;

-- Rule 13, enforced in the database rather than in the application: a candidate
-- reaches approved or rejected only through the reviewer's role, and only with a
-- named reviewer on the row. The pipeline may set pending_review; it may not
-- decide anything.
create or replace function refuse_pipeline_decision() returns trigger
language plpgsql
as $$
begin
    if new.status in ('approved', 'rejected') and new.status is distinct from old.status then
        if current_user <> 'monitor_review' then
            raise exception
                'candidate % cannot be set to % by %: only monitor_review decides',
                new.id, new.status, current_user
                using errcode = 'insufficient_privilege';
        end if;
        if new.reviewer is null or btrim(new.reviewer) = '' then
            raise exception
                'candidate % cannot be set to % without a named reviewer',
                new.id, new.status
                using errcode = 'check_violation';
        end if;
    end if;
    new.updated_at := now();
    return new;
end;
$$;

create trigger candidates_decision_guard
    before update on candidates
    for each row
    execute function refuse_pipeline_decision();
