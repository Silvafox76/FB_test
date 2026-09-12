"""Record the simap contract fixture from one real pass over the live API.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_simap_fixture.py

Writes `tests/contract/fixtures/simap.json` and prints the shape of what came back.
The window is wider than the connector's own two days on purpose, so the fixture
carries the type, language and absent-field variance a single run does not; the
request is otherwise the one `SimapConnector` makes, built by its own `params`.

There is no documented simap API. What is below was established by probing the
live host on 2026-09-12, not by reading anything:

  - The endpoint is `/api/publications/v2/project/project-search`, a GET, named in
    the site's own generated client in `/assets/js/main.js` as
    `getPublicProjectSearch` together with all nineteen of its query parameters.
    No key, no registration, no session and no CSRF token: a cold client with an
    identified user agent gets a 200.
  - **It is project-centric.** One row per project, carrying that project's newest
    publication, and both the date bounds and the type filter apply to that newest
    publication. A two-day window returned 195 rows with 195 distinct project ids
    and 195 distinct publication ids. So a *longer* lookback loses notices: a
    tender superseded by an award inside the window is answered as the award and
    then dropped by the type filter, project and all.
  - **Unknown parameter names are ignored in silence.** `nonsenseParameter=1`
    changed nothing and still answered 200. Known names with bad values are refused
    loudly: `lang=DE`, `cpvCodes=72` and `newestPubTypes=nope` are each a 400 with
    `E0025 Invalid parameter`. Hence the window and type guards in `parse_page`.
  - **`cpvCodes` is hierarchical, eight digits, comma separated.** Asking for the
    three division roots returned notices whose own codes are 79420000, 72300000,
    48100000, 72230000, 48921000 and 79330000.
  - **The filter's type vocabulary is not the result's.** `newestPubTypes=award` is
    a 400; `award_tender`, `award_competition` and `award_study_contract` are the
    values it takes, and they answer with rows whose `pubType` is `award` or
    `direct_award`.
  - **The notice's language is `base.creationLanguage` on the detail.** The
    multilingual `title` and `orderDescription` dicts carry de, en, fr and it keys
    with nulls for the languages not published, and `base.translationLanguages`
    names which of the filled ones are renderings rather than the original.
    `project-info.publicationLanguages` looks like the same thing and is not: it is
    null on most publication types.
  - **The buyer's level of government is in a different document.** The publication
    carries the office's name and address but not its type; `/api/procoffices/v1/po/public`
    is a 4,966-row public directory whose `type` is one of eight values across
    federal, cantonal, communal and foreign.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import yaml

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "simap.json"
SOURCE_YAML = REPO / "sources" / "simap.yaml"
THRESHOLDS_YAML = REPO / "config" / "thresholds.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.simap import (  # noqa: E402
    DETAIL_URL,
    MAX_PAGES,
    PROC_OFFICES_URL,
    SimapConnector,
)
from monitor.models import Source  # noqa: E402

# Wider than the connector's LOOKBACK_DAYS so the fixture holds more than one day's
# worth of publication types and languages. Everything else about the request is
# the connector's own.
RECORD_LOOKBACK_DAYS = 6


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))
    prefixes = yaml.safe_load(THRESHOLDS_YAML.read_text(encoding="utf-8"))["cpv_pass_prefixes"]
    connector = SimapConnector(source, prefixes)

    today = date.today()
    window = (today - timedelta(days=RECORD_LOOKBACK_DAYS), today)
    expected_types = connector.expected_result_types()

    with httpx.Client(
        timeout=120.0,
        headers={"User-Agent": agent, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        pages = []
        cursor = ""
        for _ in range(MAX_PAGES):
            response = client.get(source.api_url, params=connector.params(window, cursor))
            response.raise_for_status()
            document = response.json()
            pages.append(document)

            rows = document["projects"]
            cursor = document["pagination"].get("lastItem") or ""
            if not rows or len(rows) < document["pagination"]["itemsPerPage"] or not cursor:
                break

        rows = [row for page in pages for row in page["projects"]]

        offices_document = client.get(PROC_OFFICES_URL).json()

        details = {}
        for row in rows:
            detail = client.get(DETAIL_URL.format(project_id=row["id"], publication_id=row["publicationId"]))
            detail.raise_for_status()
            details[row["publicationId"]] = detail.json()

    fixture = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "window": [day.isoformat() for day in window],
        "params": connector.params(window),
        "expected_result_types": sorted(expected_types),
        "search_pages": pages,
        "details": details,
        "proc_offices": offices_document,
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    report(fixture, rows, details, offices_document)
    return 0


def report(fixture: dict, rows: list[dict], details: dict, offices_document: dict) -> None:
    """Print what came back, so the parser is written against counts and not guesses."""
    offices = offices_document["procOffices"]
    by_office = {office["id"]: office for office in offices}
    bases = [detail["base"] for detail in details.values()]
    infos = [detail["project-info"] for detail in details.values()]
    procurements = [detail.get("procurement") for detail in details.values()]
    date_blocks = [detail.get("dates") for detail in details.values()]
    total = len(details)

    def present(values) -> str:
        return f"{sum(1 for value in values if value)}/{total}"

    def language_of(detail: dict) -> str:
        return detail["base"]["creationLanguage"]

    def in_own_language(getter) -> str:
        return present((getter(detail) or {}).get(language_of(detail)) for detail in details.values())

    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"window:   {fixture['window']}")
    print(f"params:   {fixture['params']}")
    print(f"pages:    {len(fixture['search_pages'])}, rows {len(rows)}")
    print(f"projects: {len({row['id'] for row in rows})} distinct of {len(rows)} rows")
    print(f"pubTypes: {dict(Counter(row['pubType'] for row in rows))}")
    print(f"dates:    {sorted({row['publicationDate'] for row in rows})}")
    print(f"offices:  {len(offices)}, types {dict(Counter(office['type'] for office in offices))}")

    print(f"creationLanguage:   {dict(Counter(base['creationLanguage'] for base in bases))}")
    translations = Counter(
        (entry["language"], entry["type"]) for base in bases for entry in base["translationLanguages"] or []
    )
    print(f"translationLangs:   {dict(translations)}")
    print(f"title in own lang:  {in_own_language(lambda detail: detail['base']['title'])}")
    body = in_own_language(lambda detail: (detail.get("procurement") or {}).get("orderDescription"))
    buyer = in_own_language(lambda detail: (detail["project-info"].get("procOfficeAddress") or {}).get("name"))
    print(f"body in own lang:   {body}")
    print(f"buyer in own lang:  {buyer}")

    print(f"procOfficeId found: {present(base.get('procOfficeId') in by_office for base in bases)}")
    used = Counter(by_office[base["procOfficeId"]]["type"] for base in bases if base.get("procOfficeId") in by_office)
    print(f"office types used:  {dict(used)}")
    print(f"procOfficeAddress:  {present(info.get('procOfficeAddress') for info in infos)}")
    buyer_countries = Counter((info.get("procOfficeAddress") or {}).get("countryId") for info in infos)
    print(f"buyer countryIds:   {dict(buyer_countries)}")

    print(f"procurement block:  {present(procurements)}")
    print(f"cpvCode present:    {present(block and block.get('cpvCode') for block in procurements)}")
    codes = [block["cpvCode"]["code"] for block in procurements if block and block.get("cpvCode")]
    print(f"cpv divisions:      {dict(Counter(code[:2] for code in codes))}")
    print(f"additional codes:   {present(block and block.get('additionalCpvCodes') for block in procurements)}")
    lot_codes = present(
        {code["code"] for lot in detail.get("lots") or [] for code in lot.get("additionalCpvCodes") or []}
        - {code["code"] for code in (detail.get("procurement") or {}).get("additionalCpvCodes") or []}
        for detail in details.values()
    )
    print(f"lot-only cpv codes: {lot_codes}")

    print(f"dates block:        {present(date_blocks)}")
    print(f"offerDeadline:      {present(block and block.get('offerDeadline') for block in date_blocks)}")
    deadlines = [block["offerDeadline"] for block in date_blocks if block and block.get("offerDeadline")]
    print(f"deadline offsets:   {dict(Counter(value[-6:] for value in deadlines))}")
    print(f"deadline samples:   {deadlines[:3]}")
    print(f"corrections:        {present(base['corrected'] for base in bases)}")
    print(f"ted cross-published:{present(detail.get('ted') for detail in details.values())}")


if __name__ == "__main__":
    raise SystemExit(main())
