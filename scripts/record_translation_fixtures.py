"""Record the translation contract fixtures from real calls.

Step 14's acceptance asks for a French, a German and one other-language fixture
translating with schema validity above 98 percent. That number can only come from
real responses, so this records them the same way the connector fixtures are
recorded: one real call each, saved as it came back, and the tests replay them.

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile) and a database, because
every call is capped and logged like any other (rule 22).

    ANTHROPIC_API_KEY=... uv run python scripts/record_translation_fixtures.py

Writes tests/contract/fixtures/translate_<lang>.json, one per notice, each holding
the original text, the model's response and whether any system name was dropped.
The notices are taken from the database - real ones, in the language concerned, with
a body - so the fixtures are what the pipeline actually meets rather than invented
text. Selected by language and not by current status: the first run of this script
found nothing, because the translation stage had already processed all 662 held
notices and moved them on. The notice's text is the same text whatever status it now
carries, and requiring it to be *awaiting* translation only meant the fixtures could
be recorded in the window before the stage first ran and never afterwards.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic
import psycopg

from monitor.translate.client import SchemaError, dropped_acronyms, system_prompt, translate
from monitor.translate.run import prompt_version

REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "tests" / "contract" / "fixtures"

# The languages step 14's acceptance names, plus Ukrainian because Prozorro is a
# wave-1 source and Cyrillic is where acronym preservation is most at risk.
WANTED = ("fr", "de", "uk", "pl", "es")
PER_LANGUAGE = 1


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; a translation fixture comes from a real call", file=sys.stderr)
        return 2

    url = os.environ.get("DATABASE_URL_PIPELINE")
    if not url:
        print("DATABASE_URL_PIPELINE is not set; every model call is capped and logged (rule 22)", file=sys.stderr)
        return 2

    client = anthropic.Anthropic()
    # The prompt is built from config/system_names.yaml at call time, so the version
    # is hashed from the prompt as it actually goes out rather than from a constant.
    version = prompt_version(system_prompt())
    written = 0

    with psycopg.connect(url) as conn:
        for language in WANTED:
            rows = conn.execute(
                """
                select external_id, title, coalesce(body, '')
                from notices
                where language = %s and coalesce(body, '') <> ''
                order by length(coalesce(body, '')) desc, fetched_at
                limit %s
                """,
                (language, PER_LANGUAGE),
            ).fetchall()
            if not rows:
                print(f"{language}: no held notice in the database, skipping")
                continue

            for external_id, title, body in rows:
                try:
                    result = translate(conn, client, language=language, title=title, body=body, prompt_version=version)
                except SchemaError as error:
                    print(f"{language}: {external_id} failed validation twice: {error}", file=sys.stderr)
                    continue

                path = FIXTURES / f"translate_{language}.json"
                path.write_text(
                    json.dumps(
                        {
                            "recorded_from": external_id,
                            "language": language,
                            "prompt_version": version,
                            "model": result.model,
                            "original": {"title": title, "body": body},
                            "response": {
                                "title_en": result.output.title_en,
                                "body_en": result.output.body_en,
                            },
                            "attempts": result.attempts,
                            "dropped_acronyms": result.dropped_acronyms,
                        },
                        indent=2,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                written += 1
                flag = f"  DROPPED {result.dropped_acronyms}" if result.dropped_acronyms else ""
                print(f"{language}: wrote {path.name}, {result.attempts} attempt(s){flag}")
                # Sanity: the check the acceptance test exercises, on real output.
                assert result.dropped_acronyms == dropped_acronyms(
                    f"{title}\n{body}", f"{result.output.title_en}\n{result.output.body_en}"
                )
        conn.commit()

    print(f"\n{written} fixture(s) written to {FIXTURES.relative_to(REPO)}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
