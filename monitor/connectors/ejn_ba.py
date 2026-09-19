"""EJN e-Nabavke BiH Open Data API, `open.ejn.gov.ba`, OData v4.

Recorded from real calls on 2026-09-19. The fixture is
`tests/contract/fixtures/ejn_ba.json.gz` and this parser is written against the
fields that are in it. See `sources/ejn_ba.yaml` for the access-route
investigation (three hosts probed, robots.txt and terms-of-use search, the
anonymous-read confirmation) that this connector does not repeat.

**The window.** `ProcurementNotices` has no notice-type field to exclude by (the
registry entry explains why: award, termination, prior-information and annual
notices each live in their own entity set, so this one is already scoped to open
calls). The only server-side narrowing available is the publication window,
`Announced ge <since>`, verified live: `$filter=Announced ge 2026-09-12T00:00:00Z
and Announced lt 2026-09-19T00:00:00Z&$count=true` returned exactly 672, matching
the registry's own seven-day measurement. `sources/ejn_ba.yaml`'s VOLUME note is
explicit that a window shorter than a week reads a legitimate zero on the
platform's quiet weekend days, so `LOOKBACK_DAYS` is seven, not one, even though
the schedule is daily; re-reading six days of already-seen notices costs nothing
downstream, because deduplication is by content hash, not by this connector
remembering what it saw yesterday (the same reasoning `monitor/connectors/
liberia.py` gives for its own seven-day window).

**Paging is server-capped below what it advertises.** `$top` accepts up to 1000
(`$top=5000` is refused with a 400 naming the limit) but a request for `$top=1000`
against the 672-row window still came back only 50 rows, with an `@odata.nextLink`
carrying the next `$skip`/`$top` - verified live and true of every entity set
probed here (`ProcurementNotices`, `Lots`, `LotCpvCodeLinks`, `CpvCodes`), not
just the notice listing. `collect_pages` follows that link rather than
reconstructing `$skip` by hand, since the link already carries the original
`$filter` unmodified.

**CPV is a three-hop join, and it is batched, not per-notice.** The notice DTO
carries no CPV field (`sources/ejn_ba.yaml`'s own finding); the code lives behind
`Lots -> LotCpvCodeLinks -> CpvCodes`, keyed on `ProcedureId`, `LotId` and
`CpvCodeId` respectively. Doing this per notice would be three requests times 672
notices for one week's window - roughly two thousand requests, well past "one
polite pass" (rule 21). OData v4's `in (...)` filter batches it instead: verified
live, `Lots?$filter=ProcedureId in (id1,id2,...)` resolves however many ids are
listed in one request (subject to the same page-size cap above, followed the same
way). The one hazard measured batching into: **a filter value list long enough
pushes the URL past a server-side length limit that fails as a bare 404, not a
400 naming the limit** - `$filter=ProcedureId in (...)` with 300 seven-digit ids
(a 2,453-character URL) 404s, while 200 ids (1,653 characters) is fine.
`JOIN_BATCH_SIZE` is 100, comfortably under that measured boundary for ids of any
width this API uses. One full week's window (672 notices, 1,101 lots, 1,489
links, 459 distinct CPV codes, measured 2026-09-19) resolves in 62 requests total
across all four entity sets, against roughly 2,016 if the join were done per
notice - the number this module's design exists to avoid, and it is said here
rather than left to be rediscovered.

**Every procedure has at least one Lot, whether or not `HasLots` is true.** A
`HasLots: false` procedure (a single-lot award with no formal lot split) still has
exactly one `Lots` row for its `ProcedureId`, verified live on procedure 2769495 -
which is also where the per-lot `EstimatedValue` and `ShortDescription` live; the
notice DTO itself has neither. So the join in this connector runs against every
notice's `ProcedureId` unconditionally, not gated on `HasLots`.

**Every lot in the recorded window resolved to at least one CPV code** (1,101 of
1,101), so nothing here treats a notice's CPV list as optional the way
`monitor/connectors/fts.py` does for Find a Tender's four CPV-less releases out of
twenty-five. That is a property of this one recorded window, not a documented
guarantee of the API, so a notice arriving with no CPV code is carried as an empty
list rather than raised on - the same posture `fts.py` takes - while a
`ProcedureId` with no `Lots` row at all raises, because that break would mean the
join no longer holds at all, which is the structural invariant this module relies
on rather than an occasional empty field.

**Contact fields are personal, not institutional, and this connector does not
touch them** (rule 5: what becomes `Notice.body` is the normaliser's decision, and
rule 9 makes the record what was published). Every one of the 672 notices in the
recorded window names at least one contact person
(`AdditionalInformationContactPersonName`, `DocumentationTakeOverContactPerson`,
`OfferDeliveryContactPersonName`, and `ForeignContactPerson` where the notice has
a foreign-language rendering), each paired with its own email/phone/fax; 505
distinct named individuals and a wide mix of shared-mailbox and personal-looking
addresses (`nabavke.dzsrebrenik@gmail.com` alongside `marina.toroman@gmail.com`,
`srdjan.cegar@gmail.com`), the same hazard `monitor/connectors/liberia.py` found
in OCDS `contactPoint`. `tests/contract/fixtures/ejn_ba.json.gz` redacts every one of
these fields before commit, the same way and for the same rule-19/rule-20 reason
`tests/contract/test_liberia.py` documents for its own fixture; see that file's
docstring pattern, mirrored in `tests/contract/test_ejn_ba.py`.

**Language is not a field.** `ietfTag` is a request-time display parameter for the
API's own enum-label localisation, not a stored property of a notice (registry
entry). 180 of the 672 recorded notices carry Cyrillic script in
`ProcedureName` or `ContractingAuthorityName`; the rest are Latin script. Neither
is read or guessed at here - `Notice.language` is the normaliser's field, detected
from the text, not this connector's.

**Administrative unit is a closed six-value vocabulary and the recorded window
hit all six**: `Entity` 237, `Municipality` 171, `Canton` 140, `City` 65,
`Country` 42, `District` 17. `ADMIN_UNIT_TYPES` below is that vocabulary; an
unrecognised value raises rather than being carried through blind, since a seventh
value would mean the enum changed under this connector.

There is no per-notice public page verified for this source (`next.ejn.gov.ba` is
an Angular SPA whose catch-all serves the same shell for every path, so a 200 on a
guessed route proves nothing - `sources/ejn_ba.yaml` records the same finding for
`/` and `/robots.txt`). `RawNotice.url` is instead the OData query that answers
with this one notice and nothing else
(`https://open.ejn.gov.ba/ProcurementNotices?$filter=Id eq <id>`), the same
reasoning `monitor/connectors/liberia.py` gives for using its download endpoint as
the notice URL rather than a front-end page nobody has confirmed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from monitor.connectors.base import FeedConnector
from monitor.models import RawNotice

log = structlog.get_logger(__name__)

# See VOLUME in sources/ejn_ba.yaml: a window shorter than a week reads a
# legitimate zero on the platform's own quiet weekend days.
LOOKBACK_DAYS = 7

# The server's advertised ceiling (`$top=5000` is refused naming this number).
# The effective page size returned is smaller and server-controlled (measured
# 50); `collect_pages` follows `@odata.nextLink` rather than assuming either
# number, so this is the value requested, not the value relied on.
REQUESTED_TOP = 1000

# Headroom above the registry's own ceiling (`expected_max` 1200) at the measured
# effective page size of 50: 1200 / 50 = 24 pages for the listing alone. Also the
# per-batch cap on a single `in (...)` join hop, which needs far fewer.
MAX_PAGES = 40

# Measured live 2026-09-19: an `in (...)` filter with 200 seven-digit ids (a
# 1,653-character URL) succeeds; 300 ids (2,453 characters) 404s with no message
# naming a limit, just IIS's bare "file or directory not found". 100 leaves a
# comfortable margin under that boundary for ids of any width this API uses.
JOIN_BATCH_SIZE = 100

# Every field this connector's own logic reads: the join key (`ProcedureId`), the
# fields the parser cross-checks (`Id`, `Announced` for the sort assertion), and
# the fields named in this module's docstring as measured present on all 672
# recorded notices. A response missing one of these is a changed API (rule 4).
REQUIRED_NOTICE_FIELDS = (
    "Id",
    "ProcedureId",
    "ProcedureName",
    "ContractingAuthorityName",
    "ContractingAuthorityAdministrativeUnitType",
    "ProcedureType",
    "Announced",
    "ApplicationDeadlineDateTime",
)

# The 6-value enum `sources/ejn_ba.yaml` names for
# `ContractingAuthorityAdministrativeUnitType`. All six appeared in the recorded
# window (see the module docstring); an unrecognised value raises.
ADMIN_UNIT_TYPES = frozenset({"Municipality", "City", "Canton", "District", "Entity", "Country"})

# The join keys this connector reads off each hop. `EstimatedValue` and
# `ShortDescription` on a Lot are read by the normaliser, not checked here: they
# were null on 1 and 127 of the 1,101 recorded lots respectively, so they are not
# treated as always present the way the join keys are.
REQUIRED_LOT_FIELDS = ("Id", "ProcedureId")
REQUIRED_LINK_FIELDS = ("Id", "LotId", "CpvCodeId")
REQUIRED_CPV_CODE_FIELDS = ("Id", "Code", "Description")


class EjnBaConnector(FeedConnector):
    """One publication-date window, paged, each notice joined to its CPV codes."""

    def __init__(self, source, cpv_prefixes: list[str]) -> None:
        super().__init__(source)
        # Accepted and unused: `ProcurementNotices` has no CPV query parameter
        # (see the module docstring), and the CPV codes that do exist are only
        # knowable after the join below, which already requires reading every
        # notice in the window - there is nothing left for a CPV prefix to narrow
        # at the source. Taken so every connector is built the same shape, the
        # same posture `monitor/connectors/liberia.py` documents for its own
        # ISIC-only corpus.
        self.cpv_prefixes = cpv_prefixes

    def window(self, now: datetime | None = None) -> datetime:
        """The oldest `Announced` timestamp this run asks for."""
        return (now or datetime.now(UTC)) - timedelta(days=LOOKBACK_DAYS)

    def entity_url(self, entity: str) -> str:
        """A sibling entity set on the same OData root as `source.api_url`."""
        root = self.source.api_url.rsplit("/", 1)[0]
        return f"{root}/{entity}"

    def fetch_raw(
        self, client: httpx.Client, *, since: datetime | None = None, until: datetime | None = None
    ) -> list[RawNotice]:
        """Every notice in the window, ordered newest first, each with its CPV codes.

        `since` and `until` name an explicit publication window and exist for a
        future backfill script, the same shape and the same reasoning
        `monitor/connectors/ted.py` gives for its own `since`/`until`: the
        scheduled pass never sets them and reads the trailing `LOOKBACK_DAYS`.
        """
        start = since or self.window()
        odata_filter = f"Announced ge {odata_datetime(start)}"
        if until is not None:
            odata_filter += f" and Announced lt {odata_datetime(until)}"

        notices = collect_pages(
            client,
            self.source.api_url,
            {"$filter": odata_filter, "$orderby": "Announced desc", "$top": REQUESTED_TOP},
        )
        validate_notices(notices)

        procedure_ids = sorted({notice["ProcedureId"] for notice in notices})
        lots = self._fetch_joined(client, "Lots", "ProcedureId", procedure_ids, REQUIRED_LOT_FIELDS)

        lot_ids = sorted({lot["Id"] for lot in lots})
        links = self._fetch_joined(client, "LotCpvCodeLinks", "LotId", lot_ids, REQUIRED_LINK_FIELDS)

        cpv_code_ids = sorted({link["CpvCodeId"] for link in links})
        codes = self._fetch_joined(client, "CpvCodes", "Id", cpv_code_ids, REQUIRED_CPV_CODE_FIELDS)

        raw_notices = [
            self.raw_notice(
                url=notice_url(self.source.api_url, notice),
                payload=json.dumps(build_payload(notice, lots, links, codes), ensure_ascii=False, sort_keys=True),
                mime="application/json",
            )
            for notice in notices
        ]

        log.info(
            "ejn_ba_fetch",
            notices=len(raw_notices),
            lots=len(lots),
            links=len(links),
            cpv_codes=len(codes),
            since=start.isoformat(),
        )
        return raw_notices

    def _fetch_joined(
        self, client: httpx.Client, entity: str, field: str, ids: list[int], required_fields: tuple[str, ...]
    ) -> list[dict]:
        """One join hop, batched with `in (...)` per `JOIN_BATCH_SIZE` ids."""
        rows: list[dict] = []
        for chunk in batched(ids, JOIN_BATCH_SIZE):
            id_list = ",".join(str(one_id) for one_id in chunk)
            url = f"{self.entity_url(entity)}?$filter={field} in ({id_list})"
            rows.extend(collect_pages(client, url, None))
        validate_rows(entity, rows, required_fields)
        return rows


def batched(values: list[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def odata_datetime(moment: datetime) -> str:
    """Seconds precision, `Z` suffix, no fractional seconds - verified live."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_pages(client: httpx.Client, url: str, params: dict | None) -> list[dict]:
    """Follow `@odata.nextLink` until the server has nothing more to add.

    The server enforces its own page size (measured 50, on every entity set
    probed here) regardless of a larger `$top`, and `@odata.nextLink` already
    carries the next request's `$skip`/`$top` and the original `$filter`
    unmodified - simpler and more robust than reconstructing `$skip` by hand.
    """
    rows: list[dict] = []
    next_url, next_params = url, params
    for _ in range(MAX_PAGES):
        response = client.get(next_url, params=next_params)
        response.raise_for_status()
        document = response.json()
        if "value" not in document:
            raise ValueError(f"{next_url} response has no 'value' key; got {sorted(document)}")
        rows.extend(document["value"])
        next_url = document.get("@odata.nextLink")
        next_params = None
        if not next_url:
            return rows
    raise ValueError(f"{url} did not finish paging within {MAX_PAGES} requests")


