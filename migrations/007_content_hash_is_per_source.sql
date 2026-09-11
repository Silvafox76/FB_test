-- 007_content_hash_is_per_source.sql
--
-- content_hash was globally unique, so the same notice text published by two
-- sources was stored once and the second source's copy was dropped at ingest as
-- "already seen". Three things in the design need it not to be:
--
--   - BUILD_ORDER step 8's first dedupe rule is an exact content-hash match
--     between notices in different candidates. Under a global key that rule can
--     never fire: two notices cannot share a hash.
--   - Step 8's own acceptance says "the same notice text from two sources joins",
--     and step 11's says a World Bank notice duplicating a TED or national notice
--     joins its cluster in a real run. Neither can happen if one of them was never
--     stored.
--   - Architecture v0.4 appendix E derives Partners Involved from "every
--     donor-stream source in the candidate's cluster, named". A World Bank notice
--     collapsed into TED's row leaves the cluster with one notice and the donor
--     invisible, so the export field is silently always empty.
--
-- The hash stays the change-detection key; it is now scoped to the source that
-- published it. A second run over an unchanged source still inserts nothing, which
-- is what `make fetch S=ted` twice proves, and two sources carrying the same tender
-- now produce two notices that the deduper joins into one candidate.

alter table notices drop constraint notices_content_hash_fkey;
alter table notices drop constraint notices_content_hash_key;

alter table notices_raw drop constraint notices_raw_pkey;
alter table notices_raw add constraint notices_raw_pkey primary key (source_id, content_hash);

alter table notices add constraint notices_source_content_hash_key unique (source_id, content_hash);
alter table notices
    add constraint notices_content_hash_fkey
    foreign key (source_id, content_hash) references notices_raw (source_id, content_hash);
