"""Probe the UN Global Marketplace. Records no fixture, and deliberately so.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_ungm_fixture.py

The other recorders in this directory write `tests/contract/fixtures/<id>.json` from
one real call. This one must not, and the reason is not that the call fails - it
succeeds, cleanly, every time. www.ungm.org/robots.txt permits /Public, the search
endpoint answers an anonymous httpx POST with real notices, the filters filter and
the source publishes about 25 pilot-relevant notices a day. What stops it is UNGM's
own Terms and Conditions, which forbid commercial use of the material (clause 5.2),
forbid including it in a retrieval system without prior written permission (5.3) and
assert the publishing organisations' copyright over the notices themselves (7.2).
Storing them in `notices` for a company to bid on is both of the named prohibitions.

So there is deliberately no tests/contract/fixtures/ungm.json. A file with that name
would put UNGM notice text in this repository's git history permanently, which is the
precise act 7.2 names, and a commit is not undone by a later decision going the other
way. Recording it is the FIRST step of the build the day permission exists - see
sources/ungm.yaml, unblock options (a), (b) and (c) - not a step to take first and
justify afterwards.

WHAT THIS SCRIPT FETCHES, AND WHAT IT KEEPS. It reads /robots.txt, the Terms and
Conditions page, and the public search endpoint about eight times with different
filters. It keeps counts: the `noticeTotal` each response carries, and how many
`.dataRow` elements came back. IT WRITES NO NOTICE TEXT ANYWHERE. The notice rows
arrive in the response body because the endpoint returns them - that cannot be
avoided and it is what any browser does - and they are counted and dropped.

What it is for: re-running the measurement so the claim in sources/ungm.yaml can be
re-checked in one command rather than believed, and in particular so that a change in
the terms is noticed. Exit status 1 means the answer is unchanged and the source is
still blocked on terms; exit status 0 means something moved and sources/ungm.yaml's
measurement block has to be re-read against the table. That is the same convention as
scripts/record_undp_fixture.py and scripts/record_mcc_fixture.py, and it is this way
round because the interesting outcome is the one that needs a person.

THE FULL EVIDENCE with the numbers as measured on 2026-09-12 is in the comment block
at the top of sources/ungm.yaml. In short:

  - robots.txt permits everything under /Public. /Scripts/ is disallowed and was not
    read; the endpoint contract came from /bundles/ungmcommon, which is a different
    path and is not disallowed.
  - No login, no registration, no cookie, no antiforgery token. POST
    /Public/Notice/Search with no prior GET returns 15 notices.
  - Not a JavaScript application for our purposes: the listing arrives as a
    server-rendered HTML fragment from that one POST, so this is a FeedConnector in
    the shape of monitor/connectors/prozorro.py and not step 17 browser work.
  - PageSize is silently capped at 15, and an unparseable PublishedFrom is silently
    ignored and returns the whole active corpus. Both are the kind of quiet failure
    the World Bank connector's docstring exists to warn about.
  - UNDP publishes here in volume - 88 of the 269 active notices in the covered
    countries - which answers the question sources/undp.yaml asked, and means UNDP
    procurement is now unreachable by either route until a person decides something.

One request per URL, no retry (rule 2), identified user agent (rule 21).
"""

from __future__ import annotations

import os
import re
import sys

import httpx

TIMEOUT_SECONDS = 60.0

ROBOTS_URL = "https://www.ungm.org/robots.txt"
TERMS_URL = "https://www.ungm.org/Public/Pages/Terms"
SEARCH_URL = "https://www.ungm.org/Public/Notice/Search"

# The path a connector would read. The point of the robots check is that no Disallow
# line in the `*` stanza is a prefix of it.
NOTICE_PATH = "/Public/Notice"

# The `*` stanza's Disallow lines as measured 2026-09-12. Compared as a set so a new
# line shows up as CHANGED rather than being eyeballed.
DISALLOWED_MEASURED = frozenset(
    {
        "/UNUser/Documents/*",
        "/unuser/documents/*",
        "/UNUser/Documents/",
        "/unuser/documents/",
        "/Styles/",
        "/Scripts/",
        "/Images/",
        "/Content/",
        "/Web References/",
        "/Views/",
        "/Service References/",
        "/apple-touch-icon.png",
        "/AccessDeniedError.htm",
        "/favicon.ico",
        "/FileNotFoundError.htm",
        "/GenericError.htm",
        "/InternalError.htm",
        "/SolveUrl?",
    }
)