def validate_notices(notices: list[dict]) -> None:
    """Required fields, and that the sort this connector asked for actually held.

    The window's completeness comes from the server-side `$filter`, not from this
    sort - unlike `monitor/connectors/liberia.py`'s client-side cut, nothing here
    stops reading early because a row looked old. The check is a defensive one
    against a silently ignored `$orderby`, the same hazard
    `monitor/connectors/liberia.py` documents for its own sort parameter.
    """
    for position, notice in enumerate(notices):
        missing = [field for field in REQUIRED_NOTICE_FIELDS if field not in notice]
        if missing:
            raise ValueError(f"EJN BA notice {position} (Id={notice.get('Id', '?')}) is missing {missing}")
        admin_unit = notice["ContractingAuthorityAdministrativeUnitType"]
        if admin_unit not in ADMIN_UNIT_TYPES:
            raise ValueError(
                f"EJN BA notice {notice['Id']} has ContractingAuthorityAdministrativeUnitType "
                f"{admin_unit!r}, not one of {sorted(ADMIN_UNIT_TYPES)}"
            )

    # Parsed, not compared as raw strings: `Announced` carries a variable number of
    # fractional-second digits (0 to 3 measured across the recorded window), so a
    # shorter string can sort after a longer one lexicographically even though its
    # timestamp is earlier - measured live, not a hypothetical.
    announced = [announced_at(notice) for notice in notices]
    for position, (newer, older) in enumerate(zip(announced, announced[1:], strict=False)):
        if older > newer:
            raise ValueError(
                f"EJN BA notices are not sorted by Announced descending: row {position + 1} ({older}) is "
                f"newer than row {position} ({newer}); $orderby may have been ignored"
            )


