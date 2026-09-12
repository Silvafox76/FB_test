"""BUILD_ORDER step 24: the translation sample two human readers check by hand.

Writes `reports/translation_sample_<date>.csv`: one row per notice, the original text
beside the English rendering the pipeline produced, a link to the notice, and two empty
columns the reader fills in. Nothing here calls a model or writes to the database.

WHAT THE STEP ASKS FOR AND WHAT THE CORPUS CAN ACTUALLY SUPPLY. Step 24 names French,
German, Ukrainian, Arabic and Albanian. Measured on 2026-09-12, three of those five
cannot be sampled at all, and the reasons are worth knowing before anyone books a
reader's afternoon:

  French   ZERO machine translations exist, and this is structural rather than a gap in
           the corpus. French has its own lexicon, so `monitor/filter/run.py` filters
           French notices natively and they never reach the translate stage. The 108
           English renderings on French notices are all stamped `ted-eforms` /
           `source-native` - they are TED's own translations, not ours, and handing
           those to a reader would be checking TED's work. This is decision 46 showing
           up as a measurement.
  Arabic   No source in `sources/*.yaml` publishes in Arabic. There is nothing to sample
           and there will not be until one is onboarded.
  Albanian One source publishes in Albanian, `sources/kosovo.yaml`, and it is disabled
           and blocked on a copyright clause a person has to clear (decision 27).

So the sample is drawn from what the translator has actually produced. German is the
deep one at 321 notices; Ukrainian is thin at 10 and all of them are taken; the rest of
the quota goes to the next-largest languages so a reader sees more than one source's
house style. The languages and counts land in the CSV, so nobody has to take this
docstring's word for what is in the file.
"""

from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import UTC, datetime

import psycopg

from monitor.registry.load import REPO

SAMPLE_SIZE = 30
OUT_DIR = REPO / "reports"

# How the 30 are allocated, and it is weighted by WHO READS THEM rather than split
# evenly. Step 24 arranges a French reader and a German reader for two hours each. The
# French reader has nothing to read - see the docstring - so German is the only language
# in this sample a human is booked to check properly, and it gets the largest share.
# Ukrainian takes every notice there is, all 10, because the step asks for it and there
# are no more; it is checked by back-translation rather than by a human this round. The
# remaining six spread across the next-largest languages so the German reader's sense of
# what the translator does is not formed from one publisher's house style.
ALLOCATION = {"de": 14, "uk": 10, "pl": 2, "es": 2, "nl": 2}

COLUMNS = [
    "notice_id",
    "language",
    "source_id",
    "country",
    "url",
    "original_title",
    "english_title",
    "original_body",
    "english_body",
    "model",
    "prompt_version",
    "verdict",  # the reader writes: ok / wrong / unsure
    "what_is_wrong",  # and why, in their own words
]

# `distinct on` because `translations` is keyed on (notice_id, prompt_version) and a
# notice can carry both a source-native rendering and a machine one. Taking the latest
# without narrowing would put a TED rendering in the sample and call it ours.
ROWS = """
    with rendering as (
        select distinct on (t.notice_id) t.notice_id, t.title_en, t.body_en, t.model, t.prompt_version
        from translations t
        order by t.notice_id, t.created_at desc, t.prompt_version
    )
    select n.id::text, n.language, n.source_id, n.country, n.url, n.title,
           r.title_en, coalesce(n.body, ''), coalesce(r.body_en, ''), r.model, r.prompt_version
    from notices n
    join rendering r on r.notice_id = n.id
    where r.prompt_version <> 'source-native'
    order by n.language, md5(n.id::text)
"""

BODY_CHARS = 1200  # enough for a reader to judge the rendering, short enough to read


def main() -> int:
    with psycopg.connect(os.environ["DATABASE_URL_READONLY"]) as conn:
        rows = conn.execute(ROWS).fetchall()

    by_language: dict[str, list] = {}
    for row in rows:
        by_language.setdefault(row[1], []).append(row)

    # Take every Ukrainian notice there is, then fill from the priority order, then from
    # whatever else the translator has produced. Deterministic: the query orders on
    # md5(id), so the same corpus yields the same sample and a second reader gets the
    # same file rather than a fresh draw.
    chosen: list = []
    short: dict[str, int] = {}
    for language, want in ALLOCATION.items():
        have = by_language.get(language, [])
        chosen.extend(have[:want])
        if len(have) < want:
            short[language] = want - len(have)

    # A language that could not fill its share gives the remainder to the next-largest
    # one rather than leaving the sample short, and says so on the way past: a reader
    # asked for 30 should get 30, and whoever booked the session should know the mix
    # moved.
    if len(chosen) < SAMPLE_SIZE:
        spare = sorted(
            (lang for lang in by_language if lang not in ALLOCATION),
            key=lambda lang: -len(by_language[lang]),
        )
        for language in spare:
            if len(chosen) >= SAMPLE_SIZE:
                break
            chosen.extend(by_language[language][: SAMPLE_SIZE - len(chosen)])

    if not chosen:
        raise SystemExit(
            "no machine translations in the database. Run 'make translate' first; note that "
            "French notices never reach the translator because French has its own lexicon."
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"translation_sample_{datetime.now(UTC).date().isoformat()}.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for notice_id, lang, source, country, url, title, title_en, body, body_en, model, version in chosen:
            writer.writerow(
                [
                    notice_id,
                    lang,
                    source,
                    country,
                    url,
                    title,
                    title_en,
                    body[:BODY_CHARS],
                    body_en[:BODY_CHARS],
                    model,
                    version,
                    "",
                    "",
                ]
            )

    counts = Counter(row[1] for row in chosen)
    if short:
        print("short of the allocation: " + ", ".join(f"{lang} by {n}" for lang, n in short.items()))
    print(f"wrote {path.relative_to(REPO)} with {len(chosen)} notices")
    print("languages: " + ", ".join(f"{lang} {n}" for lang, n in counts.most_common()))
    print(
        "available to sample, by language: "
        + ", ".join(f"{lang} {len(rows_)}" for lang, rows_ in sorted(by_language.items(), key=lambda kv: -len(kv[1])))
    )
    print(
        "\nNot in this file and why (step 24 names all three):\n"
        "  French   - no machine translation exists. French has its own lexicon, so French notices are\n"
        "             filtered natively and never reach the translate stage. Every English rendering on\n"
        "             a French notice is TED's own (`source-native`), not ours.\n"
        "  Arabic   - no source in the registry publishes in Arabic.\n"
        "  Albanian - only sources/kosovo.yaml does, and it is disabled and legally blocked (decision 27)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
