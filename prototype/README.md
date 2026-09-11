# Prototype

Two revisions of a React prototype of the whole system, kept as a design input and as the source of
the seeded function and lexicon data. `pfm_opportunity_monitor_final.jsx` is the later and more
useful of the two.

Neither is the review app. Step 10 builds a server-rendered FastAPI and Jinja interface with one CSS
file and no JavaScript beyond a confirm dialog on Reject. What carries forward from these files, what
is a starting point to be checked against the component map workbook, and what is superseded is set
out in `docs/design_inputs.md`. Read that before lifting a constant out of either file.

They are not built, linted or tested. `ruff` does not see them and no `make` target touches them.
