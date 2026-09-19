"""Public Contracts Scotland (below-threshold site contracts) release -> Notice.

Written against `tests/contract/fixtures/pcs_gb_sct.json`, recorded 2026-09-19:
62 `noticeType=102` releases for September 2026. Same OCDS family as
`monitor/normalise/fts.py`, and the CPV extraction is literally the same
function, imported rather than copied (rule 8): PCS carries CPV codes at
`tender.items[].additionalClassifications` with `scheme` "CPV", the identical
path fts.py already reads, and `tender.items[].classification` is `None` on
every recorded item on both sources.

Two judgements beyond what fts.py already made:

  - The body is `tender.description`, never the release-level `description`.
    The release-level field is fixed boilerplate about the Postbox facility and
    the SC reference number; it carries no information about the procurement
    and using it would put the same paragraph into every notice's body.
  - `admin_level` is fixed to "regional": every buyer on this source is a
    Scottish public body by construction of what the source lists, so there is
    no national/sub-national judgement to make per release the way there is for
    fts.py's unqualified national government buyers.
"""

from __future__ import annotations

from decimal import Decimal

from monitor.connectors.pcs_gb_sct import release_url
from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.fts import cpv_codes
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

SOURCE_ID = "pcs_gb_sct"
COUNTRY = "GB"

# Every buyer on this source is a Scottish public body by construction of what
# `noticeType=102` lists, so - unlike fts.py's unqualified national buyers -
# there is no per-release judgement to make: sub-national is known, not guessed.
DEFAULT_ADMIN_LEVEL = "regional"


def map_notice(raw: dict) -> MappedNotice:
    """One OCDS release to a Notice."""
    tender = raw["tender"]
    external_id = raw["id"]
    title = (tender.get("title") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: release has no tender title")

    # tender.description, never the release-level description (Postbox/SC-ref
    # boilerplate repeated on every release - see the module docstring).
    body = (tender.get("description") or "").strip()
    url = release_url(raw)

    estimated_value, value_currency = _published(tender)
    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=external_id,
        url=url,
        title=title,
        buyer=((raw.get("buyer") or {}).get("name") or "").strip(),
        country=COUNTRY,
        admin_level=DEFAULT_ADMIN_LEVEL,
        # `date` is stated at midnight UTC on every recorded release - precision
        # is date-only, never a real time of publication.
        published_at=parse_published(raw.get("date", ""), source_id=SOURCE_ID, url=url),
        deadline_at=parse_deadline((tender.get("tenderPeriod") or {}).get("endDate", ""), source_id=SOURCE_ID, url=url),
        # This publisher states "EN" in upper case; OCDS language codes are
        # lower case everywhere else in this pipeline, so it is lowered here.
        language=(raw.get("language") or "en").lower(),
        language_confidence=1.0,
        cpv_codes=cpv_codes(tender),
        estimated_value=estimated_value,
        value_currency=value_currency,
        body=body,
        status="detected",
    )
    # Already English. Filling title_en with a copy of the title would say a
    # translation happened when none did.
    return MappedNotice(notice=notice)


def _published(tender: dict) -> tuple[Decimal | None, str | None]:
    """The stated value as published. Public Contracts Scotland states GBP on
    every recorded release with a value, so it is carried in GBP, not USD."""
    value = tender.get("value") or {}
    return published_value(value.get("amount"), value.get("currency"), source_id=SOURCE_ID)
