"""Record the BOAMP contract fixture from one real pass over DILA's open-data flux.

    MONITOR_USER_AGENT="FreeBalance-OpportunityMonitor/0.1 (+you@freebalance.com)" \
        uv run python scripts/record_boamp_fixture.py

Writes `tests/contract/fixtures/boamp.json` and prints the census of the day it
read, which is where the numbers in `monitor/connectors/boamp.py` and
`monitor/normalise/boamp.py` come from.

The pass is the connector's own: one day directory index, then every notice file in
it, with the connector's own URLs. The day is a *completed* publication day, one
before today, because a day directory fills all day long - 223 of the 414 files
published on 2026-09-11 were written in the 04:46 batch and the other 191 arrived
up to 19:45 - so recording today would record half a day and call it a day.

**The fixture holds the whole index and a subset of the notice files.** A day is
414 files and 29 MB of XML, which is ten times the largest fixture in the
repository, and most of each file is DILA's own HTML rendering of the notice that
nothing in the pipeline reads. So the index is stored verbatim - the listing parser
is tested against the real 414-row page - together with the census of the whole day
and the files that carry each shape the census found. `select` below is the rule,
and every chosen file records why it was chosen, so a reader can tell a
representative fixture from a convenient one.

What probing the live flux established before any of this was written is in
`monitor/connectors/boamp.py`'s docstring: which of BOAMP's four hosts are blocked
or disallowed, that the day directory is the publication date, and that the index's
timestamps are Europe/Paris. This script assumes all of it.
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
FIXTURE = REPO / "tests" / "contract" / "fixtures" / "boamp.json"
SOURCE_YAML = REPO / "sources" / "boamp.yaml"
THRESHOLDS_YAML = REPO / "config" / "thresholds.yaml"

sys.path.insert(0, str(REPO))

from monitor.connectors.boamp import (  # noqa: E402
    BoampConnector,
    listing_names,
    notice_idweb,
    notice_nature,
    parse_document,
)
from monitor.models import Source  # noqa: E402
from monitor.normalise.boamp import (  # noqa: E402
    DEADLINE,
    FORMAT_BLOCK,
    FormatPaths,
    cpv_codes,
    document_element,
    format_paths,
    text_of,
)

# One completed publication day back. A person recording an older day edits this;
# the flux keeps every day of the year.
RECORD_DAY_OFFSET = 1

# The nature the connector yields, so the variance picks below are about notices
# that actually reach the pipeline rather than about awards.
TENDER = "APPEL_OFFRE"


def main() -> int:
    agent = os.environ.get("MONITOR_USER_AGENT")
    if not agent:
        print("MONITOR_USER_AGENT is not set; a source is never read anonymously (rule 21)")
        return 1

    source = Source.model_validate(yaml.safe_load(SOURCE_YAML.read_text(encoding="utf-8")))
    prefixes = yaml.safe_load(THRESHOLDS_YAML.read_text(encoding="utf-8"))["cpv_pass_prefixes"]
    connector = BoampConnector(source, prefixes)

    day = date.today() - timedelta(days=RECORD_DAY_OFFSET)
    if day >= date.today():
        print(f"{day} is not a completed publication day")
        return 1
    day_url = connector.day_url(day)

    with httpx.Client(
        timeout=60.0,
        headers={"User-Agent": agent},
        follow_redirects=True,
    ) as client:
        index = client.get(day_url)
        index.raise_for_status()
        names = listing_names(index.text, day_url=day_url)
        print(f"index:    {day_url} -> {len(names)} notice files")

        notices: dict[str, str] = {}
        for position, name in enumerate(names, start=1):
            response = client.get(day_url + name)
            response.raise_for_status()
            notices[name] = response.content.decode("utf-8")
            if position % 50 == 0:
                print(f"          {position}/{len(names)}")

    facts = {name: shape(name, text, prefixes) for name, text in notices.items()}
    chosen = select(facts)

    fixture = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "day": day.isoformat(),
        "day_url": day_url,
        # Verbatim, so the listing parser is tested against the real page and not
        # against a hand-written one.
        "index": index.text,
        "census": census(facts),
        "selection": chosen,
        "notices": {name: notices[name] for name in chosen},
    }
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(fixture, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    report(fixture, facts)
    return 0


def shape(name: str, text: str, prefixes: list[str]) -> dict:
    """What one notice file is, in the terms the census and the selection are written in."""
    root = parse_document(text, reference=name)
    nature = notice_nature(root, reference=name)
    declared = [child.tag for child in root.find(FORMAT_BLOCK)][0]
    stated_deadline = text_of(root, DEADLINE)

    facts = {
        "idweb": notice_idweb(root, reference=name),
        "format": declared,
        "nature": nature,
        "deadline_shape": deadline_shape(stated_deadline),
        "departements": len(root.findall("GESTION/INDEXATION/DEP_PUBLICATION")),
        "resume_objet_chars": len(text_of(root, "GESTION/INDEXATION/RESUME_OBJET")),
    }

    if nature != TENDER:
        return facts | {"title_chars": 0, "body_chars": 0, "cpv": [], "cpv_passes": False}

    paths: FormatPaths = format_paths(root, reference=name)
    document = document_element(root, paths, reference=name)
    codes = cpv_codes(document, paths)
    return facts | {
        "title_chars": len(text_of(document, paths.title)),
        "body_chars": len(text_of(document, paths.description)),
        "cpv": codes,
        "cpv_passes": any(code.startswith(prefix) for code in codes for prefix in prefixes),
    }


def deadline_shape(stated: str) -> str:
    """`2026-09-29T12:00:00.000+02:00` -> `NNNN-NN-NNTNN:NN:NN.NNN+NN:NN`, or `absent`.

    The shape and not the value, because it is the set of shapes that decides
    whether the shared date parser can read this source: six of them on the day the
    fixture was recorded, two carrying a fractional second it cannot.
    """
    if not stated:
        return "absent"
    return "".join("N" if character.isdigit() else character for character in stated)


def census(facts: dict[str, dict]) -> dict:
    """The counts of the whole day, which is what the docstrings cite."""
    tenders = {name: fact for name, fact in facts.items() if fact["nature"] == TENDER}
    return {
        "files": len(facts),
        "by_format": dict(Counter(fact["format"] for fact in facts.values())),
        "by_nature": dict(Counter(fact["nature"] for fact in facts.values())),
        "by_format_and_nature": {
            f"{declared}/{nature}": count
            for (declared, nature), count in sorted(
                Counter((fact["format"], fact["nature"]) for fact in facts.values()).items()
            )
        },
        "deadline_shapes": dict(Counter(fact["deadline_shape"] for fact in facts.values())),
        "tenders": len(tenders),
        "tenders_with_deadline": sum(1 for fact in tenders.values() if fact["deadline_shape"] != "absent"),
        "tenders_without_body": sum(1 for fact in tenders.values() if not fact["body_chars"]),
        "tenders_without_cpv": sum(1 for fact in tenders.values() if not fact["cpv"]),
        "tenders_cpv_passing": sum(1 for fact in tenders.values() if fact["cpv_passes"]),
        "resume_objet_at_cap": sum(1 for fact in tenders.values() if fact["resume_objet_chars"] >= 199),
    }


def select(facts: dict[str, dict]) -> dict[str, list[str]]:
    """The notice files the fixture carries, and why each one is in it.

    One pick per shape the census found, in a fixed order so re-recording the same
    day produces the same fixture. Every clause is a shape a parser can get wrong:
    a format's own title and CPV paths, a nature the registry excludes, a deadline
    the shared parser cannot read, an absent deadline, an absent body, an absent
    classification, a notice that reaches the lexicon, and a truncated
    `RESUME_OBJET`.
    """
    reasons: dict[str, list[str]] = {}

    def take(name: str | None, reason: str) -> None:
        if name is not None:
            reasons.setdefault(name, []).append(reason)

    ordered = sorted(facts)

    def first(predicate) -> str | None:
        return next((name for name in ordered if predicate(facts[name])), None)

    for pair in sorted({(fact["format"], fact["nature"]) for fact in facts.values()}):
        take(
            first(lambda fact, pair=pair: (fact["format"], fact["nature"]) == pair),
            f"format and nature {pair[0]}/{pair[1]}",
        )
    for found in sorted({fact["deadline_shape"] for fact in facts.values()}):
        take(
            first(lambda fact, found=found: fact["deadline_shape"] == found and fact["nature"] == TENDER),
            f"deadline shape {found}",
        )
    for found in sorted({fact["format"] for fact in facts.values()}):

        def tender_of(fact, found=found) -> bool:
            return fact["format"] == found and fact["nature"] == TENDER

        take(
            first(lambda fact, of=tender_of: of(fact) and not fact["body_chars"]),
            f"{found} tender with no description",
        )
        take(
            first(lambda fact, of=tender_of: of(fact) and not fact["cpv"]),
            f"{found} tender with no CPV code",
        )
        take(
            first(lambda fact, of=tender_of: of(fact) and fact["cpv_passes"]),
            f"{found} tender whose CPV passes the registry prefixes",
        )
    take(
        first(lambda fact: fact["nature"] == TENDER and fact["resume_objet_chars"] >= 199),
        "RESUME_OBJET at its 200-character cap",
    )
    take(
        max(ordered, key=lambda name: facts[name]["departements"]),
        "the most DEP_PUBLICATION entries of the day",
    )
    return {name: reasons[name] for name in sorted(reasons)}


def report(fixture: dict, facts: dict[str, dict]) -> None:
    """Print the census, so the parsers are written against counts and not guesses."""
    counts = fixture["census"]
    print(f"wrote {FIXTURE.relative_to(REPO)} ({FIXTURE.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"day:      {fixture['day']}")
    for key, value in counts.items():
        print(f"{key + ':':<26} {value}")
    print(f"selected: {len(fixture['selection'])} of {counts['files']} files")
    for name, reasons in fixture["selection"].items():
        print(f"  {name:<16} {facts[name]['format']}/{facts[name]['nature']:<12} {'; '.join(reasons)}")


if __name__ == "__main__":
    raise SystemExit(main())
