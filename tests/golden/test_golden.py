"""The golden set harness.

The property this file exists to hold: the pipeline does not label its own golden
set. A set labelled by the same family of model it measures would score well
against its own opinion, and a real regression would read as agreement. The
harness refuses to run until a person has filled the labels in, and these tests
prove the refusal rather than trusting the docstring.

The arithmetic is tested on a constructed confusion matrix rather than on a model
call, so precision and recall are checked against numbers a person can verify by
hand.
"""

from __future__ import annotations

import csv

import pytest

from monitor.golden import (
    COLUMNS,
    GOLDEN_CSV,
    HISTORY_COLUMNS,
    REQUIRED_COLUMNS,
    GoldenResult,
    NotLabelled,
    append_history,
    read_labels,
    render,
    stage_threshold,
)


def write_csv(path, rows, columns=COLUMNS):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


def result(**overrides) -> GoldenResult:
    payload = {
        "labelled": 30,
        "scored": 30,
        "parked": 0,
        "true_positive": 9,
        "false_positive": 6,
        "false_negative": 3,
        "true_negative": 12,
        "cost_usd": 0.09,
        "prompt_version": "abc123def456",
        "threshold": 60,
    }
    payload.update(overrides)
    return GoldenResult(**payload)


# --- the labels are human ----------------------------------------------------


def test_an_unlabelled_set_refuses_to_run(tmp_path):
    """The whole point. An agent-labelled golden set measures nothing."""
    path = write_csv(tmp_path / "golden.csv", [["id-1", "A notice", "https://x.invalid", ""]])

    with pytest.raises(NotLabelled, match="no label"):
        read_labels(path)


def test_a_partly_labelled_set_refuses_and_says_how_many(tmp_path):
    path = write_csv(
        tmp_path / "golden.csv",
        [
            ["id-1", "A notice", "https://x.invalid", "relevant"],
            ["id-2", "Another", "https://x.invalid", ""],
            ["id-3", "A third", "https://x.invalid", ""],
        ],
    )

    with pytest.raises(NotLabelled, match="2 of 3"):
        read_labels(path)


def test_a_label_outside_the_vocabulary_refuses(tmp_path):
    """'maybe' is not a label. A golden set with a third state cannot be scored."""
    path = write_csv(tmp_path / "golden.csv", [["id-1", "A notice", "https://x.invalid", "maybe"]])

    with pytest.raises(NotLabelled, match="maybe"):
        read_labels(path)


def test_labels_are_read_case_insensitively(tmp_path):
    path = write_csv(
        tmp_path / "golden.csv",
        [["id-1", "A", "https://x.invalid", "Relevant"], ["id-2", "B", "https://x.invalid", " NOT "]],
    )

    assert read_labels(path) == {"id-1": "relevant", "id-2": "not"}


def test_a_missing_file_says_how_to_make_one(tmp_path):
    with pytest.raises(NotLabelled, match="--export"):
        read_labels(tmp_path / "nothing.csv")


def test_a_missing_column_refuses(tmp_path):
    path = write_csv(tmp_path / "golden.csv", [["id-1", "A notice"]], columns=("notice_id", "title"))

    with pytest.raises(NotLabelled, match="label"):
        read_labels(path)


# --- the exported set --------------------------------------------------------


def test_the_exported_set_has_the_columns_the_step_names():
    """BUILD_ORDER step 7: notice_id, title, label. url is added for the labeller."""
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))

    assert set(REQUIRED_COLUMNS) <= set(header)
    assert header == list(COLUMNS)


def test_the_exported_set_is_thirty_notices_and_is_not_labelled_by_the_pipeline():
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 30
    assert all(row["notice_id"] and row["title"] for row in rows)
    assert not any(row["label"].strip() for row in rows), (
        "the golden set has labels in it; if an agent wrote them, the set measures its own opinion"
    )


# --- the arithmetic ----------------------------------------------------------


def test_precision_is_of_what_would_be_staged():
    """9 staged and relevant, 6 staged and not: 9 of 15."""
    assert result().precision == pytest.approx(9 / 15)


def test_recall_is_of_what_a_person_called_relevant():
    """9 found, 3 relevant notices the scorer would not stage: 9 of 12."""
    assert result().recall == pytest.approx(9 / 12)


def test_schema_validity_counts_the_parked_against_the_attempted():
    assert result(scored=28, parked=2).schema_validity == pytest.approx(28 / 30)


def test_mean_cost_is_per_scored_notice():
    assert result(scored=30, cost_usd=0.09).mean_cost_usd == pytest.approx(0.003)


def test_a_run_that_staged_nothing_does_not_divide_by_zero():
    empty = result(true_positive=0, false_positive=0, false_negative=0, true_negative=30)

    assert empty.precision == 0.0
    assert empty.recall == 0.0


def test_the_threshold_comes_from_config():
    """Precision is measured at the threshold that decides what a reviewer sees."""
    assert stage_threshold() == 60


# --- the history -------------------------------------------------------------


def test_history_appends_rather_than_replaces(tmp_path):
    """The trend is the point. A file that only holds the last number hides a regression."""
    path = tmp_path / "history.csv"

    append_history(result(prompt_version="aaaaaaaaaaaa"), path)
    append_history(result(prompt_version="bbbbbbbbbbbb", true_positive=11, false_positive=4), path)

    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [row["prompt_version"] for row in rows] == ["aaaaaaaaaaaa", "bbbbbbbbbbbb"]
    assert list(rows[0]) == list(HISTORY_COLUMNS)


def test_history_records_the_prompt_version_with_every_number():
    """A score is only comparable to another made under the same prompt."""
    assert "prompt_version" in HISTORY_COLUMNS
    assert "model" in HISTORY_COLUMNS


def test_the_report_prints_the_four_numbers_the_step_asks_for():
    printed = render(result())

    assert "precision" in printed
    assert "recall" in printed
    assert "schema validity" in printed
    assert "mean cost per notice" in printed
    assert "abc123def456" in printed
