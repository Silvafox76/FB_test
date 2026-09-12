-- `fx_rates` is keyed on (currency, rate_date) and it should be keyed on
-- (source, currency, rate_date).
--
-- 012 gave the table a `source` column, and both queries that read it filter on
-- that column - `latest` asks for the newest rate_date FROM a named publisher, and
-- reads that day's rows FROM the same one. But the primary key left the publisher
-- out, so the column was documentation rather than identity, and the
-- `on conflict (currency, rate_date) do nothing` in the insert meant a second
-- publisher's USD row for a day already held would be silently DISCARDED rather
-- than stored alongside. The read would then return the first publisher's number
-- under the second publisher's name.
--
-- Rule 1 says there is one rate publisher and that has not changed: nothing here
-- invites a second one, and the conversion still reads exactly one. What this fixes
-- is that the table could not REPRESENT what its own queries asked of it. Two
-- things make that concrete rather than theoretical: a test that stores rates under
-- its own publisher id, so it cannot collide with a real `monitor fx` run; and the
-- day the publisher is ever changed by a decision, when the old rows have to stay
-- attributable to the publisher that issued them rather than being overwritten by
-- whoever comes next.

alter table fx_rates drop constraint fx_rates_pkey;

alter table fx_rates add primary key (source, currency, rate_date);
