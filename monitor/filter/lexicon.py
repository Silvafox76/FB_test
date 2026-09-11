"""The free filter's second stage: phrase matching against the function lexicons.

A notice in a language we have a lexicon for is dropped unless it matches at least
one phrase from at least one of the 33 functions. Matching is case-insensitive on
the title and body together, and it is whole-phrase: a phrase is found or it is
not, with no stemming, no fuzzy matching and no second attempt (rule 1). The
lexicons are tuned instead, which is a config change with a version history.

Word boundaries matter more than they look. Without them "TSA" matches inside
"Motsa" and every Polish notice containing "ITAS" as a substring passes. The
pattern requires a non-word character or a string edge on each side, which is
right for both English and French and for the acronyms that carry the most signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class LexiconVerdict:
    """Which functions matched and which phrases said so."""

    matched: bool
    functions: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)


@lru_cache(maxsize=4096)
def _pattern(phrase: str) -> re.Pattern[str]:
    """A whole-phrase, case-insensitive matcher for one lexicon entry.

    Cached because the same few hundred phrases are matched against every notice
    in a run and compiling them per notice dominates the stage's cost.
    """
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)


def check(title: str, body: str, phrases_by_function: dict[str, list[str]]) -> LexiconVerdict:
    """Match the lexicon for one language against a notice's own text.

    Returns every function that matched, in lexicon order, and the phrases that
    matched them. The reviewer sees the first three on the candidate page, so the
    order has to be deterministic rather than set-ordered.
    """
    haystack = f"{title}\n{body}"
    functions: list[str] = []
    phrases: list[str] = []

    for function_id, lexicon_phrases in phrases_by_function.items():
        hit = False
        for phrase in lexicon_phrases:
            if _pattern(phrase).search(haystack):
                phrases.append(phrase)
                hit = True
        if hit:
            functions.append(function_id)

    return LexiconVerdict(matched=bool(functions), functions=functions, phrases=phrases)
