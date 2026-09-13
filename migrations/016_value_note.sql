-- 016_value_note.sql
--
-- A notice that publishes no overall value but does publish per-lot values is
-- not a notice that "states no value", and the record said it was.
--
-- Measured on 2026-09-12 across the 584 BOAMP payloads in storage/boamp/: 22 of
-- the 323 eForms notices carry `EstimatedOverallContractAmount` on one or more of
-- their lots and nothing at procedure level. 012 stores the procedure-level
-- amount only, and deliberately so: summing the lots would be the pipeline's own
-- arithmetic, not the published figure (rule 9), and the publisher's own totals
-- disagree with their lot sums on 12 other notices. So those 22 carry
-- estimated_value NULL - correctly - and the reviewer then reads "not stated",
-- which is false: the value is stated, per lot.
--
-- This adds a text beside the value columns saying what WAS published, derived
-- by rule in the normaliser from the notice's own figures and carried onto the
-- candidate at staging beside estimated_value, the same way the USD figure is.
-- estimated_value and value_currency stay NULL for these notices: the note is a
-- description of the published figures, never a number the pipeline computed.
--
-- `not null default ''` rather than nullable: empty means "nothing to add to
-- the value columns", which is the case for every notice that states a total or
-- states nothing, and it is the same convention `Notice.buyer` and
-- `Notice.body` already use. No new grant: both runtime roles hold table-level
-- privileges on notices and candidates (002), which cover a new column.

alter table notices
    add column value_note text not null default '';

comment on column notices.value_note is
    'What the notice published about its value when estimated_value cannot carry it, '
    'e.g. per-lot amounts with no overall total. Derived by rule from the original, '
    'quoting the published figures. Empty when estimated_value says it all.';

alter table candidates
    add column value_note text not null default '';

comment on column candidates.value_note is
    'Carried from the primary notice at staging, beside estimated_value.';
