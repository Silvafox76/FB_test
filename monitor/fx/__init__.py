"""Exchange rates: acquire them, store them, convert with them.

Four modules, one job each (rule 5). `config.py` reads and validates
`config/fx.yaml`. `nbu.py` fetches the publisher's day and parses it. `store.py`
puts rates in `fx_rates` and reads the newest set back. `convert.py` is pure
arithmetic over a rate table and holds no I/O.

Nothing here decides policy. Every number the conversion depends on - the
endpoint, the base currency, the statutory CFA pegs, how stale a rate may be,
the rounding mode - is in `config/fx.yaml` and version-hashed with the rest of
the configuration (rule 6).
"""
