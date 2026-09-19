-- 017_review_tag.sql
--
-- The first reviewer pass (decision 68, 2026-09-19) came back with three words for
-- two statuses: approve, reject, and "monitor" on eight rows the reviewer wanted kept
-- in view without committing to a bid. Decision 69 settles it: there are two decisions
-- and only two. "Monitor" is an approval carrying a tag, and the tag is read at the
-- export step, where the operator reconfirms relevance and includes or excludes each
-- record from the batch by hand.
--
-- The tag sits on the approved record, not the candidate, because it is a property of
-- the approval: it is written in the same transaction, by the same single write path
-- (rule 12), and it travels with the record to the export page. `not null default ''`
-- follows 016's convention: empty means an ordinary approval. The allowed values are
-- the list in config/review.yaml (`approval_tags`), validated there on every approve;
-- the check here is the database saying the same thing, as 008 does for the rejection
-- reason. No new grant: monitor_review already holds insert on approved_records and
-- select for both runtime roles covers the column.
--
-- Numbering: BUILD_ORDER.md reserved 017 for regions and 018 for users before this
-- was needed; those steps now say 018 and 019.

alter table approved_records
    add column review_tag text not null default ''
        constraint approved_records_review_tag check (review_tag in ('', 'monitor'));

comment on column approved_records.review_tag is
    'Empty for an ordinary approval. ''monitor'' marks an approval the reviewer wants '
    'kept in view and reconfirmed at export (decision 69). Values: config/review.yaml approval_tags.';
