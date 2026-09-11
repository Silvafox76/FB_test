"""The change-detection key.

`content_hash = sha256(normalised title + body)`. Two runs over an unchanged
notice produce the same hash and the second run inserts nothing, which is what
makes a daily pass cheap and what `make fetch S=ted` twice proves.

Normalisation before hashing is deliberately minimal: collapse runs of whitespace,
strip the ends, and join title and body with a separator that cannot appear in
either after collapsing. A portal that re-renders its HTML with different
indentation must not look like a new notice. Anything more aggressive (case
folding, punctuation stripping) would make two genuinely different notices
collide, which is the more expensive mistake: a missed opportunity is invisible.
"""

from __future__ import annotations

import hashlib
import re

WHITESPACE = re.compile(r"\s+")

# A newline cannot survive whitespace collapsing, so this separator cannot be
# produced by the title or the body themselves.
SEPARATOR = "\n"


def normalise_text(value: str) -> str:
    """Collapse whitespace runs to one space and strip the ends. Nothing else."""
    return WHITESPACE.sub(" ", value).strip()


def content_hash(title: str, body: str) -> str:
    """The sha256 of the normalised title and body, as stored in notices_raw."""
    material = normalise_text(title) + SEPARATOR + normalise_text(body)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
