"""Drill 3: the daily call cap stops the run before the call, not after it.

    uv run python scripts/drills/drill3_call_cap.py

**What it proves.** Rule 22: every model call is capped and the cap is checked *before* the
request. With the cap at 2 and two calls already recorded today, the third call does not
happen: `monitor/caps.py:check` raises `CapExceeded`, `monitor/score/run.py` commits what
it had, logs `score_cap_exceeded` and re-raises, and the run stops with a non-zero exit.
Rule 1 as well - the cap is one guard shared by every purpose that calls a model, not one
per caller - and rule 6, because the number lives in `config/thresholds.yaml` with an
environment override and not in a `.py` file.

**Why this drill needs no model credential at all, and why that is the strongest part of
it.** The cap is checked before the client is asked for anything, so a capped run makes no
request. The drill proves that by putting a deliberately invalid credential into the run's
environment: if the cap check had let the call through, the output would carry a 401 from
api.anthropic.com. It does not. No request, no 401, no `model_calls` row.

**How "two calls already today" is arranged.** Two rows are inserted into `model_calls` as
`monitor_pipeline`, the role that records real calls, so today's count is exactly the cap.
They stand in for two calls the scorer would have made, and they are removed on the way
out. If the day already has calls of its own the drill uses them: the cap is set to today's
count, so the next call is always the one over the line and the drill never needs to delete
a real row. The dollar cap is lifted above today's spend for the duration, so the stop
under test is unambiguously the call cap - `check` looks at calls before dollars anyway,
but a drill that could stop for either reason proves neither.

**The control.** A drill that only watches the cap fire cannot tell a working cap from a
guard that raises on everything. So `caps.check` is called in this process twice against
the live database: at one above the cap it returns today's spend without raising, and at
the cap it raises. The subprocess then does the real thing through the CLI.

**One thing to be honest about: "with a `cap_exceeded` event".** The event is a structlog
line named `score_cap_exceeded`, carrying how many notices were scored and how many were
left. There is no row in the `events` table, and the drill checks that there is not: that
table is the audit trail for a notice, a candidate and an approved record - what the
reviewer and the week 14 gate read - and a run that stopped on its budget is an
operational fact about the run, which is what the log and `make status` are for. If the
runbook is read as promising a database row, the fix is a decision about `events`, not a
change to this drill.

**What it changes.** One notice at `filtered_in`, so the score stage has a call to reach
the cap check with, and two `model_calls` rows. All three are removed on the way out
whether the drill passed or failed, and the drill refuses to start while another pipeline
run is connected, because a run scoring notices next to it would push today's count past
the cap for a reason that is not the drill's. If the drill itself is killed, they are the rows whose `purpose` is
`drill`: `delete from model_calls where purpose = 'drill'` as the owner removes them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _drill import (
    INVALID_CREDENTIAL,
    Drill,
    connection,
    filtered_in_notice,
    owner,
    require_no_concurrent_calls,
    require_quiet_pipeline,
    run,
)

from monitor import caps

REPO = Path(__file__).resolve().parents[2]

# The cap BUILD_ORDER step 11 names. The drill tops today's count up to it rather than
# assuming the day started empty.
DRILL_CAP = 2

# Purpose and model on the seeded rows. The model name is the real one because the rows
# stand in for two real score calls; the purpose says what they are so a person who kills
# this drill halfway can find them.
SEED_PURPOSE = "drill"
SEED_MODEL = "claude-haiku-4-5"

SEED_CALL = """
    insert into model_calls (purpose, model, prompt_version, tokens_in, tokens_out, cost_usd, latency_ms)
    values (%s, %s, 'drill', 7500, 320, 0.0012, 900)
    returning id
