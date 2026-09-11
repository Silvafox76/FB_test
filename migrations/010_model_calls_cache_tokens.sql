-- 010_model_calls_cache_tokens.sql
--
-- The cost line was not reconstructible. model_calls recorded tokens in, tokens
-- out and a cost, but the scorer caches a ~7,500-token system block and the two
-- cache counters the API reports were written nowhere. So a cost_usd value could
-- not be checked against the counts beside it, which is how a sign error in
-- monitor/caps.py survived until the first real scoring run: 28 of the first 29
-- calls recorded a NEGATIVE cost, and because spend_today sums this column to
-- enforce rule 22's USD cap, the day's spend read lower than it was.
--
-- Rule 22 asks for tokens in and out, cost and latency per call. It gets them. But
-- "cost recorded per call" is only auditable if the inputs to the cost are there
-- too, and on a cached prompt those two counters are most of the bill.
--
-- The 29 rows already written keep their token counts and get a recomputed cost
-- from the components that survive (fresh input and output). Their cache
-- components are genuinely unrecoverable, because the columns did not exist when
-- they were written, so the corrected figure is a floor rather than the true cost
-- and is marked as such: prompt_version gets a -uncached suffix so no report ever
-- averages them in with rows whose cost is complete. They are not deleted; no role
-- holds delete on this table and an audit row that turned out to be wrong is part
-- of the record.

alter table model_calls add column cache_read_tokens  int not null default 0;
alter table model_calls add column cache_write_tokens int not null default 0;

comment on column model_calls.cache_read_tokens is
    'usage.cache_read_input_tokens. Disjoint from tokens_in, which is fresh input only.';
comment on column model_calls.cache_write_tokens is
    'usage.cache_creation_input_tokens. Billed at 1.25x the input rate.';

-- Recompute the negative rows to the floor described above. The Haiku input rate is
-- 1.00 and the output rate 5.00 per million (config/thresholds.yaml rate_card); it
-- is written literally here because a migration records what was done on the day it
-- ran, and must not silently change meaning when the rate card is next edited.
update model_calls
set cost_usd = round((tokens_in * 1.00 + tokens_out * 5.00) / 1000000.0, 6),
    prompt_version = prompt_version || '-uncached'
where cost_usd < 0;
