"""Contracts Finder release -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/contractsfinder_gb.json`, recorded
2026-09-19 (14 releases; `tests/contract/fixtures/contractsfinder_gb.notes.txt`
records the one change made to it before commit - a named individual buyer
contact redacted per rule 19 - and nothing else). Same publisher family and
the same OCDS 1.1 shape as Find a Tender (`fts.py`), with zero overlap in the
notices each one carries (`monitor/connectors/contractsfinder_gb.py`'s own
docstring), and one shape difference from it that matters here:

**CPV lives at `tender.classification`, not `items[].additionalClassifications`.**
Confirmed present, with `scheme` "CPV", on all 14 recorded releases at that one
path. Find a Tender's own `additionalClassifications` path is a different
publisher habit and copying `fts.py`'s `cpv_codes` here would silently read a
key this source does not use it for; two of the fourteen releases also carry a
`tender.additionalClassifications` list alongside `tender.classification`
(same scheme, different codes), and it is left unread; `tender.classification`
is confirmed complete for this source; a normaliser that also decides which
extra codes matter is a filtering judgement, not a mapping one (rule 5).

**Three of the fourteen carry `tag: ["tenderAmendment"]` rather than
`["tender"]`.** `monitor/connectors/contractsfinder_gb.py`'s `parse_releases`
already confirms the tag keeps the release in the tender stage before this
module ever sees it; nothing here treats an amendment differently; it maps
like any other tender-stage release, exactly as the connector's own docstring
says it must.

**The notice URL is read from the release's own stated document, via the
connector's own `release_url`, and never rebuilt from the release id.** The
release id embeds an internal numeric suffix the public URL does not carry
(`monitor/connectors/contractsfinder_gb.py`'s own docstring), so the one
function that already knows where the real address lives is called rather than
reimplemented a second time here (rule 1: no second selector for the same
fact).

**Value is carried in GBP exactly as published, including two releases stating
`1`.** Two of the fourteen (PFRU2-2025-738 and PFRU2-2025-764, both Ukraine
donor-funded tenders run through this UK portal) publish `tender.value.amount`
as `1`. That is not the publisher's "not stated" convention checked in
`monitor/normalise/value.py` - it is a *stated*, non-zero figure - so it is
carried, not dropped; rule 9 is that the original text and figures are the
record, not that this module edits what looks implausible.

**Admin level has no field to read here either.** The fourteen buyers range
from a fire and rescue service and NHS trusts to county and borough councils,
several plainly sub-national by name, but nothing on any release states a
level, and guessing "local" from a buyer's own name is exactly the judgement
rule 5 leaves to a stage with actual evidence for it. `national` is the
conservative default `fts.py` gives its own unqualified OCDS buyers, read here
the same way rather than inferred from a name.

**Already English; nothing is filled into `title_en` or `body_en`.** Every
recorded release states `language: "en"`, so filling an English rendering
would claim a translation happened when none did - the same choice `fts.py`
makes for its own English-only publisher.
"""

from __future__ import annotations

from decimal import Decimal

import structlog

from monitor.connectors.contractsfinder_gb import release_url
from monitor.models import Notice
from monitor.normalise.cpv import extract_codes
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "contractsfinder_gb"
COUNTRY = "GB"

# See the module docstring: nothing on a release states a level, and the
# buyers measured span a fire and rescue service, NHS trusts, county and
# borough councils and a Cabinet Office framework alike. National is the
# conservative reading, the same choice fts.py makes for Find a Tender's own
# unqualified buyers.
DEFAULT_ADMIN_LEVEL = "national"


def map_notice(raw: dict) -> MappedNotice:
    """One OCDS release (tender or tenderAmendment tag) to a Notice."""
    tender = raw["tender"]
    external_id = raw["id"]
    title = (tender.get("title") or "").strip()
    if not title:
        raise ValueError(f"{external_id}: release has no tender title")

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
        published_at=parse_published(raw.get("date", ""), source_id=SOURCE_ID, url=url),
        deadline_at=parse_deadline((tender.get("tenderPeriod") or {}).get("endDate", ""), source_id=SOURCE_ID, url=url),
        # The release states its own language; every one recorded is English.
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


def cpv_codes(tender: dict) -> list[str]:
    """The CPV code at `tender.classification`, where the source states one.

    This publisher's CPV lives here and not at `items[].additionalClassifications`
    (Find a Tender's path); see the module docstring for what was checked before
    settling on this one field.
    """
    classification = tender.get("classification") or {}
    if classification.get("scheme") != "CPV" or not classification.get("id"):
        return []
    return extract_codes(str(classification["id"]))


def _published(tender: dict) -> tuple[Decimal | None, str | None]:
    """The stated value as published. Contracts Finder states GBP, carried as is."""
    value = tender.get("value") or {}
    return published_value(value.get("amount"), value.get("currency"), source_id=SOURCE_ID)