# The four clauses the decision turns on, as fragments that must still be present
# verbatim on the terms page. THESE ARE THE LINES THAT WOULD UNBLOCK THIS SOURCE IF
# THEY CHANGED, so they are checked rather than summarised. Whitespace is collapsed on
# both sides before comparing, because the page's HTML wraps them differently over
# time. Quoted in full in sources/ungm.yaml.
TERMS_CLAUSES = {
    "5.2 no commercial use": "None of the material on the Site may be used for any commercial or public use",
    "5.3 no retrieval system": (
        "or included in any retrieval system or site without the prior written permission of UNGM"
    ),
    "7.2 notices are copyrighted": (
        "It may not be reproduced, stored, transmitted or photocopied in any form or by any means "
        "without the prior written consent of the respective organizations"
    ),
    "7.5 copying to bid is allowed": (
        "for the purpose of preparing documents to be submitted by the Subscriber in response of any Materials"
    ),
}

# The address clause 5.3 names for a permission request. Checked because unblock
# option (a) in sources/ungm.yaml is an email to it, and an address that has moved
# would send that email nowhere.
PERMISSION_CONTACT = "registry@ungm.org"

# UNGM's numeric ids for the 19 countries in sources/ungm.yaml's `covers`, read from
# the country picker on /Public/Notice on 2026-09-12. Kosovo is XK 2525.
COUNTRY_IDS = {
    "BJ": "2314",
    "BF": "2324",
    "CI": "2341",
    "GM": "2367",
    "GH": "2370",
    "LR": "2407",
    "ML": "2418",
    "MR": "2422",
    "NE": "2442",
    "NG": "2443",
    "SN": "2472",
    "SL": "2475",
    "TG": "2494",
    "UA": "2504",
    "AL": "2294",
    "BA": "2318",
    "XK": "2525",
    "ME": "2524",
    "MK": "2413",
}

# The 21 fields UNGM.Notice.NoticeSearch.prototype.BuildOptions sends, with the
# defaults the page itself uses. Read from /bundles/ungmcommon on 2026-09-12.
SEARCH_DEFAULTS = {
    "PageIndex": 0,
    "PageSize": 15,
    "Title": "",
    "Description": "",
    "Reference": "",
    "PublishedFrom": "",
    "PublishedTo": "",
    "DeadlineFrom": "",
    "DeadlineTo": "",
    "Countries": [],
    "Agencies": [],
    "UNSPSCs": [],
    "NoticeTypes": [],
    "SortField": "DatePublished",
    "SortAscending": False,
    "isPicker": False,
    "IsSustainable": False,
    "IsActive": True,
    "NoticeDisplayType": None,
    "NoticeSearchTotalLabelId": "noticeSearchTotal",
    "TypeOfCompetitions": [],
}

# The response carries its total in a trailing script block, and carries no such block
# at all when nothing matched.
TOTAL = re.compile(r'var noticeTotal = "(\d+)"')
DATA_ROW = 'class="tableRow dataRow'

WHITESPACE = re.compile(r"\s+")
TAGS = re.compile(r"<[^>]+>")

# The probes, and what each one establishes. The third element is what was measured on
# 2026-09-12; None means the number is expected to drift and only the RELATION to the
# baseline matters, which each note states.
PROBES = [
    ("baseline, every active notice", {}, 1445),
    ("PageSize=50 -> silently capped at 15 rows", {"PageSize": 50}, 1445),
    ("PublishedFrom=garbage -> SILENTLY IGNORED, whole corpus", {"PublishedFrom": "garbage"}, 1445),
    ("unknown key -> silently ignored, whole corpus", {"WidgetNonsense": "1"}, 1445),
    ("IsActive=false -> the whole history", {"IsActive": False}, None),
    ("Countries=[GH] -> the country filter works", {"Countries": ["2370"]}, None),
    ("Countries=[the 19 covered]", {"Countries": list(COUNTRY_IDS.values())}, None),
    (
        "Countries=[the 19] + Agencies=[UNDP]",
        {"Countries": list(COUNTRY_IDS.values()), "Agencies": ["1"]},
        None,
    ),
]


