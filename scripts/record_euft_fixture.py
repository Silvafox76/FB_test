"""Record the EU Funding and Tenders contract fixture from one real request.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_euft_fixture.py

Writes `tests/contract/fixtures/euft.json` and prints the shape of what came back.
The parser is then written against those fields and not against memory of the
API's documentation, which here would have been wrong in the most expensive way
possible: the search API reads its query only when the query arrives as a
multipart file part, and when it does not it answers HTTP 200 with the entire
4,184,545-document portal index instead of the 35 notices asked for.

What was verified against the live API on 2026-09-12, by probing rather than
reading. The connector's docstring carries the reasoning; these are the
measurements:

  - Encoding. Same URL, same filter, three ways of sending it:
    `files={"query": ("blob", body, "application/json")}` -> 368,489 results;
    `data={"query": ...}` -> 4,184,545; `json=query` -> 4,184,545. Only the first
    is read. The other two are not errors - they are HTTP 200 with the filter
    silently dropped.
  - Query language. `terms` and `range` inside a `bool` work. `prefix`,
    `wildcard` and `query_string` are all HTTP 400, so CPV prefixes (48/72/79)
    cannot be expressed at the query and the CPV stage keeps its job.
  - A `terms` clause on a field the index does not have returns 0 rows, not the
    whole corpus. A misspelled field here reads as a quiet day, which is the
    opposite of the World Bank API's failure and is why the registry's
    expected_items_per_run matters more than usual for this source.
  - `apiKey` and `text` are both mandatory (400 without either). `apiKey=SEDIA`
    names the index rather than an identity: it is echoed back in every
    document's DATASOURCE field, needs no registration, and a wrong value is a
    500, not a 401. `text=***` is match-all; a real term filters on top.
  - One tender is indexed once per portal UI language and the copies are
    identical, titles included. 28 distinct callIdentifier values in the
    2026-09-08 window produced 672 documents; `language: en` returned exactly 28.
  - `DATASOURCE: SEDIA` is the live index. `SEDIA_PRD_CENTRICITY` is a frozen
    archive whose newest record is 2023-05-26 and which still marks tenders
    "Open for submission" three years after they closed.
  - `pageSize` clamps to 100 in silence; `pageNumber` is 1-based.
  - Deadlines. `deadlineDate` publishes the local wall clock of `cftTimezone`
    stamped with a `+0000` offset it does not have. Cross-checked against TED's
    eForms `deadline-receipt-tender-time-lot` for four notices in four zones:
    603937-2026 14:30:59+02:00 (Podgorica), 581930-2026 14:30:59+02:00,
    422869-2026 12:00:59+02:00 (Skopje) and, after its 2026-08-07 corrigendum,
    549932-2026 10:00:59+01:00 (Porto-Novo). The portal's wall clock matches all
    four; its offset matches none of them.

One request, no retry (rule 2), identified user agent (rule 21).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "euft.json"
SOURCE_YAML = REPO / "sources" / "euft.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.euft import (  # noqa: E402
    API_KEY,
    CONTAINER,
    DATASOURCE,
    DOCUMENT_TYPE,
    INDEX_LANGUAGE,
    LIVE_STATUSES,
    MATCH_ALL_TEXT,
    PAGE_SIZE,
    zone_codes,
)


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    query = {
        "bool": {
            "must": [
                {"terms": {"type": [DOCUMENT_TYPE]}},
                {"terms": {"DATASOURCE": [DATASOURCE]}},
                {"terms": {"language": [INDEX_LANGUAGE]}},
                {"terms": {"status": list(LIVE_STATUSES)}},
                {"terms": {"geographicalZones": zone_codes(source["covers"])}},
            ]
        }
    }

    response = httpx.post(
        source["api_url"],
        params={
            "apiKey": API_KEY,
            "text": MATCH_ALL_TEXT,
            "pageSize": PAGE_SIZE,
            "pageNumber": 1,
        },
        # The only encoding this API reads. See the module docstring.
        files={"query": ("blob", json.dumps(query, ensure_ascii=False).encode("utf-8"), "application/json")},
        headers={"User-Agent": agent},
        timeout=120.0,
        follow_redirects=True,
    )
    response.raise_for_status()
    document = response.json()

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    results = document.get(CONTAINER) or []
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024:.0f} KB)")
    print(f"top-level keys: {sorted(document)}")
    print(f"total matching: {document.get('totalResults')}, returned: {len(results)}")
    if not results:
        print("NOTHING CAME BACK. On this API that is what a misspelled field looks like, not a quiet day.")
        return 1

    metadata = [result["metadata"] for result in results]
    print(f"types:        {sorted({tuple(m.get('type', [])) for m in metadata})}")
    print(f"datasources:  {sorted({tuple(m.get('DATASOURCE', [])) for m in metadata})}")
    print(f"statuses:     {sorted({tuple(m.get('status', [])) for m in metadata})}")
    print(f"with a deadline:      {sum(1 for m in metadata if m.get('deadlineDate'))} of {len(metadata)}")
    print(f"prior information:    {sum(1 for m in metadata if m.get('callIdentifier', [''])[0].endswith('-PIN'))}")
    print(f"timezones:    {sorted({tuple(m.get('cftTimezone', [])) for m in metadata})}")
    print(f"fields on the first notice: {sorted(metadata[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
