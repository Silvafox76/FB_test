# Design inputs, and what each one is allowed to decide

Three artifacts arrived after the repository was created. None of them is a specification; each
answers a different question and two of them are partly superseded. This file says which is which so
a later step does not treat a prototype constant as a decided fact.

## 1. `docs/reference/PFM_Component_Map_4.2.xlsx`

Authoritative for the function map and the product mapping. Step 3 generates
`config/function_map.yaml` from it with `scripts/export_function_map.py`; step 10 reads the product
columns for the export's FreeBalance Products Required and Product Gaps fields.

Verified against the counts BUILD_ORDER cites, sheet by sheet:

| Claim in BUILD_ORDER | Sheet | Found |
| --- | --- | --- |
| 8 pillars, 33 functions | `PFM Component Map` (header row 2) | 8, 33 |
| 559 components | `PFM Component Map` | 559 |
| 578 mapping rows | `New Marketecture` (header row 7) | 578 rows carry a Function |
| 20 products | `New Marketecture`, column `New Marketecture` | 20, one of which is literally `TBD` |
| Pillar 8 has no product mapping | `New Marketecture` | Confirmed: 8.1 to 8.5 are the 15 rows with a function and no product, which is why 578 rows yield 563 product mappings |

Two things the step had to decide rather than look up. **Both were settled at step 3** and the
decision lives in `TYPE_WEIGHTS` in `scripts/export_function_map.py`, which writes it into
`config/function_map.yaml` so it can be argued about in one place:

- **The Type column is per component, the weight is per function.** Column D holds a code (`E-PE`,
  `C- GC`, `D-RM`) and column E its label. There are eleven labels, not the four BUILD_ORDER's weight
  table names: Government Controls, Process Execution, Fiscal Transparency, Planning & Scenarios,
  Reform & Modernization, Approvals, Oversight & Audit, Monitoring & Evaluation, Performance
  Management, Payments, E-Commerce. A function has many components with different types, so
  `type_weight` needs an aggregation rule. Note `C- GC` carries a stray space inside the code.
  **Settled: the flat lookup, extended to all eleven labels, and a function takes the weight of its
  most heavily weighted component type rather than an average.** Averaging thirty components pulls
  every function back toward 1.0, which is what the prototype's 0.82 to 1.13 band shows and what the
  weight exists to avoid.
- **There is no label called "Policy".** BUILD_ORDER's 0.8 weight for Policy has to map onto Reform &
  Modernization or Planning & Scenarios, or the weight table needs a row per real label. **Settled:
  a row per real label.** Reform & Modernization and Planning & Scenarios both take the 0.8 that
  BUILD_ORDER gave Policy, since both describe advisory work that rarely buys a system; the seven
  labels the weight table never named are set in `TYPE_WEIGHTS` rather than falling to the default.

## 2. `prototype/pfm_opportunity_monitor_final.jsx` and `prototype/pfm_opportunity_monitor_1.jsx`

A React prototype of the whole system in two revisions. `_1` is the earlier one: twelve invented
functions with round weights. `_final` is the later one and is the useful one: the real 33 functions
with their workbook pillar names, type labels, computed weights to two decimals, and a seeded English
and French keyword list per function.

**Carries forward as a starting point, to be checked against the workbook, not trusted over it:**

- The 33 function ids, names and pillars, and the per-function `type_weight` (step 3,
  `config/function_map.yaml`). The prototype's weights cluster between 0.82 and 1.13, which is a
  weighted average across each function's components rather than BUILD_ORDER's flat 1.3 / 1.2 / 0.8 /
  0.6 lookup. Those two are different schemes and step 3 has to pick one and say why.
- The `en` and `fr` keyword strings per function (step 3, `config/lexicon_en.yaml` and
  `lexicon_fr.yaml`). This is the largest single saving the prototype offers: 33 functions of seeded
  French phrasing that would otherwise be written from scratch.
- The system-name list. `_final` adds IFMAS, GFMAS, HRMS, ITMIS and IRMIS to the fourteen in
  `CLAUDE.md`. Treat as a proposed addition; `CLAUDE.md` is the list of record until it is amended.

**Superseded, do not carry forward:**

- `PRODUCT_MAP` in both files uses the *current* marketecture names (Core Accountability, Treasury
  Management, Public Expenditures, Revenue and Taxation). Step 3 and step 10 use the **New
  Marketecture** sheet's 20 products. The workbook wins; the prototype's map is stale.
- The React UI is not the review app. Step 10 is server-rendered Jinja, one CSS file, no JavaScript
  beyond a confirm dialog on Reject (`CLAUDE.md`, BUILD_ORDER step 10). What the prototype
  contributes to step 10 is its information architecture and one rule already in BUILD_ORDER: orange
  is the decision panel and nothing else. Its dark palette, its icon set and its component structure
  are not requirements.
- Anything in the prototype implying a notification, a CRM lookup or a second data path is superseded
  by D31 and rules 15 to 18.

## 3. The reference documents

`docs/reference/` holds Architecture v0.4 and Weekend Build Plan v1.3 as circulated, each with a
plain text extraction beside it for grepping. Appendix E of the architecture is the export column
spec and is authoritative for step 10; nothing in a prototype overrides it.
