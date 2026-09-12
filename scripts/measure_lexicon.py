"""Before/after measurement for a lexicon change, without needing labels.

    uv run python scripts/measure_lexicon.py OLD_EN OLD_FR NEW_EN NEW_FR

Point it at two pairs of lexicon files - typically `git show HEAD:config/lexicon_en.yaml`
written to a temp path, and the working copy. Exit status is non-zero if any notice the
scorer rated at or above the staging threshold would stop passing, so it can gate a commit.

This exists because a lexicon edit is otherwise unfalsifiable. `monitor/golden.py` is
explicit that precision and recall are reported and never tuned toward, and the golden set
has no labels yet, so there is no precision number to move. What CAN be established without
labels is that a change drops no notice the scorer itself rated highly - which is what made
the 2026-09-12 change (decision 44) a defect fix rather than a matter of taste.

WHICH TEXT THE FILTER ACTUALLY READS, because the first version of this script got it
wrong and every number it produced was too small. `monitor/filter/run.py` matches the
lexicon for the notice's OWN language when one exists (en, fr). A notice in any other
language is parked for translation, and `monitor/translate/run.py` then re-filters it on
`title_en`/`body_en` with the ENGLISH lexicon - which is why `filter_result` reads
"translated, cpv 48, lexicon en: compliance". Measuring only `language in ('en','fr')`
therefore misses most of the corpus: 826 notices reach the English lexicon by that route.

The translations join is `distinct on (notice_id)`, because `translations` is keyed on
(notice_id, prompt_version) and a plain join returns a TED notice twice - the same fan-out
already fixed in the scorer, the stager and the golden export.

Three questions, in increasing order of how much they matter:
  1. How many notices pass the free filter before and after?
  2. WHICH notices stop passing? Every one must be inspectable junk.
  3. Does any notice the SCORER rated highly stop passing?

(3) is the regression test. Nobody has labelled the golden set, so the scorer's own
relevance is the only independent signal available about whether a notice mattered, and a
notice the model scored at or above the staging threshold that the free filter would now
drop before any model call is a recall loss with evidence behind it, not a matter of taste.
"""

from __future__ import annotations

import os
import sys

import psycopg
import yaml

from monitor.filter.lexicon import _pattern, canonical

ROWS = """
    with rendering as (
        select distinct on (t.notice_id) t.notice_id, t.title_en, t.body_en
        from translations t
        order by t.notice_id, t.created_at desc, t.prompt_version
    ),
    cur as (
        select distinct on (s.notice_id) s.notice_id, s.relevance
        from scores s order by s.notice_id, s.created_at desc, s.id desc
    )
    select n.id::text, n.language, n.title, coalesce(n.body, ''),
           r.title_en, coalesce(r.body_en, ''), n.source_id, n.filter_result, cur.relevance
    from notices n
    left join rendering r on r.notice_id = n.id
    left join cur on cur.notice_id = n.id
"""


def load(path):
    return {f: list(ps) for f, ps in yaml.safe_load(open(path, encoding="utf-8"))["functions"].items()}


def hits(text, by_function):
    return [p for _f, ps in by_function.items() for p in ps if _pattern(p).search(text)]


def readable(language, title, body, title_en, body_en):
    """The text and lexicon the filter would actually use, or None if it never reaches one."""
    if language in ("en", "fr"):
        return language, canonical(f"{title}\n{body}")
    if title_en:  # translated, then re-filtered against the English lexicon
        return "en", canonical(f"{title_en}\n{body_en}")
    return None, None  # parked for translation; the lexicon has never seen it


def main(before_en, before_fr, after_en, after_fr):
    sets = {
        "before": {"en": load(before_en), "fr": load(before_fr)},
        "after": {"en": load(after_en), "fr": load(after_fr)},
    }

    with psycopg.connect(os.environ["DATABASE_URL_READONLY"]) as c:
        rows = c.execute(ROWS).fetchall()

    lost, gained, seen = [], [], 0
    before = after = 0
    for _id, lang, title, body, t_en, b_en, _src, _filter_result, rel in rows:
        use, text = readable(lang, title, body, t_en, b_en)
        if use is None:
            continue
        seen += 1
        b = hits(text, sets["before"][use])
        a = hits(text, sets["after"][use])
        before += bool(b)
        after += bool(a)
        if b and not a:
            lost.append((rel, title[:105], use, sorted(set(b))))
        if a and not b:
            gained.append((rel, title[:105], use, sorted(set(a))))

    print(f"notices the lexicon actually reads: {seen} (of {len(rows)} held)")
    print(f"pass BEFORE: {before}")
    print(f"pass AFTER:  {after}    ({after - before:+d})")
    print(f"  lost {len(lost)}, gained {len(gained)}\n")

    scored_lost = [x for x in lost if x[0] is not None]
    high = [x for x in scored_lost if x[0] >= 60]
    band = [x for x in scored_lost if 40 <= x[0] < 60]
    print("=== REGRESSION CHECK: scored notices that would now be dropped ===")
    print(f"  scored >= 60 (would have been staged): {len(high)}   <-- must be 0")
    print(f"  scored 40-59 (rescore band):           {len(band)}")
    for r, t, u, p in sorted(scored_lost, key=lambda x: -x[0])[:10]:
        print(f"    score {r:3} [{u}] {t}\n              kept by {p}")

    kept_high = [x for x in gained if x[0] is not None and x[0] >= 60]
    if kept_high:
        print(f"\n  newly caught, scored >= 60: {len(kept_high)}")
        for r, t, u, p in kept_high:
            print(f"    score {r:3} [{u}] {t}\n              now caught by {p}")

    print(f"\n=== notices that stop passing ({len(lost)}) ===")
    for r, t, u, p in lost[:60]:
        print(f"  [{r if r is not None else '  -'}] [{u}] {t}\n        via {p}")
    if len(lost) > 60:
        print(f"  ... and {len(lost) - 60} more")

    print(f"\n=== notices that start passing ({len(gained)}) ===")
    for r, t, u, p in gained[:30]:
        print(f"  [{r if r is not None else '  -'}] [{u}] {t}\n        via {p}")

    return 1 if high else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:5]))
