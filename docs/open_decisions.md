# Open decisions

Questions raised by a step that the step itself did not settle, each with the step that has to.
Not the decision register in Architecture v0.4 section 10, which holds the architectural decisions
D1 to D33. This file holds the small ones that came out of building, so they are not rediscovered.

Closed items stay, struck through in the status column, with what was decided.

| # | Raised at | Question | Decides at | Status |
| --- | --- | --- | --- | --- |
| 1 | Step 3 | **Which config is version-hashed in the database.** Rule 6 says sources, lexicons, thresholds, geography weights, the function map, the product mapping and the record defaults are "version-hashed in the database". Only the lexicons are: `lexicon_versions` is the one table `001_schema.sql` defines for it, and BUILD_ORDER step 3 asks only for lexicon hashes. Nothing yet depends on the others being traceable, but from step 5 the thresholds drive what is dropped and from step 10 the record defaults drive what is exported, and at that point a scoring run cannot be traced back to the numbers that produced it. Three options: a `config_versions` table covering every file under `config/`; a hash column on the existing tables; or accept the lexicons as the only versioned config and say so in rule 6. | Step 5, before thresholds first change what is dropped | Open |
| 2 | Step 3 | **The Zoho picklist values for Industry outside West Africa.** `config/record_defaults.yaml` carries `North & West Africa` from Architecture v0.4. The Europe and Balkans values are CRM picklist strings that appear in no document and in no reference record, so they are `TBD` rather than a guess: a wrong picklist value is a rejected column at import, a placeholder is one mapping decision the operator makes once. | Step 16, at the dry-run import with the named import operator | Open |
| 3 | Step 3 | **Whether the model call accepts the generated tool schema as it stands.** `Score.model_json_schema()` emits `$defs` and a `$ref` for the nested `matched_functions` object. That should be a valid `input_schema`, but it has not been put to a real call. If it is rejected, the fix is to inline the definition, not to loosen the model. | Step 6, on the first real scoring call | Open |
| 4 | Step 3 | **BUILD_ORDER's wording on `Score`.** Step 3 says the models match the step 2 schema "field for field" and also that `Score` is the model-call tool schema. Those only conflict if the whole `scores` table is read as the tool schema; Architecture v0.4 appendix C, which BUILD_ORDER cites, settles it as the ten output fields. The code follows appendix C. The document could say so. | Whoever next edits BUILD_ORDER | Open |
