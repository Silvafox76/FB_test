"""Join notices about the same procurement into one candidate.

A tender reaches us more than once: TED carries it, the country's own portal
carries it, and if a donor financed it the World Bank carries it too. The reviewer
should see one candidate with three sources behind it, not three candidates.

Three rules, tried in order, all within the same country. Country is a hard gate
rather than a weighted signal: two ministries of finance buying an IFMIS in the
same month are two opportunities, and joining them would hide one.

  1. Exact content hash. The same text from two sources. Certain, so it needs no
     other evidence.
  2. Fuzzy title on the English rendering, `token_set_ratio >= 85`, with deadlines
     within seven days. `title_en` is what makes a French notice and its English
     mirror comparable at all; matching the originals would never join them.
  3. A shared system name with `token_set_ratio >= 40` and deadlines within seven
     days. A notice naming GIFMIS and another naming GIFMIS in the same country
     inside a week is almost certainly the same programme, even when the titles
     read differently. The lower ratio is doing less work here because the system
     name is carrying the evidence. Step 20: this rule is tried only after rule 2
     has been tried against every candidate, so it is the fallback for a pair the
     titles alone would not join and never a second, easier route to a match a
     stricter rule already declined.

Rules 2 and 3 both require a deadline within seven days *and* both notices to have
a deadline. Without one there is no window, and joining on title alone would merge
a tender with last year's rerun of it.

**Where `title_en` comes from** (step 20). The stager hands both sides
`scores.title_en`: the English title the scorer returns, which appendix C makes a
required, non-empty field of `Score`, so a scored notice always has one — 221 of
221 rows non-empty on 2026-09-12. It is native English carried through where the
notice was published in English and the step 14 translation where it was not, which
is what step 20 means by "whether native or translated": both arrive in one column.

It is deliberately not `translations.title_en`, the other place an English
rendering lives, for two measured reasons. It is missing: 33 of the 146 scored
notices on 2026-09-12 have no `translations` row at all, nine of them French. And
for a TED notice it is only half English — TED's own `ted-eforms`/`source-native`
rendering translates the standardised CPV heading and leaves the buyer's own words
in the original language, on 618 of 738 non-English TED notices. Comparing that
against an English mirror is comparing French to English, which is the thing
cross-language dedupe exists to stop doing.

Absence is not a comparison. A notice with no English rendering is skipped by both
text rules rather than scored on an empty string, and nothing falls back to the
original-language title (rule 1): the pair is not comparable, which is different
from being compared and found different. Measured on 2026-09-12, rapidfuzz scores
an empty string against anything at 0.0, so today an empty rendering fails both
floors unaided. The guard is here so that stays a property of this module rather
than of whichever rapidfuzz is installed, because a library that scored two empty
strings 100 would silently join every renderingless notice sharing a country and a
deadline week.

The functions here are pure: they take notices and candidates and return a verdict.
Nothing in this module touches the database (rule 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from rapidfuzz.fuzz import token_set_ratio

# BUILD_ORDER step 8. Not config: these are the deduper's own mechanics rather than
# a business threshold someone tunes per region, and step 29 decides whether the
# method changes at all, on measured cross-language duplicate rate.
TITLE_RATIO = 85
SYSTEM_NAME_RATIO = 40
DEADLINE_WINDOW = timedelta(days=7)

MATCH_CONTENT_HASH = "content_hash"
MATCH_TITLE_FUZZY = "title_fuzzy"
MATCH_SYSTEM_NAME = "system_name"


@dataclass(frozen=True)
class Candidate:
    """The bit of a candidate the deduper compares against."""

    id: str
    country: str
    title_en: str
    system_names: tuple[str, ...]
    deadline_at: datetime | None
    content_hashes: frozenset[str]


@dataclass(frozen=True)
class Incoming:
    """The bit of a newly scored notice the deduper compares."""

    notice_id: str
    country: str
    title_en: str
    system_names: tuple[str, ...]
    deadline_at: datetime | None
    content_hash: str


@dataclass(frozen=True)
class Match:
    candidate_id: str
    method: str
    score: int


def within_window(left: datetime | None, right: datetime | None) -> bool:
    """Both deadlines known and within seven days of each other.

    A missing deadline is not a match. Joining on title alone would merge a tender
    with last year's rerun, which reads to a reviewer as a live opportunity that
    closed eleven months ago.
    """
    if left is None or right is None:
        return False
    return abs(left - right) <= DEADLINE_WINDOW


def both_rendered(left: str, right: str) -> bool:
    """Both sides carry an English rendering, so the two texts can be compared.

    Whitespace-only counts as absent: `coalesce(title_en, '')` on a notice with no
    rendering is the shape this is defending against, and a single space is the
    same absence with a character in it.
    """
    return bool(left.strip()) and bool(right.strip())


def shared_system_names(left: tuple[str, ...], right: tuple[str, ...]) -> list[str]:
    lowered = {name.lower() for name in right}
    return [name for name in left if name.lower() in lowered]


def find_match(incoming: Incoming, candidates: list[Candidate]) -> Match | None:
    """The first candidate this notice belongs to, or None to make a new one.

    Candidates are tried in the order given, and the rules in the order above, so
    the result does not depend on dictionary ordering: an exact hash match on the
    third candidate beats a fuzzy title match on the first.
    """
    same_country = [candidate for candidate in candidates if candidate.country == incoming.country]

    for candidate in same_country:
        if incoming.content_hash in candidate.content_hashes:
            return Match(candidate.id, MATCH_CONTENT_HASH, 100)

    for candidate in same_country:
        if not within_window(incoming.deadline_at, candidate.deadline_at):
            continue
        if not both_rendered(incoming.title_en, candidate.title_en):
            continue
        ratio = int(token_set_ratio(incoming.title_en, candidate.title_en))
        if ratio >= TITLE_RATIO:
            return Match(candidate.id, MATCH_TITLE_FUZZY, ratio)

    # Only now, with the fuzzy rule exhausted against every candidate, does the
    # system name get a say (step 20). The title floor stays: the system name says
    # these two notices are about the same programme, and the floor says they are
    # about the same procurement within it, which is what a candidate is.
    for candidate in same_country:
        if not within_window(incoming.deadline_at, candidate.deadline_at):
            continue
        if not shared_system_names(incoming.system_names, candidate.system_names):
            continue
        if not both_rendered(incoming.title_en, candidate.title_en):
            continue
        ratio = int(token_set_ratio(incoming.title_en, candidate.title_en))
        if ratio >= SYSTEM_NAME_RATIO:
            return Match(candidate.id, MATCH_SYSTEM_NAME, ratio)

    return None
