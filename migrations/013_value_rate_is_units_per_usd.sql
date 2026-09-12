-- `candidates.value_rate` is stored in the direction that can reproduce its own
-- arithmetic, and 012 chose the other one.
--
-- 012 defined value_rate as "USD per one unit of value_currency" on a
-- numeric(18, 6) column. That direction cannot hold a low-value currency: USD per
-- UAH is 0.0224476..., which rounds to 0.022448 at six decimal places, and
-- 4,428,444 UAH at the stored rate is USD 99,410 while the true figure is USD
-- 99,408. XOF is worse: USD per XOF is 0.00176714..., so a 100,000,000 XOF
-- contract lands 14 dollars away from itself.
--
-- The point of storing the rate at all is that a reviewer can multiply it out and
-- get the number on the record back. A rate that does not reproduce its own
-- converted figure is decoration. Stored the other way up - units of
-- value_currency per one USD - every currency in play is at least 0.74 (GBP) and
-- most are well above 1 (UAH 44.55, XOF 565.89), so six decimal places is ample
-- and `estimated_value / value_rate` returns exactly what is in
-- estimated_value_usd.
--
-- No data moves: this is a semantics and comment change on a column that is null
-- on every one of the 148 existing candidates, because nothing has been staged
-- since 012 added it. The type is already right for the new direction.

comment on column candidates.value_rate is
    'Units of value_currency per ONE USD, as used for this record. '
    'estimated_value / value_rate reproduces estimated_value_usd exactly, which is '
    'the whole reason it is stored. Quoted this way up rather than as USD-per-unit '
    'because numeric(18, 6) cannot hold USD-per-unit for UAH or XOF without losing '
    'the last digits of the answer.';

-- Said in the database as well as in the comment: a rate is a positive number, and
-- a zero would make the division that uses it undefined rather than merely wrong.
alter table candidates
    add constraint candidates_value_rate_positive
    check (value_rate is null or value_rate > 0);
