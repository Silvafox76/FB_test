"""Record the World Bank pipeline contract fixture from one real request.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_worldbank_pipeline_fixture.py

Writes `tests/contract/fixtures/worldbank_pipeline.json` and prints the shape of
what came back. The parser is then written against those fields rather than against
memory of the API's documentation, which here would have been wrong twice over.

This is the projects API (`api/v3/projects`), not the procurement notices API
(`api/v2/procnotices`) that `scripts/record_worldbank_fixture.py` records. A
pipeline entry has no tender and no bid deadline; it is the signal that procurement
is coming.

What was verified against the live API on 2026-09-12, by probing rather than by
reading:

  - **Only the `_exact` parameters filter.** `countrycode=GH` and `status=Pipeline`
    are accepted with a 200 and return the whole 28,113-row corpus; `banana=split`
    does the same. `countrycode_exact=GH` returns 340 and `status_exact=Pipeline`
    returns 317. Both `countrycode` and `status` are real fields on every returned
    row, so the ignored spelling is the one that looks written against the data.
    `countrycode_exact=GH&status=Pipeline` returns 340 Ghanaian projects of every
    status, which is the shape of failure this connector checks for: correctly
    scoped on one axis and silently unscoped on the other.
  - `status_exact` is case sensitive - `pipeline` and `PIPELINE` both return 0 -
    and an unknown value returns 0 rather than the corpus, so a typo there fails as
    zero yield rather than as a flood.
  - **`fl=*` is required.** Without it a row carries 15 fields and none of `status`,
    `countrycode`, `pdo` or `project_abstract`. An `fl` naming a subset is not
    honoured: asking for five fields returns `project_abstract` as well.
  - `rows` caps at 1,000 (`rows=2000` returns 1,000); `^` is the OR separator and
    `,` and `|` return nothing; `os` is the offset and pages cleanly.
  - `api/v2/projects` is a 404. `apilang=fr` and `apilang=es` are ignored and
    return the English edition; a project row carries no language field.
  - The container is an object keyed by project id, not the array the notices API
    returns.
  - The whole covered-country pipeline set is 42 rows, so the connector makes one
    request and reads all of it rather than walking a date window.
  - One HTTP 500 with an empty body was seen during rapid probing; five consecutive
    requests of the production shape afterwards returned byte-identical 200s. Under
    rule 2 a 500 raises and marks the source unhealthy, which is the intended
    behaviour and not something to retry around.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "worldbank_pipeline.json"
SOURCE_YAML = REPO / "sources" / "worldbank_pipeline.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.worldbank_pipeline import (  # noqa: E402
    PIPELINE_STATUS,
    ROWS,
    country_query,
)


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    params = {
        "format": "json",
        "rows": ROWS,
        "fl": "*",
        "countrycode_exact": country_query(source["covers"]),
        "status_exact": PIPELINE_STATUS,
    }

    response = httpx.get(
        source["api_url"],
        params=params,
        headers={"User-Agent": agent},
        timeout=180.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    document = response.json()

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    projects = list((document.get("projects") or {}).values())
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024:.0f} KB)")
    print(f"top-level keys:   {sorted(document)}")
    print(f"container type:   {type(document.get('projects')).__name__}")
    print(f"total matching:   {document.get('total')}, returned: {len(projects)}")
    if not projects:
        print("no projects came back; a source that normally yields and yields nothing is a failure state")
        return 1

    print(f"statuses:         {sorted({p.get('status') for p in projects})}")
    print(f"stages:           {Counter(p.get('last_stage_reached_name') for p in projects).most_common()}")
    print(f"countries:        {len({tuple(p.get('countrycode') or []) for p in projects})} distinct")
    print(f"countrycode len:  {Counter(len(p.get('countrycode') or []) for p in projects).most_common()}")
    disclosure = sorted(p.get("public_disclosure_date") or "" for p in projects)
    print(f"disclosure range: {disclosure[0]} .. {disclosure[-1]}")
    print(f"with impagency:   {sum(1 for p in projects if (p.get('impagency') or '').strip())}")
    print(f"with abstract:    {sum(1 for p in projects if (p.get('project_abstract') or '').strip())}")
    print(f"fields on the first project: {sorted(projects[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
