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
    dropped_share,
    export,
    passed_share,
    read_labels,
    render,
    set_size,
    stage_threshold,
)
from monitor.registry.load import load_sources


def row(notice_id, title, label):
    """One golden row, in COLUMNS order, with the fields these tests do not vary.

    Written as a helper rather than a literal so that adding a column to the export
    is one edit here. `source_id` and `language` were added at step 21 and every one
    of these tests would otherwise have had to repeat them.
    """
    return [notice_id, "ted", "en", title, "https://x.invalid", label]


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
    path = write_csv(tmp_path / "golden.csv", [row("id-1", "A notice", "")])

    with pytest.raises(NotLabelled, match="no label"):
        read_labels(path)


def test_a_partly_labelled_set_refuses_and_says_how_many(tmp_path):
    path = write_csv(
        tmp_path / "golden.csv",
        [
            row("id-1", "A notice", "relevant"),
            row("id-2", "Another", ""),
            row("id-3", "A third", ""),
        ],
    )

    with pytest.raises(NotLabelled, match="2 of 3"):
        read_labels(path)


def test_a_label_outside_the_vocabulary_refuses(tmp_path):
    """'maybe' is not a label. A golden set with a third state cannot be scored."""
    path = write_csv(tmp_path / "golden.csv", [row("id-1", "A notice", "maybe")])

    with pytest.raises(NotLabelled, match="maybe"):
        read_labels(path)


def test_labels_are_read_case_insensitively(tmp_path):
    path = write_csv(
        tmp_path / "golden.csv",
        [row("id-1", "A", "Relevant"), row("id-2", "B", " NOT ")],
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


def test_the_exported_set_is_the_configured_size_and_is_not_labelled_by_the_pipeline():
    """Step 21's acceptance names 150. The size is read, not repeated, so it moves once."""
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == set_size()
    assert all(row["notice_id"] and row["title"] for row in rows)
    assert not any(row["label"].strip() for row in rows), (
        "the golden set has labels in it; if an agent wrote them, the set measures its own opinion"
    )


def test_the_set_holds_one_row_per_notice():
    """`translations` is keyed on (notice, prompt version), so a plain join duplicates.

    The same fan-out was found in the scorer and the stager on 2026-09-12 and cost 75
    paid model calls. The 30-row set escaped it only because it took the oldest rows,
    which predate the translator; at this size it would not have.
    """
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        ids = [row["notice_id"] for row in csv.DictReader(handle)]

    assert len(ids) == len(set(ids)), "a notice appears twice; the rendering join is fanning out"


def test_the_set_straddles_the_free_filter():
    """A set drawn only from what the filter passed cannot measure what it dropped.

    91% of the corpus is discarded before any model sees it, so a relevant notice
    killed by a CPV code or a missing lexicon phrase is invisible to a set built from
    survivors alone. Labelling a dropped notice `relevant` reports a FILTER miss; the
    two defects have different fixes and a set that cannot separate them finds one.
    """
    assert passed_share() < set_size(), "the set must include notices the free filter dropped"
    assert dropped_share() >= 1


def test_exporting_over_a_labelled_set_refuses(tmp_path, monkeypatch):
    """The labels are the one thing here that cannot be rebuilt from the database.

    `export` opens the file with "w", so before this refusal `monitor golden --export`
    on a labelled set destroyed hours of a named person's judgement without a word.
    Everything else in this repository regenerates: the draw, the fixtures, the
    migrations. The labels do not.

    No database connection is needed, and that is asserted rather than incidental -
    the refusal must come before the query, not after a draw has already been made.
    """
    labelled = tmp_path / "golden.csv"
    write_csv(labelled, [row("id-1", "A notice", "relevant"), row("id-2", "Another", "")])
    monkeypatch.setattr("monitor.golden.GOLDEN_CSV", labelled)

    with pytest.raises(RuntimeError, match="1 label"):
        export(conn=None)


def test_exporting_over_an_unlabelled_set_is_allowed(tmp_path, monkeypatch):
    """Re-drawing a set nobody has touched is routine and must not need a ceremony."""
    untouched = tmp_path / "golden.csv"
    write_csv(untouched, [row("id-1", "A notice", ""), row("id-2", "Another", "")])
    monkeypatch.setattr("monitor.golden.GOLDEN_CSV", untouched)

    # Reaches the database rather than the guard: no connection, so psycopg raises.
    with pytest.raises(AttributeError):
        export(conn=None)


def test_the_set_carries_at_least_fifty_non_english_notices():
    """BUILD_ORDER step 21 names the floor, and it is the harder half of the set.

    An English-only golden set measures the scorer on the language the prompt was
    written in and says nothing about the path every non-English notice takes: the
    free filter's French lexicon, then the step 14 translation, then the scorer
    reading a machine rendering rather than a publisher's own words. The corpus is
    mostly not English - 129 of these 150 are not - so the floor is comfortable here
    and is asserted anyway, because a future draw from a differently-shaped corpus
    could fall under it without anything else failing.
    """
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    non_english = [row for row in rows if row["language"] != "en"]
    assert len(non_english) >= 50, f"only {len(non_english)} non-English notices in the set"


def test_the_set_is_drawn_from_real_sources_and_not_from_test_fixtures():
    """A fixture row that survives its own teardown is drawn into the set on purpose.

    Not hypothetically. On 2026-09-12 `tests/roles/test_roles.py` picked a random
    six-digit candidate id from the whole `C[0-9]{6}` space, collided with a real
    stager-allocated candidate, and aborted in setup *after* committing its source
    and notice rows on an owner connection. The orphan sat in `notices` at status
    `scored`, indistinguishable from real data to every query in the system, and the
    next export drew it - guaranteed, not by chance, because the stratifier gives
    every source at least one seat and that source had exactly one notice.

    The cause is fixed in the fixture. This asserts the symptom separately, because
    the cause is in a different file and a golden set quietly measuring the scorer
    against a row a test invented is worth catching in its own right.
    """
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        sources = {row["source_id"] for row in csv.DictReader(handle)}

    registry = {source.id for source in load_sources()}
    unknown = sorted(sources - registry)
    assert not unknown, f"the golden set draws from {unknown}, which are not in sources/"


def test_the_set_covers_more_than_one_source():
    """The old 30-row set was 30 TED notices, which measures TED.

    Step 21 asks for a draw across TED, Prozorro, World Bank, Senegal, Ghana and a
    Balkan portal. Three of those six have no connector yet, so the floor asserted
    here is the shape of the requirement - a set that spans the registry rather than
    one source - and not the six names, which would fail for a reason unrelated to
    the golden set.
    """
    with GOLDEN_CSV.open(encoding="utf-8", newline="") as handle:
        sources = {row["source_id"] for row in csv.DictReader(handle)}

    assert len(sources) >= 5, f"the set covers only {sorted(sources)}"


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
