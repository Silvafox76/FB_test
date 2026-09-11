"""Record the World Bank contract fixture from one real request.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_worldbank_fixture.py

Writes `tests/contract/fixtures/worldbank.json` and prints the shape of what came
back. The parser is then written against those fields and not against memory of the
API's documentation, which in this case would have been wrong in a way that matters:
`submission_date` is the date the notice reached the Bank, and the bid deadline is
`submission_deadline_date` plus `submission_deadline_time`. A connector built on the
obvious-looking field would have given every reviewer a deadline already in the past.

What was verified against the live API on 2026-09-11, by probing rather than reading:

  - `project_ctry_name` and `notice_type` filter exactly, and `^` is the OR
    separator between values. `|` and `,` return nothing rather than erroring.
  - There is **no server-side date filter**. `strdate`, `enddate`, `fromdate`,
    `noticedate_from` and friends are all silently ignored and return the unfiltered
    418,561 rows; `noticedate=10-Sep-2026` is an HTTP 400. The only date control is
    `srt=noticedate&order=desc` and a client-side cut, which is why the connector
    reads a sorted page and stops at the first row outside its window.
  - That sort is strictly monotonic: checked across a full 1,000-row response, every
    row is on or before the date of the row above it.
  - `rows` is capped at 1,000; `os` is the offset.
  - Unknown parameters are ignored in silence rather than refused, which is why the
    connector asserts that what came back actually matches what it asked for.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "worldbank.json"
SOURCE_YAML = REPO / "sources" / "worldbank.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.worldbank import (  # noqa: E402
    NOTICE_TYPES,
    PAGE_SIZE,
    country_query,
    type_query,
)


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    params = {
        "format": "json",
        "rows": PAGE_SIZE,
        "os": 0,
        "srt": "noticedate",
        "order": "desc",
        "project_ctry_name": country_query(source["covers"]),
        "notice_type": type_query(),
    }

    response = httpx.get(
        source["api_url"],
        params=params,
        headers={"User-Agent": agent},
        timeout=120.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    document = response.json()

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    notices = document.get("procnotices") or []
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024:.0f} KB)")
    print(f"top-level keys: {sorted(document)}")
    print(f"total matching: {document.get('total')}, returned: {len(notices)}")
    if notices:
        print(f"notice types: {sorted({n.get('notice_type') for n in notices})}")
        print(f"languages:    {sorted({n.get('notice_lang_name') for n in notices})}")
        print(f"countries:    {len({n.get('project_ctry_name') for n in notices})} distinct")
        print(f"date range:   {notices[-1].get('noticedate')} .. {notices[0].get('noticedate')}")
        print(f"fields on the first notice: {sorted(notices[0])}")
        print(f"notice types asked for:     {sorted(NOTICE_TYPES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
