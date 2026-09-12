"""Probe mcc.gov for a machine-readable procurement route. Records no fixture.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_mcc_fixture.py

The other scripts in this directory record a contract fixture from one real call.
This one cannot, and that is the finding rather than a shortfall: mcc.gov publishes
no procurement notices and offers no feed, API or JSON endpoint that leads to any,
so there is nothing on the host for a connector to parse and nothing to record.
There is deliberately no tests/contract/fixtures/mcc.json - an "mcc.json" sitting
in that directory would be read by a later session as a notices fixture, and the
honest state of this source is that no such thing exists.

What this script is for instead: it re-runs the whole probe that produced that
answer and prints the result as a table, so the claim in sources/mcc.yaml can be
re-checked in one command rather than believed. Exit status 1 means the answer is
still "no route"; exit status 0 means something in the table changed and MCC now
publishes something a connector could read, which is the decision point that
sources/mcc.yaml's unblock options a, b and c hang on.

THE FULL EVIDENCE, with the numbers as measured on 2026-09-12, is in the comment
block at the top of sources/mcc.yaml. In short:

  - /robots.txt is 404, so nothing is disallowed. The 404 body is cloud.gov Pages'
    own, so it is the origin's and not a proxy interstitial.
  - /sitemap.xml is the site's complete inventory: 5,240 URLs, and no procurement
    notice among them. That is MCC enumerating its own content types, which is why
    it is the strongest single measurement here.
  - The four mcc.gov-hosted RSS feeds its own /resources/rss-feeds page advertises
    are all 404, left behind by the WordPress site the static build replaced. The
    fifth, "Compact Program Procurements", points at dgMarket, a third-party
    aggregator that is down and that rule 21 forbids reading either way.
  - data.mcc.gov/data.json is a 311-dataset DCAT catalogue with no procurement
    notice dataset in it, and /api/grants - the only API path in the site's own
    JavaScript - is 404.
  - The one page with procurement data on it is the quarterly Partner Country
    Business Forecast, and its rows are forecasts: no deadline, no notice id, no
    notice URL, and a contact email per row that rule 19 keeps out of a model call.

One request per URL, no retry (rule 2), identified user agent (rule 21). Sixteen
requests in total, which is why this is a script a person runs and not something on
a schedule.
"""

from __future__ import annotations

import json
import os
import re
import sys

import httpx

TIMEOUT_SECONDS = 30.0

# Every route that would make MCC a feed, in the order the probe took them. The
# third element is what was measured on 2026-09-12; a mismatch is the interesting
# outcome and is what this script prints loudest.
ROUTES = [
    ("https://www.mcc.gov/robots.txt", "robots.txt: none published, so nothing disallowed", 404),
    ("https://www.mcc.gov/sitemap.xml", "the site's own complete inventory", 200),
    ("https://www.mcc.gov/resources/rss-feeds/", "MCC's own list of its RSS feeds", 200),
    ("https://www.mcc.gov/resources/feed-blogs", "advertised RSS feed, WordPress leftover", 404),
    ("https://www.mcc.gov/resources/feed-events", "advertised RSS feed, WordPress leftover", 404),
    ("https://www.mcc.gov/resources/feed-releases", "advertised RSS feed, WordPress leftover", 404),
    ("https://www.mcc.gov/resources/feed-speeches", "advertised RSS feed, WordPress leftover", 404),
    ("https://www.mcc.gov/api/grants", "the only API path in the site's own JavaScript", 404),
    ("https://www.mcc.gov/api/opportunities", "an API path that would be this source", 404),
    ("https://www.mcc.gov/procurement", "a notices listing, if one existed", 404),
    ("https://www.mcc.gov/opportunities", "a notices listing, if one existed", 404),
    ("https://data.mcc.gov/data.json", "the open data catalogue, Project Open Data v1.1", 200),
    (
        "https://www.mcc.gov/resources/doc/report-partner-country-business-forecast/",
        "the quarterly forecast: the only procurement data on the host",
        200,
    ),
]

# The dgMarket URL that /resources/rss-feeds advertises as "Compact Program
# Procurements". It is NOT probed by this script and must not be added to ROUTES.
# Rule 21 forbids it on two counts at once - it is a third-party mirror of an
# official portal, and its parent host answers an identified client with a bot-wall
# 403 that only a disguised user agent gets past. It is named here so that a later
# session finds the reason next to the URL rather than rediscovering the URL alone.
DGMARKET_ADVERTISED = "http://mcc.dgmarket.com/tenders/RssFeedAction.do~locationISO="