def announced_at(notice: dict) -> datetime:
    return datetime.fromisoformat(notice["Announced"].replace("Z", "+00:00"))


def validate_rows(entity: str, rows: list[dict], required_fields: tuple[str, ...]) -> None:
    for position, row in enumerate(rows):
        missing = [field for field in required_fields if field not in row]
        if missing:
            raise ValueError(f"EJN BA {entity} row {position} (Id={row.get('Id', '?')}) is missing {missing}")


def build_payload(notice: dict, lots: list[dict], links: list[dict], codes: list[dict]) -> dict:
    """This one notice, plus the slice of each join hop that resolves its CPV codes.

    Every sub-object is the raw entity the API returned; nothing here is merged
    into a new field or derived - `lot_cpv_links` and `cpv_codes` are handed
    through exactly as fetched so the normaliser does the join, not this
    connector (rule 5).

    Raises when a notice's `ProcedureId` has no `Lots` row at all: every procedure
    in the recorded window has at least one (see the module docstring), so a
    `ProcedureId` with none is the structural invariant breaking, not an empty
    field. An empty CPV list, by contrast, is carried through rather than raised
    on, because CPV-per-lot coverage is a measured property of one window, not a
    documented guarantee (see the module docstring, and `monitor/connectors/
    fts.py` for the same posture on its own CPV-less releases).
    """
    notice_lots = [lot for lot in lots if lot["ProcedureId"] == notice["ProcedureId"]]
    if not notice_lots:
        raise ValueError(f"EJN BA notice {notice['Id']} has no Lots row for ProcedureId {notice['ProcedureId']}")

    lot_ids = {lot["Id"] for lot in notice_lots}
    notice_links = [link for link in links if link["LotId"] in lot_ids]

    codes_by_id = {code["Id"]: code for code in codes}
    notice_codes = []
    seen_code_ids: set[int] = set()
    for link in notice_links:
        code = codes_by_id.get(link["CpvCodeId"])
        if code is None:
            raise ValueError(f"EJN BA notice {notice['Id']} link {link['Id']} names an unresolved CpvCodeId")
        if code["Id"] not in seen_code_ids:
            seen_code_ids.add(code["Id"])
            notice_codes.append(code)

    return {
        "notice": notice,
        "lots": notice_lots,
        "lot_cpv_links": notice_links,
        "cpv_codes": sorted(notice_codes, key=lambda c: c["Id"]),
    }


def notice_url(api_url: str, notice: dict) -> str:
    """The one OData query that answers with this notice and nothing else."""
    return f"{api_url}?$filter=Id eq {notice['Id']}"
