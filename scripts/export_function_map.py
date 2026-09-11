"""Generate config/function_map.yaml from the component map workbook.

Run once, by hand, and commit the result. Hand edits to the generated file are
expected afterwards; this script does not try to preserve them, so re-running it
overwrites and the diff is the review.

    uv run --with openpyxl python scripts/export_function_map.py

Two sheets, both in `docs/reference/PFM_Component_Map_4.2.xlsx`:

  "PFM Component Map"   header on row 2. Columns A Pillar, B Function, C Component,
                        D Type code, E Type label. 8 pillars, 33 functions,
                        559 components.
  "New Marketecture"    header on row 7. Columns B New Marketecture (the product),
                        E Function, F Component, H Status. 578 rows carry a
                        function; 563 of those carry a product. The 15 that do not
                        are pillar 8, which has no product mapping in 4.2.

`type_weight` is a flat lookup on the Type label, extended to all eleven labels the
workbook actually uses. Architecture v0.4 and BUILD_ORDER name weights for four of
them; the other seven are set here, in TYPE_WEIGHTS, where they can be argued about
in one place. A function's weight is the weight of its most heavily weighted
component type, not an average across its components: averaging thirty components
pulls every function back toward 1.0 and erases the distinction the weight exists
to make.
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKBOOK = REPO / "docs" / "reference" / "PFM_Component_Map_4.2.xlsx"
OUTPUT = REPO / "config" / "function_map.yaml"

COMPONENT_SHEET = "PFM Component Map"
COMPONENT_HEADER_ROW = 2
MARKETECTURE_SHEET = "New Marketecture"
MARKETECTURE_HEADER_ROW = 7

# The eleven Type labels the workbook uses, each with its weight. The first four
# are from Architecture v0.4 section 8 and BUILD_ORDER; the remaining seven are set
# here because the workbook has labels the weight table never named. Government
# Controls and Process Execution are what a software procurement looks like;
# Planning, Reform and Fiscal Transparency describe advisory work that rarely buys
# a system.
TYPE_WEIGHTS = {
    "Government Controls": 1.3,
    "Process Execution": 1.2,
    "Approvals": 1.2,
    "Payments": 1.1,
    "E-Commerce": 1.1,
    "Performance Management": 1.0,
    "Monitoring & Evaluation": 0.9,
    "Oversight & Audit": 0.9,
    "Planning & Scenarios": 0.8,
    "Reform & Modernization": 0.8,
    "Fiscal Transparency": 0.6,
}
DEFAULT_TYPE_WEIGHT = 1.0

# Two different reasons a function ends up with no product, said out loud in the
# generated file rather than left as an empty field a reader has to interpret.
# Pillar 8, Government Service Delivery, has no rows on the New Marketecture sheet
# at all. Function 3.4 Progressive Activation has rows, but every one of them names
# the product "TBD", which is the workbook saying it has not been placed yet.
NO_ROWS_NOTE = "no product mapping in component map 4.2"
UNPLACED_NOTE = "component map 4.2 maps this function to TBD, not to a named product"

# The workbook uses this literally as a product name for components not yet placed.
UNPLACED_PRODUCT = "TBD"

# A function has many components and each carries its own status for the same
# product, so one status has to stand for the pair. The best one does: if any
# component of the function is Available in that product, the product serves the
# function and is not a gap. This reproduces Architecture v0.4 appendix E's own
# summary table, where 4.2 Debt and Investment reads "Available; not Available"
# across its two products even though its first product has four Available
# components out of thirty.
STATUS_RANK = {"Available": 0, "High Priority": 1, "Medium Priority": 2, "Low Priority": 3}
UNRANKED_STATUS = 4


def slug(function_name: str) -> str:
    """'4.2 Debt & Investment Management' -> 'debt_investment_management'."""
    without_number = re.sub(r"^[0-9]+(\.[0-9]+)*\.?\s*", "", function_name.strip())
    cleaned = re.sub(r"[^a-z0-9]+", "_", without_number.lower())
    return cleaned.strip("_")


def cell(sheet, row: int, column: int) -> str:
    value = sheet.cell(row, column).value
    return "" if value is None else str(value).strip()


def read_components(workbook) -> dict[str, dict]:
    """Function name -> pillar, ordered component names, and the type labels seen."""
    sheet = workbook[COMPONENT_SHEET]
    functions: dict[str, dict] = {}
    for row in range(COMPONENT_HEADER_ROW + 1, sheet.max_row + 1):
        pillar, function, component = (cell(sheet, row, i) for i in (1, 2, 3))
        type_label = cell(sheet, row, 5)
        if not (pillar and function and component):
            continue
        entry = functions.setdefault(function, {"pillar": pillar, "components": [], "types": []})
        entry["components"].append(component)
        if type_label:
            entry["types"].append(type_label)
    return functions


def read_products(workbook) -> dict[str, dict[str, str]]:
    """Function name -> {product: status}. A function can map to several products.

    The status kept for a function and product is the best one across that
    function's components, per STATUS_RANK above.

    A function with rows but no named product gets an empty dict, which is what
    distinguishes it from one absent from the sheet entirely.
    """
    sheet = workbook[MARKETECTURE_SHEET]
    products: dict[str, dict[str, str]] = {}
    for row in range(MARKETECTURE_HEADER_ROW + 1, sheet.max_row + 1):
        product, function, status = (cell(sheet, row, i) for i in (2, 5, 8))
        if not function:
            continue
        entry = products.setdefault(function, {})
        if not product or product == UNPLACED_PRODUCT:
            continue
        status = status or "Unknown"
        best = entry.get(product)
        if best is None or STATUS_RANK.get(status, UNRANKED_STATUS) < STATUS_RANK.get(best, UNRANKED_STATUS):
            entry[product] = status
    return products


def weight_for(type_labels: list[str]) -> float:
    """The most heavily weighted component type in the function, not the average."""
    if not type_labels:
        return DEFAULT_TYPE_WEIGHT
    return max(TYPE_WEIGHTS.get(label, DEFAULT_TYPE_WEIGHT) for label in type_labels)


def keywords_from(function_name: str, components: list[str]) -> list[str]:
    """A starting point: the function name and its component names, numbers stripped.

    The real phrasing is hand-written into config/lexicon_en.yaml and lexicon_fr.yaml.
    """
    names = [function_name, *components]
    seen, keywords = set(), []
    for name in names:
        phrase = re.sub(r"^[0-9]+(\.[0-9]+)*\.?\s*", "", name.strip()).lower()
        if phrase and phrase not in seen:
            seen.add(phrase)
            keywords.append(phrase)
    return keywords


def build() -> dict:
    workbook = openpyxl.load_workbook(WORKBOOK, data_only=True)
    components = read_components(workbook)
    products = read_products(workbook)

    functions = []
    for function_name, entry in components.items():
        mapped = products.get(function_name, {})
        on_marketecture_sheet = function_name in products
        function = {
            "function_id": slug(function_name),
            "name": function_name,
            "pillar": entry["pillar"],
            "type_weight": weight_for(entry["types"]),
            "product": sorted(mapped),
            "product_status": {name: mapped[name] for name in sorted(mapped)},
            "keywords_en": keywords_from(function_name, entry["components"]),
            "keywords_fr": [],
        }
        if not mapped:
            function["product_note"] = UNPLACED_NOTE if on_marketecture_sheet else NO_ROWS_NOTE
        functions.append(function)

    return {
        "source": "docs/reference/PFM_Component_Map_4.2.xlsx",
        "generated_by": "scripts/export_function_map.py",
        "type_weights": TYPE_WEIGHTS,
        "functions": functions,
    }


def main() -> int:
    document = build()
    OUTPUT.write_text(
        "# Generated by scripts/export_function_map.py from the component map workbook.\n"
        "# Hand edits are expected; re-running the script overwrites them and the diff is the review.\n"
        "# keywords_fr is filled in by hand: the workbook is English only.\n"
        + yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    functions = document["functions"]
    without_product = [f["function_id"] for f in functions if not f["product"]]
    print(f"wrote {OUTPUT.relative_to(REPO)}: {len(functions)} functions")
    print(f"  pillars: {len({f['pillar'] for f in functions})}")
    print(f"  products: {len({p for f in functions for p in f['product']})}")
    print(f"  no product mapping: {len(without_product)} ({', '.join(without_product)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
