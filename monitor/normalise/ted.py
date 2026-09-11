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

import structlog

from monitor.connectors.ted import notice_url
from monitor.models import Notice
from monitor.normalise.codes import country_alpha2, language_alpha2
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash

log = structlog.get_logger(__name__)

SOURCE_ID = "ted"

# TED's buyer legal types, from the recorded fixture's spread. `cga` is a central
# government authority, `ra` regional, `la` local; `body-pl` is a body governed by
# public law, which takes the level of whichever authority it hangs off. Anything
# unrecognised is national, which is the conservative reading for scoring: a
# national buyer is the larger opportunity, so it is the one a reviewer should see.
# An unqualified code ("body-pl", "pub-undert") states the kind of body without
# stating its level, which covers 17 of the 50 recorded notices. National is the
# conservative reading for those, for the same reason it is the default: a national
# buyer is the larger opportunity and the one a reviewer should see.
ADMIN_LEVEL_BY_LEGAL_TYPE = {
    "cga": "national",
    "body-pl": "national",
    "pub-undert": "national",
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

# TED translates the *title* into all 24 EU languages: `eng` is present on the
# title of all 50 recorded notices. The *description* is not translated. It
# carries one language on 48 of those 50, so an English body is the exception, not
# the rule, and TED still needs the step 14 translation stage for its bodies.
ENGLISH = "eng"


@dataclass(frozen=True)
class TedNotice:
    """A mapped notice and the English rendering TED supplied alongside it.

    `title_en` is not on `Notice` because an English title is a derived field with
    a provenance, and TED's provenance is TED rather than a model call. It is
    carried here so the scorer can use it without a translation and without
    anything overwriting `notice.title` (rule 9).

    `body_en` is empty far more often than not: TED translates titles, not
    descriptions. Empty means no English was published, never that the body was
    English.
    """

    notice: Notice
    title_en: str
    body_en: str


def map_notice(raw: dict) -> TedNotice:
    """One TED search result to a Notice. Raises on anything it cannot map."""
    language = language_alpha2(raw["official-language"][0])
    original = raw["official-language"][0].lower()

    external_id = raw["publication-number"]
    title = _original(raw["notice-title"], original, "notice-title", external_id)
    body = _original(raw["description-proc"], original, "description-proc", external_id)

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
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
        title_en=_english(raw["notice-title"], "notice-title", external_id),
        body_en=_english(raw["description-proc"], "description-proc", external_id),
    )


def _original(values: dict[str, str], code: str, field: str, external_id: str) -> str:
    """The notice's own language. Raises if TED does not carry it.

    Substituting another language here would store, say, an Italian title on a
    notice whose `language` column says `es`, and every stage downstream would
    believe the column. That is the drift codes.py raises for; this raises too.
    """
    if code not in values:
        raise ValueError(
            f"{external_id}: {field} has no entry for the notice's own language {code!r}; got {sorted(values)}"
        )
    return values[code]


def _english(values: dict[str, str], field: str, external_id: str) -> str:
    """TED's English rendering, or nothing at all.

    TED translates into all 24 EU languages, so `eng` is present on every notice
    in the recorded response. If it ever is not, no English is better than another
    language stored as though it were English: the caller writes no translations
    row and the scorer sees the original.
    """
    if ENGLISH in values:
        return values[ENGLISH]
    log.warning("ted_no_english_rendering", field=field, external_id=external_id, languages=sorted(values))
    return ""


def _buyer(raw: dict, language: str) -> str:
    """`buyer-name` is per language and each value is a list of names."""
    names = raw["buyer-name"]
    chosen = names.get(language) or names[sorted(names)[0]]
    return chosen[0] if chosen else ""


def _admin_level(raw: dict) -> str:
    """The buyer's level of government.

    Absent is normal: 4 of the 50 recorded notices carry no legal type, and
    national is the conservative reading, since a national buyer is the larger
    opportunity and the one a reviewer should see. Present but unrecognised is
    not normal: TED's vocabulary is longer than the ten values below, and a new
    code silently becoming "national" is a scoring error nobody would find.
    """
    legal_types = raw.get("buyer-legal-type") or []
    if not legal_types:
        return DEFAULT_ADMIN_LEVEL

    for legal_type in legal_types:
        if legal_type in ADMIN_LEVEL_BY_LEGAL_TYPE:
            return ADMIN_LEVEL_BY_LEGAL_TYPE[legal_type]

    raise ValueError(
        f"{raw['publication-number']}: unknown buyer-legal-type {legal_types}; "
        "add it to ADMIN_LEVEL_BY_LEGAL_TYPE in monitor/normalise/ted.py"
    )


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
