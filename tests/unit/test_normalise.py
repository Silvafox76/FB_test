"""The normaliser's source-independent rules.

Hash stability, deadline parsing and CPV extraction are the three the pipeline
leans on hardest: the hash decides what is new, the deadline decides whether a
candidate is worth a reviewer's minute, and the CPV list decides what is dropped
before any model call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from monitor.normalise.cpv import extract_codes, normalise_code
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash, normalise_text

TITLE = "Supply and implementation of an integrated financial management system"
BODY = "The Ministry of Finance invites bids for the supply, installation and support of a GIFMIS."


# --- hash stability ----------------------------------------------------------


def test_the_same_notice_hashes_the_same_twice():
    assert content_hash(TITLE, BODY) == content_hash(TITLE, BODY)


def test_whitespace_changes_do_not_change_the_hash():
    """A portal re-rendering its HTML must not look like a new notice."""
    reindented = f"  {TITLE}  "
    rewrapped = BODY.replace(" ", "\n  ", 1).replace("bids", "bids \t")

    assert content_hash(reindented, rewrapped) == content_hash(TITLE, BODY)


def test_a_real_text_change_changes_the_hash():
    assert content_hash(TITLE, BODY + " Addendum 1.") != content_hash(TITLE, BODY)


def test_moving_text_between_title_and_body_changes_the_hash():
    """The separator cannot be forged by collapsing whitespace, so the split is stable."""
    assert content_hash(TITLE + " " + BODY, "") != content_hash(TITLE, BODY)


def test_normalise_text_collapses_but_does_not_fold_case():
    """Case folding would make two genuinely different notices collide."""
    assert normalise_text("  a   b \n c ") == "a b c"
    assert content_hash("IFMIS", BODY) != content_hash("ifmis", BODY)


# --- deadlines ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        # An offset is an instant, not a wall clock: 17:00+01:00 is 16:00 UTC.
        ("2026-11-30T17:00:00+01:00", datetime(2026, 11, 30, 16, 0, tzinfo=UTC)),
        ("2026-11-30T17:00:00Z", datetime(2026, 11, 30, 17, 0, tzinfo=UTC)),
        # No offset means UTC. Sources that publish local time without saying so
        # are a per-source mapper's problem, not a guess made here.
        ("2026-11-30T17:00:00", datetime(2026, 11, 30, 17, 0, tzinfo=UTC)),
        ("2026-11-30 17:00:00", datetime(2026, 11, 30, 17, 0, tzinfo=UTC)),
        ("2026-11-30T17:00+01:00", datetime(2026, 11, 30, 16, 0, tzinfo=UTC)),
    ],
)
def test_deadline_parses_the_formats_feeds_actually_emit(raw, expected_utc):
    parsed = parse_deadline(raw)

    assert parsed is not None
    assert parsed.astimezone(UTC) == expected_utc


def test_a_date_with_no_time_is_the_end_of_that_day():
    """A tender closing on the 30th is open on the 30th."""
    parsed = parse_deadline("2026-11-30")

    assert parsed is not None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 11, 30, 23)


def test_a_published_date_with_no_time_is_its_midnight():
    parsed = parse_published("2026-11-30")

    assert parsed is not None
    assert (parsed.hour, parsed.minute) == (0, 0)


@pytest.mark.parametrize("raw", ["30/11/2026", "1 December 2026", "next Friday", "", "   ", "not a date"])
def test_an_unparseable_deadline_is_none_and_never_a_guess(raw):
    """Ambiguous and natural-language dates are refused, not guessed (rule 10)."""
    assert parse_deadline(raw) is None


def test_an_offset_is_preserved_not_assumed_utc():
    parsed = parse_deadline("2026-11-30T17:00:00+05:00")

    assert parsed is not None
    assert parsed.utcoffset() is not None
    assert parsed.utcoffset().total_seconds() == 5 * 3600


# --- CPV ---------------------------------------------------------------------


def test_the_check_digit_is_dropped():
    assert normalise_code("48000000-8") == "48000000"
    assert normalise_code("72000000") == "72000000"


def test_a_non_cpv_string_raises():
    with pytest.raises(ValueError, match="not a CPV code"):
        normalise_code("480000")


def test_codes_are_extracted_from_several_fields_in_order_seen():
    codes = extract_codes("48000000-8", "72200000-7, 72300000-8", "")

    assert codes == ["48000000", "72200000", "72300000"]


def test_duplicate_codes_are_collapsed():
    assert extract_codes("48000000-8 48000000-9", "48000000") == ["48000000"]


def test_extraction_does_not_decide_what_passes():
    """Rule 5: the normaliser extracts, monitor/filter/cpv.py decides."""
    assert extract_codes("45000000-7") == ["45000000"]
