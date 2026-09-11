"""TED notice -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/ted.json`, recorded 2026-09-11. The
shapes this handles are described in `monitor/connectors/ted.py`'s docstring.

Rule 9 is the constraint that shapes this module: `title` and `body` are the
notice as published, in the notice's own language, and TED's own English
rendering is a derived field carried separately. Rule 10 is the other: the
deadline is parsed by rule from the original date string.
"""

from __future__ import annotations

from dataclasses import dataclass

from monitor.connectors.ted import notice_url
from monitor.models import Notice
from monitor.normalise.codes import country_alpha2, language_alpha2
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash

SOURCE_ID = "ted"

# TED's buyer legal types, from the recorded fixture's spread. `cga` is a central
# government authority, `ra` regional, `la` local; `body-pl` is a body governed by
# public law, which takes the level of whichever authority it hangs off. Anything
# unrecognised is national, which is the conservative reading for scoring: a
# national buyer is the larger opportunity, so it is the one a reviewer should see.
ADMIN_LEVEL_BY_LEGAL_TYPE = {
    "cga": "national",
    "body-pl-cga": "national",
    "pub-undert-cga": "national",
    "eu-ins-bod-ag": "national",
    "ra": "regional",
    "body-pl-ra": "regional",
    "pub-undert-ra": "regional",
    "la": "local",
    "body-pl-la": "local",
    "pub-undert-la": "local",
}
DEFAULT_ADMIN_LEVEL = "national"

# TED translates every notice into all 24 EU languages itself, so the English
# rendering is free and the step 14 translation stage never has to run for TED.
ENGLISH = "eng"


@dataclass(frozen=True)
class TedNotice:
    """A mapped notice and the English rendering TED supplied alongside it.

    `title_en` is not on `Notice` because an English title is a derived field with
    a provenance, and TED's provenance is TED rather than a model call. It is
    carried here so the scorer can use it without a translation and without
    anything overwriting `notice.title` (rule 9).
    """

    notice: Notice
    title_en: str
    body_en: str


def map_notice(raw: dict) -> TedNotice:
    """One TED search result to a Notice. Raises on anything it cannot map."""
    language = language_alpha2(raw["official-language"][0])
    original = raw["official-language"][0].lower()

    title = _in_language(raw["notice-title"], original)
    body = _in_language(raw["description-proc"], original)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=raw["publication-number"],
        url=notice_url(raw),
        title=title,
        buyer=_buyer(raw, original),
        country=country_alpha2(raw["buyer-country"][0]),
        admin_level=_admin_level(raw),
        published_at=parse_published(raw["publication-date"], source_id=SOURCE_ID, url=notice_url(raw)),
        deadline_at=_deadline(raw),
        language=language,
        # TED states the notice's own language rather than detecting it, so there
        # is nothing to be uncertain about.
        language_confidence=1.0,
        cpv_codes=extract_codes(*raw["classification-cpv"]),
        estimated_value_usd=_value_usd(raw),
        body=body,
        status="detected",
    )
    return TedNotice(
        notice=notice,
        title_en=_in_language(raw["notice-title"], ENGLISH),
        body_en=_in_language(raw["description-proc"], ENGLISH),
    )


def _in_language(values: dict[str, str], code: str) -> str:
    """One rendering from TED's per-language object, deterministically."""
    if code in values:
        return values[code]
    return values[sorted(values)[0]]


def _buyer(raw: dict, language: str) -> str:
    """`buyer-name` is per language and each value is a list of names."""
    names = raw["buyer-name"]
    chosen = names.get(language) or names[sorted(names)[0]]
    return chosen[0] if chosen else ""


def _admin_level(raw: dict) -> str:
    legal_types = raw.get("buyer-legal-type") or []
    for legal_type in legal_types:
        if legal_type in ADMIN_LEVEL_BY_LEGAL_TYPE:
            return ADMIN_LEVEL_BY_LEGAL_TYPE[legal_type]
    return DEFAULT_ADMIN_LEVEL


def _deadline(raw: dict):
    """The earliest per-lot deadline, which is the first one that binds.

    TED gives one date per lot. A reviewer looking at the candidate needs the
    soonest date they could miss, not the last.
    """
    dates = raw.get("deadline-receipt-tender-date-lot") or []
    parsed = [
        value
        for value in (parse_deadline(entry, source_id=SOURCE_ID, url=notice_url(raw)) for entry in dates)
        if value is not None
    ]
    return min(parsed) if parsed else None


def _value_usd(raw: dict) -> int | None:
    """The stated value, only when it is already in USD.

    TED states a currency alongside the value and most of them are EUR. Converting
    would need an exchange rate and a date, and nothing in this pilot has either;
    an invented conversion would reach the reviewer as a number that looks
    researched. Open decision 6: what the record builder does with a EUR value.
    """
    value = raw.get("estimated-value-proc")
    currency = raw.get("estimated-value-cur-proc")
    if not value or currency != "USD":
        return None
    return int(float(value))
