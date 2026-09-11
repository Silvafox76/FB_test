"""Record the TED contract fixture from one real request.

BUILD_ORDER step 4 makes this the first action of the session: fetch one real
response, save it, then write the parser against the fields that are actually
present. Not against documentation memory, and not against this script's guess at
the query.

Run it anywhere with outbound access to api.ted.europa.eu:

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_ted_fixture.py

It writes tests/contract/fixtures/ted.json and prints the shape of what came
back: the top-level keys, the count, and the field names on the first notice.
Those field names are what the parser is then written against.

The request below expresses what step 4 asks for - CPV in the pass prefixes,
published within the last two days, English fields where the API supports them -
but the expert-query syntax and the field list are **unverified**: nobody has run
this against the live API yet. If it returns an error or an empty set, that is
information, not a failure of the pipeline: adjust QUERY or FIELDS here, run it
again, and record what worked in the comment at the top of monitor/connectors/ted.py.
One polite request per run, identified user agent, no retry (rules 1, 2, 21).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "ted.json"
SOURCE_YAML = REPO / "sources" / "ted.yaml"
THRESHOLDS = REPO / "config" / "thresholds.yaml"

LOOKBACK_DAYS = 2
PAGE_SIZE = 250  # the API's maximum, verified 2026-09-12


def build_query(pass_prefixes: list[str], exclude_notice_types: list[str]) -> str:
    """The connector's own query, so the fixture is what the connector fetches.

    Expert-query syntax verified against the live API on 2026-09-11 and 2026-09-12:
    dates are yyyymmdd with no separators, `field=value*` is the prefix form, and
    the values inside `IN (...)` are space separated.
    """
    since = (date.today() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    cpv = " OR ".join(f"classification-cpv={prefix}*" for prefix in pass_prefixes)
    query = f"({cpv}) AND publication-date>={since}"
    if exclude_notice_types:
        query += f" AND NOT (notice-type IN ({' '.join(exclude_notice_types)}))"
    return query


# Verified against the live API on 2026-09-11. `fields` is mandatory and every
# name is validated server side: TED v3 uses eForms business-term names, and a
# name that is not one of the 1,830 supported values fails the whole request with
# a 400 listing all of them. These are the ones the normaliser reads; the suffix
# says which level of the notice the value sits at (-proc is the procedure,
# -lot is a lot).
FIELDS = [
    "publication-number",  # external_id
    "notice-title",  # title
    "description-proc",  # body
    "buyer-name",  # buyer
    "buyer-country",  # country
    "buyer-legal-type",  # admin_level, national unless it says otherwise
    "classification-cpv",  # cpv_codes
    "publication-date",  # published_at
    "deadline-receipt-tender-date-lot",  # deadline_at, per lot
    "estimated-value-proc",  # estimated_value_usd, with its currency below
    "estimated-value-cur-proc",
    "official-language",  # language
    "notice-type",
    "links",  # url
]


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)", file=sys.stderr)
        return 2

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    prefixes = yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))["cpv_pass_prefixes"]
    url = source["api_url"]
    body = {
        "query": build_query(prefixes, source.get("exclude_notice_types") or []),
        "fields": FIELDS,
        "limit": PAGE_SIZE,
        "page": 1,
    }

    print(f"POST {url}")
    print(f"  query: {body['query']}")

    response = httpx.post(
        url,
        json=body,
        timeout=30.0,
        headers={"User-Agent": agent, "Accept": "application/json"},
    )
    print(f"  status: {response.status_code}")

    if response.status_code != 200:
        print(f"  body: {response.text[:2000]}", file=sys.stderr)
        print("\nAdjust QUERY or FIELDS in this script against the error above and run it again.", file=sys.stderr)
        return 1

    document = response.json()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    print(f"\nwrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size} bytes)")
    print(f"  top-level keys: {sorted(document)}")
    for key, value in document.items():
        if isinstance(value, list) and value:
            print(f"  {key}: {len(value)} items")
            if isinstance(value[0], dict):
                print(f"  first item's fields: {sorted(value[0])}")
            break
    print(f"\nRecorded {date.today().isoformat()}. Put the query and this date in monitor/connectors/ted.py,")
    print("then write the parser against the field names printed above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
