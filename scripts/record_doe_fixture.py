"""Record the Datenservice Öffentlicher Einkauf contract fixture from real calls.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_doe_fixture.py

Makes exactly the requests one run makes for one completed publication day: one
listing request for the day's `csv.zip`, then one `?format=domain` request per
notice the listing selects. Writes `tests/contract/fixtures/doe.json` holding the
two listing files verbatim and every detail record that was fetched, and prints
the shape of what came back.

What probing the live service established on 2026-09-12, which documentation would
not have told us and which is why the connector is shaped the way it is:

  - The export endpoint offers four representations, advertised by the 406 it
    returns for an unknown `format`: csv.zip, ocds.zip, eforms.zip and ocds2.zip.
  - `ocds2.zip`, the one that carries both the submission deadline and the buyer
    legal type, **silently omits 484 of the day's 975 notices**, 333 of them live
    contract notices. The per-notice endpoint answers HTTP 500 for every omitted
    notice tested, so the bulk export is swallowing its own converter's failures
    and answering 200 with half the corpus.
  - `ocds.zip` is complete but carries no submission deadline anywhere. All 150
    JSON paths across its 975 releases were enumerated; there is no `tenderPeriod`.
  - `eforms.zip` is complete and is the published record, but the payload is XML
    and `monitor/fetch.py` stores payloads by `json.loads`.
  - Which leaves the day's metadata from `csv.zip` and the notice itself from
    `?format=domain`, which answered 200 for every notice tested.
  - Every query parameter except the date is ignored in silence: `cpv=`,
    `noticeType=`, `page=` and an invented parameter each returned bytes identical
    to the unfiltered response. The date parameter is real and strict: a malformed
    `pubDay`, an empty one, two of them, or one alongside `pubMonth` all return
    400, and three different days returned disjoint id sets.
  - `noticeVersion` is honoured exactly: `01` and `1` are different notices and a
    version that does not exist is a 404, so the listing's string is passed through.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "doe.json"
SOURCE_YAML = REPO / "sources" / "doe.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.doe import (  # noqa: E402
    CLASSIFICATION_FILE,
    DETAIL_FORMAT,
    DETAIL_URL,
    LISTING_FORMAT,
    NOTICE_FILE,
    check_detail,
    parse_listing,
    read_archive,
    select,
)
from monitor.registry.load import CONFIG_DIR  # noqa: E402

# The day recorded. One completed publication day, which is what one run reads per
# listing request.
PUB_DAY = date(2026, 9, 11)


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    prefixes = yaml.safe_load((CONFIG_DIR / "thresholds.yaml").read_text(encoding="utf-8"))["cpv_pass_prefixes"]

    with httpx.Client(
        timeout=180.0,
        headers={"User-Agent": agent},
        follow_redirects=True,
    ) as client:
        response = client.get(source["api_url"], params={"pubDay": PUB_DAY.isoformat(), "format": LISTING_FORMAT})
        response.raise_for_status()
        files = read_archive(response.content)
        rows = parse_listing(files, PUB_DAY)
        selected = select(
            rows,
            exclude_form_types=source.get("exclude_notice_types") or [],
            cpv_prefixes=prefixes,
        )
        print(f"listing: {len(response.content) / 1024:.0f} KB, {len(rows)} notices, {len(selected)} selected")

        details = []
        for position, row in enumerate(selected, start=1):
            detail = client.get(
                DETAIL_URL.format(notice_id=row.notice_id),
                params={"format": DETAIL_FORMAT, "noticeVersion": row.version},
            )
            detail.raise_for_status()
            details.append(check_detail(detail.json(), row))
            if position % 25 == 0:
                print(f"  fetched {position}/{len(selected)}")

    document = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "pub_day": PUB_DAY.isoformat(),
        "listing": {NOTICE_FILE: files[NOTICE_FILE], CLASSIFICATION_FILE: files[CLASSIFICATION_FILE]},
        "details": details,
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024:.0f} KB)")
    print(f"form types published:  {_counts(row.form_type for row in rows)}")
    print(f"form types selected:   {_counts(row.form_type for row in selected)}")
    print(f"notice types selected: {_counts(row.notice_type for row in selected)}")
    print(f"unclassified selected: {sum(1 for row in selected if not row.cpv_codes)}")
    print(f"eforms versions:       {_counts(detail.get('eformsVersion', '(absent)') for detail in details)}")
    print(f"no declared language:  {sum(1 for d in details if 'noticeOfficialLanguages' not in d)} of {len(details)}")
    no_classification = sum(1 for detail in details if not detail.get("classification"))
    print(f"no classification:     {no_classification} of {len(details)}")
    print(f"buyers per notice:     {_counts(str(len(d.get('buyers') or [])) for d in details)}")
    print(f"lots per notice:       {_counts(str(len(d.get('lots') or [])) for d in details)}")
    return 0


def _counts(values) -> str:
    counted: dict[str, int] = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return ", ".join(f"{key}={count}" for key, count in sorted(counted.items(), key=lambda item: -item[1]))


if __name__ == "__main__":
    raise SystemExit(main())
