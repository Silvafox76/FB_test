"""Probe UNDP for a readable procurement route. Records no fixture, and may not.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_undp_fixture.py

The other scripts in this directory record a contract fixture from one real call.
This one must not, and that is the finding rather than a shortfall:
procurement-notices.undp.org answers every robot with `Disallow: /` on every path,
and rule 21 says respect robots.txt. The notices behind that file are real, public
and exactly the West African PFM work the pilot is looking for. Reading them is
what is forbidden, so no fixture can be recorded honestly and none is.

There is deliberately no tests/contract/fixtures/undp.json. A file with that name
would be read by a later session as a recorded notices response, and the honest
state of this source is that no response was ever fetched.

WHAT THIS SCRIPT FETCHES, AND THE LINE IT DOES NOT CROSS. On the three disallowed
hosts it requests /robots.txt and nothing else, ever. That file is the one request
you have to make in order to obey it, and every other path on those hosts is out
of bounds while sources/undp.yaml says tos_status: reviewed_restricted. On
www.undp.org and data.undp.org, whose robots.txt is the permissive Drupal default,
it requests the paths listed in PERMITTED below. The advertised RSS feed URLs in
ADVERTISED_NOT_PROBED are named so a later session finds the reason next to the
URL instead of rediscovering the URL alone - they are not requested here and must
not be added to any list in this file.

What this script is for: re-running the measurement so the claim in
sources/undp.yaml can be re-checked in one command rather than believed. Exit
status 1 means the answer is unchanged and the source is still unreadable; exit
status 0 means something moved and sources/undp.yaml's measurement block has to be
re-read against the table. That is the same convention as
scripts/record_mcc_fixture.py, and it is this way round because the interesting
outcome is the one that needs a person.

THE FULL EVIDENCE with the numbers as measured on 2026-09-12 is in the comment
block at the top of sources/undp.yaml. In short:

  - procurement-notices.undp.org, public-components.undp.org and jobs.undp.org
    serve one byte-identical 76-byte robots.txt that disallows every agent on every
    path. Three hosts, one file: an estate-wide policy on UNDP's legacy ColdFusion
    applications, not a stale file on one host.
  - UNDP's own pages advertise a procurement RSS feed and an embeddable listing
    component on those same two disallowed origins. A publisher offering
    syndication while disallowing every robot is a conflict a named person
    resolves, not a branch the code takes (rule 1).
  - www.undp.org has permissive robots and no notices: 116,983 sitemap URLs, of
    which 101 are country-office /procurement landing pages, 17 are static
    guidance pages, and none is a notice. Its HTML also answers this client 403
    from Akamai, which only a disguised user agent gets past (rule 21).
  - The coverage is not lost. UNDP publishes to the UN Global Marketplace too, and
    www.ungm.org/robots.txt permits /Public/Notice. That is monitor/connectors/
    ungm.py, already a separate source in BUILD_ORDER step 15.

One request per URL, no retry (rule 2), identified user agent (rule 21).
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

import httpx

TIMEOUT_SECONDS = 30.0

# The 76 bytes served by all three legacy hosts, CRLF line endings and all. Held as
# a hash so a single changed byte shows up as CHANGED rather than being eyeballed.
DISALLOW_ALL_SHA256 = "b9b5842464880731ddd7531cde5be5f10f429a74efd3fcfbcd032a07296ac946"

# The hosts whose robots.txt is read, and nothing else on them is. The third element
# is what was measured on 2026-09-12.
ROBOTS_HOSTS = [
    ("procurement-notices.undp.org", "the notices themselves; the source this file is about", True),
    ("public-components.undp.org", "where UNDP documents the embeddable notices listing", True),
    ("jobs.undp.org", "same legacy platform; probed to show the disallow is estate-wide", True),
    ("www.undp.org", "the modern Drupal site: permissive robots, but no notices on it", False),
    ("data.undp.org", "the open data site: permissive robots, 48 URLs, no procurement", False),
]

# Paths on the two PERMISSIVE origins. Every one of these is allowed by that host's
# own robots.txt, so a non-200 here is a WAF decision and not a policy one.
#
# The third element is the set of statuses measured on 2026-09-12, and two of these
# rows carry two of them on purpose. www.undp.org sits behind Akamai, which refuses
# most requests from this identified client and admits a few. Tallied over one
# session: /procurement answered 403 eight times and 200 twice out of ten requests,
# including a run of five consecutive 403s immediately after one of the 200s; `/`
# answered 403 once and 200 twice out of three. An expectation of {403} would call
# every admitted request CHANGED and an expectation of {200} would call every
# refused one CHANGED, so the honest expectation is both, and the flapping itself
# is the measurement. It is also why nothing here could ever be a scheduled source:
# rule 2 forbids retrying into a host that answers this way, and a connector that
# fetched it would report failure most mornings and a success occasionally.
#
# None of this decides anything about UNDP notices, because www.undp.org has none
# (the sitemap measurement below). It is recorded so a later session does not spend
# an afternoon on the 403 believing the notices are behind it.
PERMITTED = [
    ("https://www.undp.org/sitemap.xml", "the sitemap index; 7 pages on 2026-09-12", {200}),
    ("https://www.undp.org/", "an HTML path behind Akamai: mostly 403, occasionally 200", {200, 403}),
    ("https://www.undp.org/procurement", "UNDP's own guidance page, same WAF behaviour", {200, 403}),
    ("https://data.undp.org/sitemap.xml", "48 URLs, none of them procurement", {200}),
]

# Advertised by UNDP's own pages as syndication routes for these notices, known from
# a search engine's index and NEVER FETCHED. They sit on origins that disallow every
# robot on every path. Do not add them to PERMITTED. If a person obtains written
# permission from UNDP (sources/undp.yaml, unblock option a), this is the list to
# start from - and the first thing to establish is whether any of them is still
# alive, because four of the five feeds MCC advertised turned out to be 404
# leftovers from a platform migration.
ADVERTISED_NOT_PROBED = [
    "https://procurement-notices.undp.org/proc_notices_rss_feed.cfm",
    "https://procurement-notices.undp.org/rss_feeds/rss.xml",
    "https://public-components.undp.org/help/howto/proc_notices.cfm",
]

# Where this coverage goes instead while UNDP is unreadable.
UNGM_ROBOTS = "https://www.ungm.org/robots.txt"
UNGM_NOTICE_PATH = "/Public/Notice"

SITEMAP_PAGES_MEASURED = 7


def user_agent() -> str:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        raise SystemExit("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
    return agent


def disallows_everything(robots: str) -> bool:
    """True when the `*` group forbids every path.

    Deliberately narrow: it answers the one question this source turns on and does
    not implement robots.txt generally. A group is a run of User-Agent lines
    followed by its rules, so the `*` group's rules are the lines between the last
    `User-Agent: *` and the next User-Agent line.
    """
    in_star_group = False
    for line in robots.splitlines():
        text = line.split("#")[0].strip()
        if not text:
            continue
        field, _, value = text.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            in_star_group = value == "*"
        elif in_star_group and field == "disallow" and value == "/":
            return True
    return False


def read_robots(client: httpx.Client) -> list[tuple[str, str, int, int, str, bool, bool]]:
    """One request per host, /robots.txt only. Returns a row per host."""
    rows = []
    for host, note, measured_blanket in ROBOTS_HOSTS:
        response = client.get(f"https://{host}/robots.txt")
        body = response.content
        digest = hashlib.sha256(body).hexdigest()
        blanket = response.status_code == 200 and disallows_everything(response.text)
        rows.append((host, note, response.status_code, len(body), digest, blanket, blanket == measured_blanket))
    return rows


def read_permitted(client: httpx.Client) -> list[tuple[str, str, int, int, bool]]:
    """The permissive origins only. Every path here is allowed by its host's robots."""
    rows = []
    for url, note, expected in PERMITTED:
        response = client.get(url)
        rows.append((url, note, response.status_code, len(response.content), response.status_code in expected))
    return rows


