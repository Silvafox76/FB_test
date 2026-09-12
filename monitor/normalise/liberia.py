"""One Liberia ePPS OCDS release -> Notice. Source-specific mapping and nothing else.

Written against `tests/contract/fixtures/liberia.json`, recorded 2026-09-12, and the
14 compiled releases in it. `monitor/connectors/liberia.py` yields a payload of
`{"listing": <search row>, "detail": <OCDS release package>}`, so the release this
reads is `detail.releases[0]` and the listing row is what the connector already used
to decide the notice was in window.

Four judgements, all measured on those 14 releases rather than assumed.

**The description is dropped when it only repeats the title, which is 11 times in
14.** Liberia's ePPS fills `tender.description` with the `tender.title` string on
most releases. Carrying it into `Notice.body` anyway would send the scorer the same
sentence twice and pay for the tokens, so the body is set only when it says
something the title does not. The three that differ keep theirs.

**The value is carried as published.** All 14 releases carry `tender.value` as
`{"amount": ..., "currency": "USD"}`, so Liberia was for a while the only source whose
value survived at all: decision 7 dropped any non-USD amount rather than convert it,
because converting needed a rate and a date the pipeline did not have. Migration 012
gives it both, so every source now carries its own currency and the conversion is a
stamped step at staging. Liberia is no longer special; it is just the source that
happens to publish in the target currency.

**Classifications are ISIC, not CPV, so `cpv_codes` stays empty.** All 18
classifications across the fixture use the ISIC scheme. Mapping ISIC to CPV would be
a second classification path with a lossy table behind it (rule 1), and the free
filter already handles a notice with no CPV code by falling through to the lexicon.
That is the honest outcome: this source is filtered on words, not on codes.

**Everything is national.** The buyers are ministries, commissions, authorities and
state corporations - Ministry of Internal Affairs, General Auditing Commission,
Liberia Revenue Authority, Liberia Electricity Corporation. Nothing in the release
distinguishes a county administration, and `sources/liberia.yaml` declares national.

The deadline is `tender.tenderPeriod.endDate`, which is ISO 8601 with a zone on all
14, so `monitor/normalise/dates.py` parses it without this module reshaping anything
(contrast `sierra_leone.py`, whose source publishes an ambiguous day-month form).
"""

from __future__ import annotations

import structlog

from monitor.models import Notice
from monitor.normalise.dates import parse_deadline, parse_published
from monitor.normalise.hashing import content_hash
from monitor.normalise.mapped import MappedNotice
from monitor.normalise.value import published_value

log = structlog.get_logger(__name__)

SOURCE_ID = "liberia"
COUNTRY = "LR"
LANGUAGE = "en"

# The release carries `"language": "en"` on all 14, so this is read rather than
# detected, and the confidence says so.
LANGUAGE_CONFIDENCE = 1.0

# See the module docstring: every buyer in the fixture is a central government body.
ADMIN_LEVEL = "national"


def _release(raw: dict) -> dict:
    """The OCDS release inside the connector's payload, or a loud failure.

    The connector pairs a search row with a release package. An empty `releases`
    list means the package arrived without the thing it exists to carry, which is a
    change at the source and not an empty day (rule 4).
    """
    detail = raw.get("detail") or {}
    releases = detail.get("releases") or []
    if not releases:
        listing = raw.get("listing") or {}
        raise ValueError(f"liberia release package for {listing.get('id', 'unknown')} carries no releases")
    return releases[0]


def map_notice(raw: dict) -> MappedNotice:
    """One OCDS release to a Notice."""
    release = _release(raw)
    tender = release.get("tender") or {}

    title = (tender.get("title") or "").strip()
    if not title:
        raise ValueError(f"liberia release {release.get('ocid', 'unknown')} has no tender title")

    # Only when it is not the title again. See the module docstring.
    description = (tender.get("description") or "").strip()
    body = "" if description == title else description

    buyer = ((release.get("buyer") or {}).get("name") or "").strip()
    url = (raw.get("listing") or {}).get("url") or ""

    # The zero-is-not-stated rule started here - three of the 14 releases carry `0`
    # for "Construction of Two District Offices", "FY2026 Procurement of Transport
    # Equipment" and "Procurement of Office Equipment", none of which costs nothing -
    # and it now lives in `monitor/normalise/value.py` because TED turned out to
    # publish 19 zeros of its own. Liberia's USD is no longer a special case either:
    # it is carried as the published currency like every other source's, and it
    # converts through the identity rate at staging.
    value = tender.get("value") or {}
    estimated_value, value_currency = published_value(value.get("amount"), value.get("currency"), source_id=SOURCE_ID)

    deadline_raw = (tender.get("tenderPeriod") or {}).get("endDate") or ""

    notice = Notice(
        content_hash=content_hash(title, body),
        source_id=SOURCE_ID,
        external_id=str(release.get("ocid") or tender.get("id") or ""),
        url=url,
        title=title,
        buyer=buyer,
        country=COUNTRY,
        admin_level=ADMIN_LEVEL,
        published_at=parse_published(release.get("date") or "", source_id=SOURCE_ID, url=url),
        deadline_at=parse_deadline(deadline_raw, source_id=SOURCE_ID, url=url),
        language=LANGUAGE,
        language_confidence=LANGUAGE_CONFIDENCE,
        estimated_value=estimated_value,
        value_currency=value_currency,
        body=body,
    )
    # Already English; an English rendering would be a translation of nothing.
    return MappedNotice(notice=notice)
