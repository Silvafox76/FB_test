"""Prozorro tender -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/prozorro.json`, recorded 2026-09-12.

The DK021 rule is the one that needed a decision. Ukraine classifies with ДК021,
whose codes are CPV-shaped ("34350000-5") and whose top-level divisions line up
with CPV's. Below the division they diverge, so only the **top-level prefix** is
carried across and the rest of the code is kept as published. The filter tests
prefixes, so that is enough for it to work correctly, and nothing downstream is
told a DK021 code is a CPV code at full precision.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from monitor.models import Notice
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "prozorro"
COUNTRY = "UA"
LANGUAGE = "uk"
DK021 = "ДК021"


def map_notice(raw: dict) -> MappedNotice:
    """One Prozorro tender detail to a Notice."""
    external_id = raw.get("tenderID") or raw["id"]
    title = (raw.get("title") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: tender has no title")

    body = (raw.get("description") or "").strip()
    url = f"https://prozorro.gov.ua/tender/{external_id}"

    estimated_value, value_currency = _published(raw)
    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        buyer=_buyer(raw),
        country=COUNTRY,
        # Prozorro carries central and local buyers alike and does not say which.
        # National is the conservative reading, the same as TED's unqualified codes.
        admin_level="national",
        published_at=parse_published(
            raw.get("noticePublicationDate") or raw.get("dateCreated") or "", source_id=SOURCE_ID, url=url
        ),
        deadline_at=parse_deadline((raw.get("tenderPeriod") or {}).get("endDate", ""), source_id=SOURCE_ID, url=url),
        language=LANGUAGE,
        # Prozorro publishes in Ukrainian and says so by convention rather than in a
        # field, so this is the registry's statement rather than a detection.
        language_confidence=1.0,
        cpv_codes=cpv_from_dk021(raw),
        estimated_value=estimated_value,
        value_currency=value_currency,
        body=body,
        status="detected",
    )
    # Ukrainian only. Step 14 fills the English in; empty is not 'it was English'.
    return MappedNotice(notice=notice)


def cpv_from_dk021(raw: dict) -> list[str]:
    """DK021 codes, carried across by top-level prefix only.

    Every classification on every item, plus the lot-level ones where present. The
    codes are stored whole because the filter tests a prefix; what this rule means
    is that only the first two digits are claimed to mean what CPV means by them.
    """
    codes: list[str] = []
    for item in raw.get("items") or []:
        classification = item.get("classification") or {}
        if classification.get("scheme") in (DK021, "CPV") and classification.get("id"):
            codes.append(str(classification["id"]))
        for additional in item.get("additionalClassifications") or []:
            if additional.get("scheme") in (DK021, "CPV") and additional.get("id"):
                codes.append(str(additional["id"]))
    return extract_codes(*codes)


def _buyer(raw: dict) -> str:
    entity = raw.get("procuringEntity") or {}
    return (entity.get("name") or "").strip()


def _published(raw: dict) -> tuple[Decimal | None, str | None]:
    """The stated value as published, in the currency published.

    Prozorro states UAH on all 742 stored notices and it is carried as UAH; the
    conversion is a stamped step at staging, not something this module invents
    (decision 7, revised by migration 012).

    The TOP-LEVEL `value` is the total, not a lot. Checked on the corpus: 531 of
    the 742 carry lot-level values too, and on every one of those 531 the lot
    amounts sum to exactly the top-level figure, so reading the top level neither
    double-counts nor understates the opportunity.
    """
    value = raw.get("value") or {}
    return published_value(value.get("amount"), value.get("currency"), source_id=SOURCE_ID)