def sitemap_pages(client: httpx.Client) -> int:
    """How many pages www.undp.org's sitemap index lists. Allowed by its robots."""
    response = client.get("https://www.undp.org/sitemap.xml")
    response.raise_for_status()
    return len(re.findall(r"<loc>(.*?)</loc>", response.text))


def ungm_permits_notices(client: httpx.Client) -> bool:
    """Whether UNGM's robots.txt still says nothing about its public notice listing."""
    response = client.get(UNGM_ROBOTS)
    response.raise_for_status()
    if disallows_everything(response.text):
        return False
    disallowed = [
        line.split(":", 1)[1].strip().lower()
        for line in response.text.splitlines()
        if line.split("#")[0].strip().lower().startswith("disallow:")
    ]
    return not any(path and UNGM_NOTICE_PATH.lower().startswith(path) for path in disallowed)


def main() -> int:
    with httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": user_agent()}, follow_redirects=True) as client:
        robots = read_robots(client)
        permitted = read_permitted(client)
        pages = sitemap_pages(client)
        ungm_open = ungm_permits_notices(client)

    print("ROBOTS.TXT, and nothing else is requested on the disallowed hosts\n")
    for host, note, status, size, digest, blanket, matches in robots:
        mark = "       " if matches else "CHANGED"
        verdict = "DISALLOWS EVERY ROBOT ON EVERY PATH" if blanket else "permissive"
        print(f"{mark} {status:>3} {size:>6,}b  {host}")
        print(f"           {verdict}")
        print(
            f"           sha256 {digest[:16]}…{'  (the 76-byte legacy file)' if digest == DISALLOW_ALL_SHA256 else ''}"
        )
        print(f"           {note}")

    print("\nPERMITTED ORIGINS, where reading is allowed and there are no notices\n")
    for url, note, status, size, matches in permitted:
        mark = "       " if matches else "CHANGED"
        print(f"{mark} {status:>3} {size:>9,}b  {url}")
        print(f"           {note}")
    print(f"\n  www.undp.org sitemap index: {pages} pages (measured 2026-09-12: {SITEMAP_PAGES_MEASURED})")
    print("  measured 2026-09-12: 116,983 URLs across 6 of 7 pages, 101 country-office")
    print("  /procurement landing pages, 17 static /procurement/* guidance pages, no notice node")

    print("\nNOT PROBED, and must not be:")
    for url in ADVERTISED_NOT_PROBED:
        print(f"  {url}")
    print("  UNDP advertises these as syndication routes for exactly these notices.")
    print("  They sit on origins that disallow every robot on every path. The conflict is")
    print("  a terms decision for a named person (sources/undp.yaml, unblock options a and c).")

    print(
        f"\nWHERE THE COVERAGE GOES MEANWHILE: UNGM {UNGM_NOTICE_PATH} "
        f"{'permitted by robots.txt' if ungm_open else 'NO LONGER PERMITTED - check it'}"
    )
    print("  monitor/connectors/ungm.py, already a separate source in BUILD_ORDER step 15.")

    moved = [host for host, _, _, _, _, _, matches in robots if not matches]
    moved += [url for url, _, _, _, matches in permitted if not matches]
    if moved or pages != SITEMAP_PAGES_MEASURED or not ungm_open:
        print(f"\nSomething moved: {moved or 'sitemap page count or UNGM robots'}.")
        print("Re-read sources/undp.yaml's measurement block against the table above.")
        return 0

    print("\nUNDP's notices host still disallows every robot. sources/undp.yaml stands: enabled false.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
