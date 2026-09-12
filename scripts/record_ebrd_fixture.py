"""Record the EBRD contract fixture from one real pass over ECEPP.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_ebrd_fixture.py

Writes `tests/contract/fixtures/ebrd.html` and `tests/contract/fixtures/ebrd.json`
and prints the shape of what came back. One listing request plus one detail request
per in-scope notice, which is the same pass `monitor fetch ebrd` makes: this script
does not read more than the connector would, and it applies the connector's own
scope so the fixture is a run rather than a selection.

Both files hold responses exactly as they arrived and nothing derived - the listing
in `ebrd.html`, the notice pages in `ebrd.json` - so the contract test runs the real
parsers over the real bytes. The listing is 3.8 MB because it cannot be anything
smaller: the portal has no paging and no limit parameter, and every request returns
the whole 4,050-row archive. Trimming it would make it a document rather than a
recording.

The JSON also holds ONE PAGE FROM OUTSIDE THE WINDOW, under its own key and
labelled as such: the newest General Procurement Notice in a covered country. A GPN
carries a different and much smaller field set - no exercise name, no description,
no procurement method, no issue date, no closing date - because it announces a
project's whole procurement programme before any contract is tendered, and GPNs are
715 of the 4,050 archived notices. None fell inside the recorded 30-day window, so
without this page the parser's behaviour on 18% of what this source publishes would
be untested. It is one extra request, it is a real response, and it is kept apart
from `details_html` so nothing can mistake it for part of the run.

What was verified against the live site on 2026-09-12, by probing rather than
reading, and none of it is in any documentation:

  - **Not one query parameter exists.** The search form's inputs carry `id` and no
    `name`, and DataTables filters in the browser. `noticeType`, `country`,
    `keyword`, `currentState` and `pageSize` all return the same 4,050 rows, and so
    does a POST to the form's own action, `noticeSearch.html`.
  - **The rows are not sorted.** 1,107 pairs are out of order on the minute-level
    sort key and 10 by whole days, the worst jumping 70 days. One of the ten is
    inside a 30-day window, so a walk that stopped at the first out-of-window row
    would have dropped most of a month and reported a healthy run.
  - The country is in a hidden metadata cell, not in the title. The title's
    `Country: ` prefix is prose a client types: one of the 4,050 is titled `BA: `
    and one is `United Kingdom: MDB PIA test`, a test notice in the live archive.
  - There is no RSS feed. `/feed/` is the WordPress wrapper's and holds one item, a
    post titled "test" from 2020; `/delta/rss.html` and `/delta/syndication.html`
    302 to the CAS login.
  - `www.ebrd.com`'s own procurement-notices page carries ten notices in total and
    says so itself: everything else "is now only published on ECEPP".
  - EBRD's own corporate and consultancy procurement is on SMART by GEP and needs
    registration to see a notice. It is not read by this connector.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "contract" / "fixtures"
# Two files, because one would not survive the commit. The listing response is
# 3.8 MB and the notice pages are another 0.3 MB, and .pre-commit-config.yaml
# refuses an added file over 4 MB. JSON-escaping the listing into the same document
# pushed the pair to 4.9 MB; stored as the HTML it is, it is 3.7 MB and the notice
# pages sit beside it. The listing is also more readable as itself than as a
# one-line JSON string, which matters for a file whose job is to be evidence.
LISTING_FIXTURE = FIXTURES / "ebrd.html"
FIXTURE = FIXTURES / "ebrd.json"
SOURCE_YAML = REPO / "sources" / "ebrd.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.ebrd import (  # noqa: E402
    LOOKBACK_DAYS,
    NOTICE_URL,
    in_scope,
    parse_detail,
    parse_listing,
    published_date,
)

TIMEOUT_SECONDS = 180.0

# The notice type whose field set differs, and which the window never happens to
# contain. See the module docstring.
GPN = "General Procurement Notice"


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8"))
    covers = source["covers"]
    excluded = source.get("exclude_notice_types", [])
    cutoff = date.today().fromordinal(date.today().toordinal() - LOOKBACK_DAYS)

    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": agent},
        follow_redirects=True,
    ) as client:
        listing = client.get(source["list_url"])
        listing.raise_for_status()

        rows = parse_listing(listing.text, covers=covers, excluded_types=excluded)
        wanted = in_scope(rows, cutoff=cutoff, excluded_types=excluded)

        details_html = {}
        for row in wanted:
            response = client.get(NOTICE_URL.format(notice_id=row["notice_id"]))
            response.raise_for_status()
            details_html[row["notice_id"]] = response.text

        gpn_row = newest_gpn(rows)
        gpn_response = client.get(NOTICE_URL.format(notice_id=gpn_row["notice_id"]))
        gpn_response.raise_for_status()

    # Responses as they arrived and nothing derived. The contract test runs the real
    # parsers over these bytes, which is the point: a fixture of parsed dictionaries
    # would only prove that the recorder once agreed with itself.
    document = {
        "recorded_on": date.today().isoformat(),
        "listing_url": source["list_url"],
        "listing_file": LISTING_FIXTURE.name,
        "details_html": details_html,
        # Outside the window and deliberately kept out of `details_html`.
        "general_procurement_notice": {
            "notice_id": gpn_row["notice_id"],
            "html": gpn_response.text,
        },
    }
    details = {notice_id: parse_detail(html, notice_id=notice_id) for notice_id, html in details_html.items()}
    gpn_detail = parse_detail(gpn_response.text, notice_id=gpn_row["notice_id"])
    FIXTURES.mkdir(parents=True, exist_ok=True)
    LISTING_FIXTURE.write_text(listing.text, encoding="utf-8")
    FIXTURE.write_text(json.dumps(document, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    print(f"wrote {LISTING_FIXTURE.relative_to(REPO)} ({LISTING_FIXTURE.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"archive rows:     {len(rows)}")
    print(f"cutoff:           {cutoff.isoformat()} ({LOOKBACK_DAYS} days)")
    print(f"in scope:         {len(wanted)}")
    print(f"details fetched:  {len(details)}")
    print(f"countries:        {sorted({row['country_name'] for row in wanted})}")
    print(f"notice types:     {sorted({row['notice_type'] for row in wanted})}")
    print(f"excluded types:   {excluded}")
    labels = {label for detail in details.values() for label in detail}
    print(f"detail labels:    {sorted(labels)}")
    for label in sorted(labels):
        present = sum(1 for detail in details.values() if detail.get(label))
        print(f"    {present:3d}/{len(details)}  {label}")
    print(f"GPN (outside the window): {gpn_row['notice_id']} {gpn_row['published_date']} {gpn_row['country_name']}")
    print(f"    labels: {sorted(gpn_detail)}")
    return 0


def newest_gpn(rows: list[dict]) -> dict:
    """The most recent General Procurement Notice in a covered country.

    Derived from the listing rather than named by id, so re-recording the fixture
    picks up whatever the archive holds that day instead of pinning a notice that
    happened to be convenient once. Raises if there is none, because a source that
    publishes 715 of them and suddenly has none in a covered country is a changed
    source and the fixture should not quietly lose the case it exists to cover.
    """
    covered_gpns = [row for row in rows if row["notice_type"] == GPN and row["country_name"]]
    if not covered_gpns:
        raise ValueError(f"no {GPN} in a covered country anywhere in the {len(rows)}-row archive")
    return max(covered_gpns, key=published_date)


if __name__ == "__main__":
    raise SystemExit(main())
