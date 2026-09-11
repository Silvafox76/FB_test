"""Find a Tender release -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/fts.json`, recorded 2026-09-12.

OCDS gives most of this directly. The two judgements are where CPV lives
(`tender.items[].additionalClassifications` with `scheme` "CPV", not
`items[].classification`) and that the value is GBP on essentially everything, so
it is not carried as USD.
"""

from __future__ import annotations

import structlog

from monitor.models import Notice
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice

log = structlog.get_logger(__name__)

SOURCE_ID = "fts"
COUNTRY = "GB"

# OCDS party roles. A buyer whose role says it procures for others is central;
# nothing in the recorded fixture distinguishes local authorities, so national is
# the conservative reading, as it is for TED's unqualified codes.
DEFAULT_ADMIN_LEVEL = "national"


def map_notice(raw: dict) -> MappedNotice:
    """One OCDS release to a Notice."""
    tender = raw["tender"]
    external_id = raw["id"]
    title = (tender.get("title") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: release has no tender title")

    body = (tender.get("description") or "").strip()
    url = f"https://www.find-tender.service.gov.uk/Notice/{external_id}"

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        buyer=((raw.get("buyer") or {}).get("name") or "").strip(),
        country=COUNTRY,
        admin_level=DEFAULT_ADMIN_LEVEL,
        published_at=parse_published(raw.get("date", ""), source_id=SOURCE_ID, url=url),
        deadline_at=parse_deadline((tender.get("tenderPeriod") or {}).get("endDate", ""), source_id=SOURCE_ID, url=url),
        # The release states its own language; the recorded package is English
        # throughout, and OCDS makes it explicit rather than assumed.
        language=(raw.get("language") or "en").split("-")[0].lower(),
        language_confidence=1.0,
        cpv_codes=cpv_codes(tender),
        estimated_value_usd=_value_usd(tender),
        body=body,
        status="detected",
    )
    # Already English. Filling title_en with a copy of the title would say a
    # translation happened when none did.
    return MappedNotice(notice=notice)


def cpv_codes(tender: dict) -> list[str]:
    """Every CPV code across the tender's items.

    `additionalClassifications` is where they are on this publisher;
    `items[].classification` carries the buyer's own scheme and is not CPV.
    """
    codes: list[str] = []
    for item in tender.get("items") or []:
        classification = item.get("classification") or {}
        if classification.get("scheme") == "CPV" and classification.get("id"):
            codes.append(str(classification["id"]))
        for additional in item.get("additionalClassifications") or []:
            if additional.get("scheme") == "CPV" and additional.get("id"):
                codes.append(str(additional["id"]))
    return extract_codes(*codes)


def _value_usd(tender: dict) -> int | None:
    """The stated value, only when it is already USD. Find a Tender states GBP."""
    value = tender.get("value") or {}
    if value.get("currency") != "USD" or value.get("amount") is None:
        return None
    return int(float(value["amount"]))
