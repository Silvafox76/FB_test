"""Write docs/export_spec.md from config/record_defaults.yaml.

    uv run python scripts/generate_export_spec.py

Run it after any change to the `columns` block and commit the result with that change.
`tests/review/test_export.py` renders the same document and compares it with the file, so
forgetting is a failing test rather than a document that quietly says 73 when the config
says 74.

The rendering itself is `review.export.spec_markdown`, not here: the export writes that
column list as the CSV header row, so the module that owns the contract is the module that
describes it, and there is one implementation for the test to hold the file against.
"""

from __future__ import annotations

from review.export import SPEC_PATH, columns, spec_markdown


def main() -> int:
    SPEC_PATH.write_text(spec_markdown(), encoding="utf-8")
    print(f"wrote {SPEC_PATH}: {len(columns())} columns, appendix E's order")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
