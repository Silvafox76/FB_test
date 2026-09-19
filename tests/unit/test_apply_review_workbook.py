"""The workbook reader and the decision mapping, without a database.

`apply()` in dry-run mode never connects, so the mapping from the reviewer's three
words to the two statuses (and the two skips) is tested here; the write itself is
`review/decisions.py`'s and is covered by tests/review/test_decisions.py.
"""

from __future__ import annotations

import openpyxl
import pytest

from review.decisions import DecisionRefused
from scripts.apply_review_workbook import Row, apply, read_rows, rejection_text

HEADER = ["#", "candidate", "why", "YOUR DECISION", "YOUR REASON", "Zoho checked (Y/N)"]


def workbook(tmp_path, rows):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Queue"
    sheet.append(HEADER)
    for row in rows:
        sheet.append(row)
    path = tmp_path / "queue.xlsx"
    book.save(path)
    return path


def test_reads_only_rows_with_a_candidate_and_normalises_the_decision(tmp_path):
    path = workbook(
        tmp_path,
        [
            [1, "C000001", "not PFM", " Reject ", "See why column from Matthew", None],
            [2, "C000002", "a real one", "APPROVE", "", "Y"],
            [None, None, None, None, None, None],
        ],
    )
    rows = read_rows(path)
    assert [r.candidate for r in rows] == ["C000001", "C000002"]
    assert rows[0].decision == "reject"
    assert rows[1].decision == "approve"
    assert rows[1].zoho_checked == "Y"


def test_missing_column_is_refused(tmp_path):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Queue"
    sheet.append(["candidate", "YOUR DECISION"])
    path = tmp_path / "bad.xlsx"
    book.save(path)
    with pytest.raises(ValueError, match="no column"):
        read_rows(path)


def test_rejection_text_uses_the_why_column_when_the_reviewer_points_at_it():
    row = Row("C000001", "reject", "See why column from Matthew", "Outsourced audit service, not software.", "")
    assert rejection_text(row, "Not PFM") == "Not PFM: Outsourced audit service, not software."


def test_rejection_text_keeps_the_reviewers_own_words_in_front():
    row = Row("C000001", "reject", "False positive", "A housing company outsourcing payroll.", "")
    assert rejection_text(row, "Not PFM") == "Not PFM: False positive A housing company outsourcing payroll."


def test_rejection_with_no_reason_anywhere_is_refused():
    row = Row("C000001", "reject", "See why column", "", "")
    with pytest.raises(DecisionRefused, match="no reason"):
        rejection_text(row, "Not PFM")


def test_category_must_come_from_the_config_list():
    row = Row("C000001", "reject", "", "why", "")
    with pytest.raises(ValueError, match="rejection_reasons"):
        rejection_text(row, "Because")


def test_dry_run_maps_every_decision_and_touches_nothing():
    rows = [
        Row("C000001", "approve", "", "", ""),
        Row("C000002", "reject", "See why column", "not PFM", ""),
        Row("C000003", "monitor", "", "", ""),
        Row("C000004", "", "", "", ""),
        Row("C000005", "reject", "", "", ""),
    ]
    outcome = apply(rows, "Matthew", "Not PFM", dry_run=True)
    assert outcome["approved"] == ["C000001"]
    assert outcome["rejected"] == ["C000002"]
    assert outcome["monitor"] == ["C000003"]
    assert outcome["blank"] == ["C000004"]
    assert outcome["refused"] == ["C000005: C000005: reject with no reason in either column"]


def test_an_unknown_word_stops_the_whole_run():
    with pytest.raises(ValueError, match="unknown decisions"):
        apply([Row("C000001", "maybe", "", "", "")], "Matthew", "Not PFM", dry_run=True)
