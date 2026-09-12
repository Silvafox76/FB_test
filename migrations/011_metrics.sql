-- 011_metrics.sql
--
-- The table the weekly metrics job writes and the review app's metrics page reads
-- (BUILD_ORDER step 21).
--
-- **One row per metric per run, not one column per metric.** Step 21 asks for
-- precision, recall, the translation rejection rate and the connector break rate,
-- and on the day this was written two of those four cannot be computed at all: the
-- golden set is exported but unlabelled, and step 21 has Matthew and the project
-- lead label it, not the agent. A wide table would hold those as NULL columns, and
-- a NULL on a dashboard renders as a blank cell that every reader has to guess at
-- - the same failure CLAUDE.md's export section refuses for BD-only fields. A long
-- table gives every metric somewhere to say *why* it has no number, in a column
-- that is as required as the number would have been.
--
-- The constraint below is the whole judgement of this step, written where it
-- cannot be argued with: a metric is either measurable and carries a value, or it
-- is not measurable and carries no number anywhere - not a value, not a numerator,
-- not a denominator - and must carry a reason. A precision figure derived from
-- nothing is worse than no precision figure, so the database refuses to store one.
-- `monitor/health/metrics.py` checks the same invariant when it builds a Metric, for
-- the same reason 006 and 009 check config kinds in both places: the loader gives the
-- better message, the constraint is what makes it true.
--
-- Append only, like `events`: no role is granted update or delete. A measurement
-- that turned out to be wrong is part of the record, and the next run's row is how
-- it is corrected.

create table metrics (
    run_id      uuid not null,                        -- one metrics run; every row of it shares this
    at          timestamptz not null default now(),   -- when the run read the database
    window_from timestamptz not null,                 -- rates are over [window_from, window_to)
    window_to   timestamptz not null,
    metric      text not null,                        -- the key in monitor/health/metrics.py's DEFINITIONS
    measurable  boolean not null,
    value       numeric,                              -- null exactly when measurable is false
    numerator   bigint,                               -- what was counted
    denominator bigint,                               -- what it was counted out of
    unit        text not null check (unit in ('rate', 'count', 'days')),
    note        text not null,                        -- why there is no number, or what this reading is of
    primary key (run_id, metric),
    constraint metrics_unmeasurable_carries_a_reason_and_no_number check (
        case when measurable
             then value is not null
             else value is null and numerator is null and denominator is null and note <> ''
        end
    )
);

-- The page reads the newest run and, per metric, the newest reading before it.
create index metrics_metric_at_idx on metrics (metric, at desc);

-- The metrics job runs as the pipeline: it is a scheduled job, not a reviewer
-- action. It reads `approved_records` for the export backlog on a separate
-- monitor_readonly connection, because rule 11 leaves monitor_pipeline no
-- privilege on that table and connecting the job as monitor_review to get at it is
-- the blocking case. Same two-connection shape as `monitor status`.
grant select, insert on metrics to monitor_pipeline;
grant select on metrics to monitor_review, monitor_readonly;
