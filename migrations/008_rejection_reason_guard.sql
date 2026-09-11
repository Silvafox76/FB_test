-- 008_rejection_reason_guard.sql
--
-- Rule 14: the rejection reason is enforced server side, and "client-side-only
-- validation is a finding". 002's trigger enforced rule 13's named reviewer and
-- stopped there, so a rejection with a blank reason was refused by nothing below
-- the form. The review app checks it too, but a check that lives only in the
-- application is exactly the shape rule 14 names.
--
-- The reason is what the week 14 gate reads: a queue of rejections with no reason
-- cannot be tuned against, because nobody can tell a wrong-country rejection from
-- a wrong-scope one afterwards.
--
-- Same trigger, one more clause. The function is replaced rather than a second
-- trigger added: one guard, one place to read it (rule 1).

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
        if new.status = 'rejected' and (new.rejection_reason is null or btrim(new.rejection_reason) = '') then
            raise exception
                'candidate % cannot be rejected without a reason',
                new.id
                using errcode = 'check_violation';
        end if;
    end if;
    new.updated_at := now();
    return new;
end;
$$;
