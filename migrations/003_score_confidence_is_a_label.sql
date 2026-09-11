-- 003_score_confidence_is_a_label.sql
--
-- scores.confidence was created as `real` in 001. Architecture v0.4 appendix C,
-- which is the model's own tool schema, defines it as one of 'high', 'medium' or
-- 'low', and step 20's rescore path compares two calls by that label. A numeric
-- column would force the scorer to invent a number the model never produced.
--
-- Done as a new migration rather than an edit to 001 because 001 has been applied
-- and recorded; an applied migration is history.
alter table scores
    alter column confidence type text using confidence::text;

alter table scores
    add constraint scores_confidence_label check (confidence in ('high', 'medium', 'low'));
