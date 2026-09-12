# Export column specification

**Generated from `config/record_defaults.yaml`. Do not edit by hand.**
`review.export.spec_markdown` renders it, `scripts/generate_export_spec.py` writes it, and
`tests/review/test_export.py` fails when this file and the config disagree. That is the only reason
this document is generated: a column added to the config and not to this page would otherwise be a
column BD reads about nowhere, and the header row is the contract.

Regenerate with:

    uv run python scripts/generate_export_spec.py

## What this specifies

Architecture v0.4 appendix E. `monitor/stage/record.py` builds every approved record with exactly
these keys and `review/export.py` writes them as the CSV header row in exactly this order, because
Zoho's import mapper matches on headers: the order and the spelling are the contract, and nothing
else invents a field.

- One directory per batch under the export directory: `B0001/B0001.csv` beside
  `B0001/B0001.manifest.json`, published by a single rename so the pair appears together or not at
  all. The manifest carries the row count, the approval range, the reviewers who approved the
  records and the sha256 of the CSV as written.
- UTF-8 **with a byte order mark**. Without it Excel and Zoho's import mapper read the file as the
  local codepage and an accented buyer name — "Ministère de l'Économie" — arrives mangled.
- RFC 4180 as those two read it: quoted where needed, CRLF rows, one header row.
- Every column is present in every row. A record missing one is refused at export rather than
  exported with a blank, because a blank column is indistinguishable from a field nobody filled in.
- The export is one way and it is a file (D31, rules 15 to 18). Nothing here talks to a CRM, nothing
  is sent anywhere, there is no import path back into this database, and a named person imports the
  file by hand. Account resolution happens at import, using Zoho's own matching; the exporter
  proposes the buyer name as text and nothing more.

## Categories

| Category | Meaning | Columns |
| --- | --- | --- |
| D | Derived from the notice or candidate, written with confidence | 17 |
| S | A heuristic suggestion; the reviewer sees it in the record preview and edits it before approving | 17 |
| B | BD judgement only: the exporter writes the placeholder the CRM already shows for an unfilled field, and never guesses | 39 |

73 columns in total.

## Columns

| # | Column | Category |
| --- | --- | --- |
| 1 | Opportunity Name | D |
| 2 | Opportunity Owner | B |
| 3 | Stage | S |
| 4 | Closing Date | B |
| 5 | Finance Project ID | D |
| 6 | Account Name | S |
| 7 | Is this a Partner or Reseller-led opportunity? | S |
| 8 | Level of Government | D |
| 9 | Sales Forecasting | B |
| 10 | Proposal Type | S |
| 11 | Original Closing Date | B |
| 12 | Lead Source | S |
| 13 | Partners Involved | D |
| 14 | Pipeline | S |
| 15 | Industry | D |
| 16 | Shipping Country | D |
| 17 | Funding Source | D |
| 18 | Currency | S |
| 19 | Deal Tier | S |
| 20 | Delivery Model | S |
| 21 | Expected Close Year | B |
| 22 | Customer Type | S |
| 23 | Eligibility Requirements Met? | S |
| 24 | Eligibility Requirements Comments | D |
| 25 | Partner Required? | S |
| 26 | Partner Required Comments | D |
| 27 | Legal Support Required? | B |
| 28 | Legal Support Comments | B |
| 29 | Expected Release of RFP/EOI? | S |
| 30 | Proposal Due or Submitted | D |
| 31 | Proposal Documents Received | B |
| 32 | Pre-Bid Meeting | B |
| 33 | Deadline for Questions | B |
| 34 | Delivery Logistics and Visa Requirements | B |
| 35 | Do we need to register our interest? | S |
| 36 | Number of Copies? | B |
| 37 | Is Bid Bond Required | B |
| 38 | Format of Bid Bond | B |
| 39 | Amount of Bid Bond (if known) | B |
| 40 | Estimated Duration of Contract in Months | B |
| 41 | Licenses | B |
| 42 | Implementation Services | B |
| 43 | Software Maintenance | B |
| 44 | Academy-Training | B |
| 45 | Sustainability-Help Desk | B |
| 46 | Third Party Licenses | B |
| 47 | FreeBalance Amount | B |
| 48 | Total Opportunity Amount | S |
| 49 | Probability (%) | S |
| 50 | Tax Amount | B |
| 51 | Pricing Notes | D |
| 52 | Hardware/Hosting | B |
| 53 | Standard or Custom Product Required? | S |
| 54 | FreeBalance Products Required | D |
| 55 | Product Gaps | D |
| 56 | Are gaps critical | B |
| 57 | Mandatory Requirements | B |
| 58 | Evaluation Weighting | B |
| 59 | Do we pass the 80/20 rule? | B |
| 60 | Third Party Software | B |
| 61 | Hardware and Systems Software | B |
| 62 | Level of Effort | B |
| 63 | Implementation Period | B |
| 64 | Number of Users | B |
| 65 | System Go Live | B |
| 66 | Available Budget | B |
| 67 | Warranty Period | B |
| 68 | Post-Warranty Period | B |
| 69 | Budget Notes | B |
| 70 | Created By | D |
| 71 | Next Steps | D |
| 72 | monitor_candidate_id | D |
| 73 | monitor_export_batch | D |
