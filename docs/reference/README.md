# Reference documents

The `.docx` files are the authoritative versions as circulated. The `.txt` beside each is a plain
text extraction, committed so the steps can grep for a section or an appendix without opening Word.
If the two disagree, the `.docx` is right and the extraction is stale: regenerate it, do not edit it.

| File | Status |
| --- | --- |
| `PFM_Opportunity_Monitor_Technology_and_Architecture_v0_4` | Current. Section 10 is the decision register; appendix D is the IAM spec; appendix E is the export column spec. |
| `PFM_Opportunity_Monitor_Weekend_Build_Plan_v1_3` | Current. Targets for steps 1 to 11. |

Anything dated before 11 September 2026 (Pilot Plan v0.2, Architecture v0.2 and v0.3) is superseded
by D31 and is not in this directory.

`PFM_Component_Map_4.2.xlsx` is the component map, authoritative for the 33 functions, the 559
components and the 20-product New Marketecture mapping. `scripts/export_function_map.py` reads it at
step 3. What it decides and what it does not is in `docs/design_inputs.md`.
