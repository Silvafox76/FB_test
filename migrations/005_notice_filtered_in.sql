-- 005_notice_filtered_in.sql
--
-- notices.status had four values and needed five. BUILD_ORDER step 5 leaves a
-- notice that passed the filter in what it calls "status scored-pending", and
-- step 6 scores "every notice in status scored-pending from step 5". Those are
-- the same state and the four-value vocabulary has no name for it: 'detected'
-- cannot distinguish a notice nobody has filtered yet from one that passed and is
-- waiting to be scored, and step 6's query has to tell them apart without reading
-- filter_result as a string.
--
-- 'filtered_in' is the name, symmetric with 'filtered_out'. The vocabulary is now
-- a check constraint rather than a comment, so a sixth value cannot arrive by
-- accident the way this fifth one nearly did.

alter table notices
    add constraint notices_status_vocabulary check (
        status in ('detected', 'filtered_out', 'filtered_in', 'scored', 'parked')
    );
