-- The published amount is the record; USD is derived and stamped.
--
-- WHY THIS EXISTS. `estimated_value_usd` was a field the MODEL filled in from the
-- notice text, and it was wrong every time it fired. All eight values the scorer
-- ever produced were checked on 2026-09-12 against the full notice text the model
-- was given (title + body, and the scorer's body budget is 12,000 characters
-- against a longest notice of 2,292, so nothing was truncated): NONE of the eight
-- appears anywhere in that text, in any format. They were not misread, they were
-- invented. On the four TED notices where the payload carries a structured figure
-- the model never saw, three of the four inventions sit at 1.09 to 1.19 times the
-- true EUR amount - the band a plausible EUR/USD rate occupies - and the fourth
-- (payload 920,000 EUR, model 250,000) is not a conversion at any rate. The
-- scoring prompt already told the model "Do not infer a system name, a value or a
-- funding source that is not written down".
--
-- Meanwhile the real figure was in the payload and nothing read it: 1,157 of the
-- 2,487 stored notices carry a structured amount WITH its currency (prozorro
-- tender value, ted estimated-value-proc, liberia OCDS tender.value), in eleven
-- currencies, and eleven of them survived because Liberia alone publishes in USD.
--
-- So the model stops being asked for a value, the normalisers read the structured
-- field, and the original amount and its currency are stored as published (rule 9:
-- the original is the record). USD becomes a DERIVED column carrying the rate and
-- the rate's date, the same way a translation carries its model and prompt version.

-- notices: the amount exactly as the publisher stated it, and in which currency.
-- numeric(18,2) rather than bigint because a minor unit is real money in every
-- currency here and UAH figures run to nine digits before the decimal point.
alter table notices
    add column estimated_value numeric(18, 2),
    add column value_currency  text;

comment on column notices.estimated_value is
    'The amount as published. Never a converted figure. Null when the notice states no value.';
comment on column notices.value_currency is
    'ISO 4217 alpha-3 as the source published it. Null exactly when estimated_value is null.';

-- Either both are set or neither is. A bare amount with no currency is not a
-- price, and a currency with no amount is noise (rule 4).
alter table notices
    add constraint notices_value_needs_currency
    check ((estimated_value is null) = (value_currency is null));

-- The eleven surviving rows are Liberia's, and Liberia publishes natively in USD
-- (monitor/normalise/liberia.py, decision 7), so this is a rename rather than a
-- conversion and nothing is assumed.
update notices
   set estimated_value = estimated_value_usd,
       value_currency  = 'USD'
 where estimated_value_usd is not null;

-- Gone rather than left permanently null: a notice now carries what was published,
-- and a USD column on the notices table would invite exactly the silent conversion
-- this migration exists to remove.
alter table notices drop column estimated_value_usd;

-- scores: dropped outright. Unlike the notices column there is nothing here worth
-- backfilling - the eight values are the fabrications described above - and
-- scores.raw_json still holds the model's full tool input verbatim, so what the
-- model said on those eight notices is not lost, only removed from the column a
-- later stage would read as fact.
alter table scores drop column estimated_value_usd;

-- candidates: the original travels with the candidate, and the USD figure beside
-- it is derived, with the rate that produced it and that rate's date.
alter table candidates
    add column estimated_value numeric(18, 2),
    add column value_currency  text,
    add column value_rate      numeric(18, 6),
    add column value_rate_date date;

comment on column candidates.estimated_value is
    'The amount as published, carried from the primary notice.';
comment on column candidates.value_currency is
    'ISO 4217 alpha-3 as published.';
comment on column candidates.estimated_value_usd is
    'Derived at staging from estimated_value at value_rate, dated value_rate_date. '
    'Null when no rate covers value_currency, which is a documented absence, not an error.';
comment on column candidates.value_rate is
    'USD per one unit of value_currency, as used. Stored so a reviewer can check the arithmetic.';

alter table candidates
    add constraint candidates_value_needs_currency
    check ((estimated_value is null) = (value_currency is null));

-- The six candidates carrying a model-invented figure are cleared rather than
-- converted: there is nothing to convert from, the number was never in the notice.
-- They are re-derived the next time they are staged.
update candidates set estimated_value_usd = null where estimated_value_usd is not null;

-- A derived USD figure without the rate that produced it is the unattributed number
-- this whole migration is about.
alter table candidates
    add constraint candidates_usd_needs_rate
    check (estimated_value_usd is null
           or (value_rate is not null and value_rate_date is not null));

-- The rate table. One row per currency per day, from one publisher.
create table fx_rates (
    currency     text not null,
    rate_date    date not null,
    -- UAH per ONE unit of `currency`, exactly as the publisher quotes it. The base
    -- currency itself is deliberately absent: the publisher quotes everything
    -- against UAH, so there is no UAH row and the conversion treats it as the base.
    uah_per_unit numeric(18, 6) not null check (uah_per_unit > 0),
    source       text not null,
    fetched_at   timestamptz not null default now(),
    primary key (currency, rate_date),
    constraint fx_rates_currency_format check (currency ~ '^[A-Z]{3}$')
);

comment on table fx_rates is
    'Daily reference rates from one publisher. Append only in practice: a rate for a '
    'date that has already been fetched is not re-fetched, so a stamped conversion '
    'stays reproducible.';

-- 002_roles.sql grants nothing to a table a later migration creates, deliberately,
-- so this says it. The pipeline fetches and reads rates; nobody else writes them.
grant select, insert on fx_rates to monitor_pipeline;
grant select on fx_rates to monitor_review, monitor_readonly;
