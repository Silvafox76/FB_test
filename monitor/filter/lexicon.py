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

**Apostrophes are normalised on both sides before matching, and that is not a second
attempt (rule 1).** French PFM vocabulary is mostly elisions — `système d'information`,
`gestion de l'investissement public`, `exécution d'un budget` — and publishers are not
consistent about which apostrophe they use. Measured on the corpus on 2026-09-12: of
584 French BOAMP notices, 400 carry an ASCII `'` and 119 carry a typographic `’`, and
83 notices across the corpus carry *both* in the same document. TED is the same shape,
87 against 30.

So a phrase can only be written one way in `config/lexicon_fr.yaml` and will miss the
other half of the corpus whichever way it is written. Spelling every elided phrase
twice in the lexicon would be the "second selector" rule 1 exists to forbid, and it
would double by hand on every phrase added afterwards. Instead there is one canonical
form: both the phrase and the notice are folded to ASCII `'` before the pattern is
built, so there is still exactly one match attempt against one form.

This was found through `monitor/normalise/ocr.py` rather than through a miss: poppler
returns `’` for the ASCII apostrophe in a PDF's content stream, so the Burkina Faso
bulletins will arrive typographic no matter what the publisher typed. The two elided
phrases currently in the lexicon match nothing in either form, so nothing is being
missed *today*; this is a latent defect fixed before step 19 adds the elided phrases
that would have hit it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

# Every character a publisher, a word processor or a PDF text layer uses where a
# French elision wants an apostrophe, folded to the one the patterns are built from.
# U+2019 right single quotation mark is what poppler and most CMSes emit; U+02BC
# modifier letter apostrophe and U+2018 turn up in text pasted out of other systems.
APOSTROPHES = str.maketrans({"’": "'", "ʼ": "'", "‘": "'"})


def canonical(text: str) -> str:
    """One apostrophe, so there is one form to match rather than two to try."""
    return text.translate(APOSTROPHES)


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
    in a run and compiling them per notice dominates the stage's cost. The cache
    key is the phrase as the lexicon writes it, so two spellings of one phrase
    would still compile to the same pattern — which is the point.
    """
    return re.compile(rf"(?<!\w){re.escape(canonical(phrase))}(?!\w)", re.IGNORECASE)


def check(title: str, body: str, phrases_by_function: dict[str, list[str]]) -> LexiconVerdict:
    """Match the lexicon for one language against a notice's own text.

    Returns every function that matched, in lexicon order, and the phrases that
    matched them. The reviewer sees the first three on the candidate page, so the
    order has to be deterministic rather than set-ordered.
    """
    # Folded once per notice rather than once per phrase: the same few hundred
    # phrases are matched against it and the fold is the same every time.
    haystack = canonical(f"{title}\n{body}")
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
