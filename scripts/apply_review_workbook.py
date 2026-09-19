"""Apply a reviewer's decisions from the review workbook they filled in.

The reviewers' workbook (`make review-workbook`, 2026-09-18) carries one row per
pending candidate and three columns for the reviewer: YOUR DECISION, YOUR REASON and
Zoho checked. When it comes back, this script reads those columns and puts each
decision through the single write path, `review/decisions.py`, as the named reviewer,
connecting as `monitor_review` (rules 11 to 13). It is the same call the form makes;
the workbook is the reviewer's instrument, not a second code path.

What it does with each decision:
  approve  -> decisions.approve(candidate, reviewer)
  reject   -> decisions.reject(candidate, reviewer, "<reason>: <why>") where <reason> is
              one of config/review.yaml's rejection_reasons and <why> is the workbook's
              own reasoning for that row, so the week 14 gate reads a stable vocabulary
              plus the evidence
  monitor  -> nothing. There is no such status (decision 57's third label is still an
              open question), so the row stays pending_review and is listed at the end
  blank    -> nothing, listed at the end

A row whose "why" column is empty and whose YOUR REASON says only "see why column" is
refused, not guessed: a rejection with no reason is what migration 008 exists to stop.

The reviewer name comes from the command line, not from the workbook, because the
workbook's reason column names the person in prose ("from Matthew") and a name parsed
out of prose is a guess. `--dry-run` prints what would happen and writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from monitor import db
from review.decisions import DecisionRefused, approve, reject, rejection_reasons

SHEET = "Queue"
DECISION_COLUMN = "YOUR DECISION"
REASON_COLUMN = "YOUR REASON"
WHY_COLUMN = "why"
CANDIDATE_COLUMN = "candidate"
ZOHO_COLUMN = "Zoho checked (Y/N)"

# The workbook offers three words; the system has two statuses. "monitor" is recorded
# here as a skip so the reviewer sees the row was read and deliberately left pending.
ACTIONS = {"approve", "reject", "monitor"}


@dataclass(frozen=True)
class Row:
    candidate: str
    decision: str  # lower-cased, stripped; "" when blank
    reason: str  # YOUR REASON as typed
    why: str  # the Monitor's own reasoning, endorsed when the reviewer points at it
    zoho_checked: str


def read_rows(path: Path) -> list[Row]:
    sheet = openpyxl.load_workbook(path, data_only=True)[SHEET]
    header = [cell.value for cell in sheet[1]]
    index = {name: i for i, name in enumerate(header)}
    for name in (CANDIDATE_COLUMN, DECISION_COLUMN, REASON_COLUMN, WHY_COLUMN, ZOHO_COLUMN):
        if name not in index:
            raise ValueError(f"{path.name}: sheet {SHEET!r} has no column {name!r}")
    rows = []
    for values in sheet.iter_rows(min_row=2, values_only=True):
        candidate = values[index[CANDIDATE_COLUMN]]
        if not candidate:
            continue
        rows.append(
            Row(
                candidate=str(candidate).strip(),
                decision=str(values[index[DECISION_COLUMN]] or "").strip().lower(),
                reason=str(values[index[REASON_COLUMN]] or "").strip(),
                why=str(values[index[WHY_COLUMN]] or "").strip(),
                zoho_checked=str(values[index[ZOHO_COLUMN]] or "").strip(),
            )
        )
    return rows


def rejection_text(row: Row, category: str) -> str:
    """`<category>: <evidence>`, the shape review/app.py stores from the form.

    The evidence is the reviewer's own words when they wrote any beyond pointing at the
    why column, and the why column otherwise. Both may be present.
    """
    if category not in rejection_reasons():
        raise ValueError(f"{category!r} is not in config/review.yaml rejection_reasons")
    own = row.reason
    points_at_why = "why column" in own.lower()
    parts = [] if points_at_why or not own else [own]
    if row.why:
        parts.append(row.why)
    if not parts:
        raise DecisionRefused(f"{row.candidate}: reject with no reason in either column")
    return f"{category}: {' '.join(parts)}"


def apply(rows: list[Row], reviewer: str, category: str, *, dry_run: bool) -> dict[str, list[str]]:
    outcome: dict[str, list[str]] = {"approved": [], "rejected": [], "monitor": [], "blank": [], "refused": []}
    unknown = [r for r in rows if r.decision and r.decision not in ACTIONS]
    if unknown:
        raise ValueError("unknown decisions: " + ", ".join(f"{r.candidate}={r.decision!r}" for r in unknown))

    conn = None if dry_run else db.connect("review")
    try:
        for row in rows:
            if not row.decision:
                outcome["blank"].append(row.candidate)
                continue
            if row.decision == "monitor":
                outcome["monitor"].append(row.candidate)
                continue
            try:
                if row.decision == "reject":
                    text = rejection_text(row, category)
                    if not dry_run:
                        reject(conn, row.candidate, reviewer, text)
                    outcome["rejected"].append(row.candidate)
                else:
                    if not dry_run:
                        approve(conn, row.candidate, reviewer)
                    outcome["approved"].append(row.candidate)
            except DecisionRefused as refused:
                outcome["refused"].append(f"{row.candidate}: {refused}")
    finally:
        if conn is not None:
            conn.close()
    return outcome


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--reviewer", required=True, help="the person whose decisions these are")
    parser.add_argument(
        "--reject-reason",
        required=True,
        help="one of config/review.yaml's rejection_reasons, stored in front of each rejection's evidence; "
        "the workbook has no category column, so one run carries one category",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    rows = read_rows(args.workbook)
    outcome = apply(rows, args.reviewer, args.reject_reason, dry_run=args.dry_run)

    label = "would " if args.dry_run else ""
    print(f"{len(rows)} rows read from {args.workbook.name}; reviewer {args.reviewer}")
    for key in ("approved", "rejected"):
        print(f"  {label}{key}: {len(outcome[key])}  {' '.join(outcome[key])}")
    print(f"  monitor, left pending (no such status): {len(outcome['monitor'])}  {' '.join(outcome['monitor'])}")
    print(f"  blank, left pending: {len(outcome['blank'])}  {' '.join(outcome['blank'])}")
    for line in outcome["refused"]:
        print(f"  REFUSED {line}")
    unchecked = [r.candidate for r in rows if r.decision == "approve" and r.zoho_checked.upper() != "Y"]
    if unchecked:
        print(f"  approved without 'Zoho checked = Y': {len(unchecked)}  {' '.join(unchecked)}")
        print("  the duplicate check is the reviewer's only control; do it before the first export batch")
    return 1 if outcome["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