"""


def main() -> int:
    drill = Drill(
        3,
        "the daily call cap stops the run at the call over the line",
        "rule 22: the cap is checked before every model call, and the run stops rather than trimming",
        f"DAILY_CALL_CAP={DRILL_CAP} with {DRILL_CAP} calls recorded: the next call never happens, exit non-zero",
    )

    with owner() as owner_conn, filtered_in_notice(owner_conn) as notice_id:
        with connection("DATABASE_URL_READONLY") as reader:
            require_quiet_pipeline(reader)

        with connection("DATABASE_URL_PIPELINE") as pipeline:
            spend = caps.spend_today(pipeline)
            seeds = max(0, DRILL_CAP - spend.calls)
            cap = spend.calls + seeds
            usd_cap = f"{spend.usd + 1:.2f}"
            seeded: list[int] = []

            try:
                for _ in range(seeds):
                    seeded.append(pipeline.execute(SEED_CALL, (SEED_PURPOSE, SEED_MODEL)).fetchone()[0])
                pipeline.commit()

                drill.note(f"seeded notice {notice_id} at filtered_in for the score stage to reach")
                drill.note(
                    f"{spend.calls} calls were already recorded today; the drill seeded {seeds} more, "
                    f"so the cap is set to {cap} and today's count is exactly {cap}"
                )
                drill.check(
                    "today's count is at the cap",
                    caps.spend_today(pipeline).calls == cap,
                    f"calls today {caps.spend_today(pipeline).calls}, cap {cap}",
                )

                # The control, in this process, against the live database.
                os.environ["DAILY_USD_CAP"] = usd_cap
                os.environ["DAILY_CALL_CAP"] = str(cap + 1)
                allowed = caps.check(pipeline, purpose="drill")
                drill.check(
                    "one call under the cap, the guard lets the call through",
                    allowed.calls == cap,
                    f"check returned calls={allowed.calls}, usd={allowed.usd:.4f} without raising",
                )

                os.environ["DAILY_CALL_CAP"] = str(cap)
                refused = ""
                try:
                    caps.check(pipeline, purpose="drill")
                except caps.CapExceeded as stopped:
                    refused = str(stopped)
                drill.check("at the cap, the guard refuses the next call", bool(refused), refused or "nothing raised")

                completed = subprocess.run(
                    [sys.executable, "-m", "monitor.cli", "score", "--limit", "1"],
                    cwd=REPO,
                    env=os.environ
                    | {
                        "DAILY_CALL_CAP": str(cap),
                        "DAILY_USD_CAP": usd_cap,
                        "MODEL_ROUTE": "direct",
                        "ANTHROPIC_API_KEY": INVALID_CREDENTIAL,
                    },
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                output = completed.stdout + completed.stderr

                drill.check(
                    "the run stopped: monitor score exited non-zero",
                    completed.returncode != 0,
                    f"exit status {completed.returncode}",
                )
                drill.check(
                    "it stopped on the cap, and said so: score_cap_exceeded",
                    "score_cap_exceeded" in output,
                    next((line.strip() for line in output.splitlines() if "score_cap_exceeded" in line), ""),
                )
                drill.check(
                    "the message names the cap it hit",
                    "cap is" in output,
                    next((line.strip() for line in output.splitlines() if "cap is" in line), ""),
                )
                drill.check(
                    "no request was made: the invalid credential in the run's environment was never sent",
                    "401" not in output and "authentication_error" not in output,
                    "no 401 anywhere in the output, so the cap was checked before the client",
                )
                drill.check(
                    "the notice is untouched: still filtered_in, still unscored",
                    pipeline.execute("select status from notices where id = %s", (notice_id,)).fetchone()[0]
                    == "filtered_in"
                    and pipeline.execute("select count(*) from scores where notice_id = %s", (notice_id,)).fetchone()[0]
                    == 0,
                    f"notice {notice_id}",
                )

                events = pipeline.execute(
                    "select count(*) from events where action like %s and at >= now() - interval '5 minutes'",
                    ("%cap%",),
                ).fetchone()[0]
                drill.check(
                    "the cap stop is a log line and not an events row, as this file's docstring says",
                    events == 0,
                    f"{events} events rows mentioning a cap in the last five minutes",
                )

                print("\n  what the run said, verbatim:")
                drill.show(output.strip())

                require_no_concurrent_calls(cap, caps.spend_today(pipeline).calls)
                drill.check(
                    "no call was logged and nothing was billed",
                    caps.spend_today(pipeline).calls == cap,
                    f"calls today still {cap}",
                )
            finally:
                owner_conn.execute("delete from model_calls where id = any(%s::bigint[])", (seeded,))
                left = owner_conn.execute(
                    "select count(*) from model_calls where id = any(%s::bigint[])", (seeded,)
                ).fetchone()[0]
                drill.check(
                    "the seeded calls are gone: today's budget is as the drill found it",
                    left == 0,
                    f"{len(seeded)} seeded rows removed, today's count back to {spend.calls}",
                )

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
