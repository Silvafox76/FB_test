"""Measure a candidate lexicon phrase against the corpus the filter ACTUALLY reads.

    uv run python scripts/probe_phrase.py en "financial management software" "payroll system"
    uv run python scripts/probe_phrase.py fr "conformité réglementaire"

Use this instead of writing your own query. The corpus is not `notices where language='en'`:
`monitor/filter/run.py` matches a notice's own language when a lexicon exists (en, fr), and
every other language is translated and then re-filtered against the ENGLISH lexicon, so the
English corpus is 996 notices, not the 170 that are natively English. A phrase measured the
wrong way looks like it matches nothing when it matches the highest-scoring notice we hold.

Reports, per phrase: how many notices it matches, the best and mean scorer relevance among
them, and how many would clear the staging threshold of 60. A replacement that reports 0
stagers where the phrase it replaces had 1 is a recall regression.
"""

from __future__ import annotations

import os
import sys

import psycopg

from monitor.filter.lexicon import _pattern, canonical

ROWS = """
 with rendering as (select distinct on (t.notice_id) t.notice_id, t.title_en, t.body_en
                    from translations t order by t.notice_id, t.created_at desc, t.prompt_version),
      cur as (select distinct on (s.notice_id) s.notice_id, s.relevance from scores s
              order by s.notice_id, s.created_at desc, s.id desc)
 select n.language, n.title, coalesce(n.body,''), r.title_en, coalesce(r.body_en,''), cur.relevance
 from notices n left join rendering r on r.notice_id = n.id left join cur on cur.notice_id = n.id
"""


def corpus():
    out = {"en": [], "fr": []}
    with psycopg.connect(os.environ["DATABASE_URL_READONLY"]) as c:
        for lang, t, b, te, be, rel in c.execute(ROWS).fetchall():
            if lang in ("en", "fr"):
                out[lang].append((canonical(f"{t}\n{b}"), rel, t))
            elif te:
                out["en"].append((canonical(f"{te}\n{be}"), rel, te))
    return out


def main(lang, *phrases):
    pool = corpus()[lang]
    print(f"corpus: {len(pool)} notices the {lang} lexicon reads\n")
    for p in phrases:
        pat = _pattern(p)
        hits = [(r, t) for hay, r, t in pool if pat.search(hay)]
        scored = [r for r, _ in hits if r is not None]
        stagers = [(r, t) for r, t in hits if r is not None and r >= 60]
        best = max(scored) if scored else None
        mean = round(sum(scored) / len(scored)) if scored else None
        print(f"{p!r}: {len(hits)} hits, best={best}, mean={mean}, would-stage={len(stagers)}")
        for r, t in sorted(hits, key=lambda x: -(x[0] or -1))[:4]:
            print(f"     [{r if r is not None else ' -'}] {t[:100]}")
        print()


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