# The sitemap groups its URLs by the first two path segments. A procurement-notice
# content type would appear here as a new prefix; on 2026-09-12 none of these 5,240
# URLs was a notice.
SITEMAP_URL = "https://www.mcc.gov/sitemap.xml"
SITEMAP_URLS_MEASURED = 5240

# The forecast table's columns as measured. A changed header means MCC reshaped the
# document and the "it is a forecast, not a notice" reading has to be re-taken.
FORECAST_URL = "https://www.mcc.gov/resources/doc/report-partner-country-business-forecast/"
FORECAST_COLUMNS = [
    "Country",
    "MCA Program Procurement Contact",
    "Project Name",
    "Procurement Description",
    "Requirement Type",
    "Industry Focus",
    "Industry Sub Focus",
    "Anticipated Quarter of Publication",
]


def user_agent() -> str:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        raise SystemExit("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
    return agent


def probe(client: httpx.Client) -> list[tuple[str, str, int, int, bool]]:
    """One request per route. Returns (url, note, status, bytes, matches_measured)."""
    results = []
    for url, note, expected in ROUTES:
        response = client.get(url)
        results.append((url, note, response.status_code, len(response.content), response.status_code == expected))
    return results


def sitemap_shape(client: httpx.Client) -> tuple[int, list[tuple[str, int]]]:
    """The sitemap's URL count and its content types, so a new one is visible."""
    response = client.get(SITEMAP_URL)
    response.raise_for_status()
    locations = re.findall(r"<loc>(.*?)</loc>", response.text)

    counts: dict[str, int] = {}
    for location in locations:
        parts = location.replace("https://www.mcc.gov", "").strip("/").split("/")
        prefix = "/".join(parts[:2]) if len(parts) > 1 else (parts[0] or "/")
        counts[prefix] = counts.get(prefix, 0) + 1
    return len(locations), sorted(counts.items(), key=lambda item: -item[1])


def forecast_shape(client: httpx.Client) -> tuple[list[str], int]:
    """The forecast table's header row and data-row count, or ([], 0) if it is gone."""
    from selectolax.parser import HTMLParser

    response = client.get(FORECAST_URL)
    response.raise_for_status()
    tables = HTMLParser(response.text).css("table")
    if not tables:
        return [], 0
    rows = tables[0].css("tr")
    header = [" ".join(cell.text().split()) for cell in rows[0].css("th,td")] if rows else []
    return header, max(len(rows) - 1, 0)


def main() -> int:
    agent = user_agent()
    with httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": agent}, follow_redirects=True) as client:
        results = probe(client)
        total, prefixes = sitemap_shape(client)
        header, forecast_rows = forecast_shape(client)

    print("ROUTE PROBE, mcc.gov\n")
    for url, note, status, size, matches in results:
        mark = "  " if matches else "CHANGED"
        print(f"{mark} {status:>3} {size:>9,}b  {url}")
        print(f"            {note}")

    print(f"\nSITEMAP: {total:,} URLs (measured 2026-09-12: {SITEMAP_URLS_MEASURED:,})")
    for prefix, count in prefixes[:12]:
        print(f"  {count:>5}  /{prefix}")
    notices = [prefix for prefix, _ in prefixes if re.search(r"procure|tender|notice|bid|solicit", prefix, re.I)]
    print(f"  procurement-notice content types: {notices or 'none'}")

    print(f"\nFORECAST: {forecast_rows} data rows, columns {json.dumps(header)}")
    if header != FORECAST_COLUMNS:
        print("  CHANGED: the forecast's columns are not what was measured; re-read it before trusting")
    print("  measured 2026-09-12: 36 rows, of which 13 in pilot countries (XK 4, SL 5, CI 3, LR 1)")
    print("  no deadline column, no notice id, no notice URL; 'Anticipated Quarter of Publication' instead")

    print(f"\nNOT PROBED, and must not be: {DGMARKET_ADVERTISED}")
    print("  dgMarket is a third-party aggregator behind a bot wall. Rule 21 forbids both.")

    moved_routes = [url for url, _, _, _, matches in results if not matches]
    if moved_routes or header != FORECAST_COLUMNS:
        print("\nSomething moved. Re-read sources/mcc.yaml's measurement block against the table above.")
        return 0

    print("\nNo machine-readable route to an MCC procurement notice. sources/mcc.yaml stands: enabled false.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