def user_agent() -> str:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        raise RuntimeError("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
    return agent


def flatten(markup: str) -> str:
    """The page as one line of text, so a clause that re-wrapped still matches."""
    return WHITESPACE.sub(" ", TAGS.sub(" ", markup))


def read_robots(client: httpx.Client) -> tuple[int, int, frozenset[str], bool]:
    """The `*` stanza's Disallow lines, and whether /Public/Notice is permitted.

    Only the `*` stanza is read. The file's second stanza names nine search-engine
    agents and MONITOR_USER_AGENT is none of them, so those lines do not apply and
    counting them would make this report say the path is blocked when it is not.
    """
    response = client.get(ROBOTS_URL)
    response.raise_for_status()

    disallowed: list[str] = []
    in_star_stanza = False
    for raw in response.text.splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        field, _, value = line.partition(":")
        field, value = field.strip().lower(), value.strip()
        if field == "user-agent":
            in_star_stanza = value == "*"
        elif field == "disallow" and in_star_stanza and value:
            disallowed.append(value)

    permitted = not any(NOTICE_PATH.lower().startswith(path.rstrip("*").lower()) for path in disallowed)
    return response.status_code, len(response.content), frozenset(disallowed), permitted


def read_terms(client: httpx.Client) -> tuple[int, dict[str, bool], bool]:
    """Whether each operative clause is still on the page, word for word."""
    response = client.get(TERMS_URL)
    response.raise_for_status()
    text = flatten(response.text)
    present = {label: WHITESPACE.sub(" ", clause) in text for label, clause in TERMS_CLAUSES.items()}
    return response.status_code, present, PERMISSION_CONTACT in text


def search(client: httpx.Client, overrides: dict) -> tuple[int, int, int | None]:
    """One search call. Returns status, row count and the response's own total.

    The rows themselves are counted and dropped. Nothing is written to disk: see the
    module docstring.
    """
    body = dict(SEARCH_DEFAULTS)
    body.update(overrides)
    response = client.post(SEARCH_URL, json=body)
    response.raise_for_status()
    match = TOTAL.search(response.text)
    return response.status_code, response.text.count(DATA_ROW), int(match.group(1)) if match else None


def main() -> int:
    with httpx.Client(
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
    ) as client:
        robots_status, robots_size, disallowed, path_permitted = read_robots(client)
        terms_status, clauses, contact_present = read_terms(client)
        results = [(note, *search(client, overrides), measured) for note, overrides, measured in PROBES]

    print("ROBOTS.TXT, read first and obeyed\n")
    print(f"        {robots_status:>3} {robots_size:>6,}b  {ROBOTS_URL}")
    print(f"            {len(disallowed)} Disallow lines in the `*` stanza (measured: {len(DISALLOWED_MEASURED)})")
    robots_moved = disallowed != DISALLOWED_MEASURED
    for path in sorted(disallowed - DISALLOWED_MEASURED):
        print(f"    NEW     {path}")
    for path in sorted(DISALLOWED_MEASURED - disallowed):
        print(f"    GONE    {path}")
    verdict = "permitted" if path_permitted else "NO LONGER PERMITTED - stop here"
    print(f"            {NOTICE_PATH} is {verdict}")
    print("            /Scripts/ is disallowed and is not read; /bundles/ is a different path and is not listed")

    print("\nTHE ENDPOINT, anonymous: no login, no cookie, no antiforgery token\n")
    for note, status, rows, total, measured in results:
        shown = f"{total:,}" if total is not None else "none"
        drift = "" if measured is None else f"   (measured 2026-09-12: {measured:,})"
        print(f"        {status:>3}  rows={rows:<3} total={shown:>9}  {note}{drift}")
    print("\n            PageSize above 15 returns 15. An unreadable date returns the whole")
    print("            corpus with a 200. Both would read as a healthy run.")

    print("\nTHE TERMS, which are what actually stops this source\n")
    for label, found in clauses.items():
        print(f"        {'still there' if found else 'CHANGED    '}  {label}")
    contact_mark = "still there" if contact_present else "CHANGED    "
    print(f"        {contact_mark}  {PERMISSION_CONTACT}, the address clause 5.3 names")

    terms_moved = not all(clauses.values())
    baselines_moved = [note for note, _, _, total, measured in results if measured is not None and total != measured]

    if terms_moved:
        print("\nA CLAUSE MOVED. Re-read the terms page in full before anything else: the")
        print("clauses above are the whole reason sources/ungm.yaml is disabled, and one of")
        print("them no longer reads as it did. This needs a person, not a rebuild.")
        return 0
    if robots_moved or not path_permitted or baselines_moved:
        print(f"\nSomething moved: {'robots.txt' if robots_moved else ''} {baselines_moved}".rstrip())
        print("Re-read sources/ungm.yaml's measurement block against the table above.")
        return 0

    print("\nUNGM is readable and its terms still forbid the use this pipeline makes of it.")
    print("sources/ungm.yaml stands: enabled false, tos_status reviewed_restricted.")
    print(f"The unblock is one email to {PERMISSION_CONTACT} (option a) - nothing here can substitute for it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
