"""Drill 1: the model credential is refused mid-run. The run stops and writes nothing.

    uv run python scripts/drills/drill1_invalid_api_key.py

**What it proves.** Rules 2 and 3: there is no retry around the model client and no
`try/except` that turns a refused credential into a quiet skip, so the failure raises out
of the score stage and the run stops. Rule 4: it stops loudly, with a non-zero exit.
And the property BUILD_ORDER step 11 asks about - no partial writes: no `scores` row, no
`model_calls` row, no notice whose status moved.

**How the failure is injected.** `ANTHROPIC_API_KEY` is set, for this one subprocess only,
to a credential the API refuses, and `MODEL_ROUTE=direct` so that credential is the one
the client sends (`monitor/cli.py:model_client`). The request is real: it leaves the host,
reaches api.anthropic.com and comes back 401 `authentication_error`. Nothing is mocked and
nothing is monkeypatched, so what the drill watches is the pipeline's own failure path.

**What it cannot reach, said plainly.** There is no valid model credential in this
environment, so the drill cannot show a run that scored three notices and lost its
credential at the fourth. The failure lands on the first call of the run. That still
proves the claim the runbook makes - the stage raises, stops and leaves nothing behind -
because the scorer commits per notice (`monitor/score/run.py`) and the writes that would
be partial are all downstream of the call that fails. A run that failed at its fourth
call would leave three committed scores and a fourth notice untouched, which is the same
invariant one notice further along.

**One wording correction the runbook should carry.** Step 11 words this drill's outcome as
"notices parked". The system does not park on a refused credential and should not: in this
codebase `notices.status = 'parked'` means the model answered twice and failed validation
twice (`monitor/score/run.py:_park`), which is a notice a person has to look at. A refused
credential is not the notice's fault, so the notice stays at `filtered_in` and the next run
picks it up once the key is fixed, with no un-parking step for anybody to remember. This
drill asserts that behaviour rather than the wording.

**What it changes.** One notice at `filtered_in`, so the score stage always has exactly
one call to attempt and the drill does not depend on what happens to be in the queue when
it is run. It is removed on the way out. The subprocess the drill starts writes nothing,
which is the whole point.

**Run it on a quiet system.** A scheduled `monitor run` overlapping this drill would add
`model_calls` and `scores` rows of its own, and the two counts below would move for a
reason that has nothing to do with the credential.
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

REPO = Path(__file__).resolve().parents[2]

# What "nothing was written" means, as one row of counts read before and after.
SNAPSHOT = """
    select (select count(*) from notices where status = 'filtered_in'),
           (select count(*) from notices where status = 'scored'),
           (select count(*) from notices where status = 'parked'),
           (select count(*) from scores),
           (select count(*) from model_calls),
           (select count(*) from model_calls where at >= date_trunc('day', now()))
"""
LABELS = ("notices filtered_in", "notices scored", "notices parked", "scores rows", "model_calls", "calls today")

# The markers that say the API refused the credential rather than something else refusing
# something else. The SDK raises `anthropic.AuthenticationError` and the CLI's traceback
# carries the API's own message with it.
AUTH_MARKERS = ("AuthenticationError", "authentication_error", "invalid x-api-key", "401")


def snapshot(conn) -> tuple[int, ...]:
    return tuple(conn.execute(SNAPSHOT).fetchone())


def differences(before: tuple[int, ...], after: tuple[int, ...]) -> list[str]:
    return [f"{label}: {was} -> {now}" for label, was, now in zip(LABELS, before, after, strict=True) if was != now]


def main() -> int:
    drill = Drill(
        1,
        "a refused model credential stops the run and writes nothing",
        "rules 2, 3 and 4: no retry, no try/except around the model client, fail loudly",
        "monitor score exits non-zero on a 401; no scores row, no model_calls row, no status moved",
    )

    with owner() as owner_conn, filtered_in_notice(owner_conn) as notice_id:
        # Read as monitor_readonly: the drill inspects, and the reporting role cannot write
        # even by accident (rule 11).
        with connection("DATABASE_URL_READONLY") as reader:
            require_quiet_pipeline(reader)
            before = snapshot(reader)

        drill.note(f"seeded notice {notice_id} at filtered_in, with {before[0]} notices waiting in all")
        drill.note(f"{before[5]} model calls recorded today; the scorer takes the oldest notice first")

        completed = subprocess.run(
            [sys.executable, "-m", "monitor.cli", "score", "--limit", "1"],
            cwd=REPO,
            env=os.environ | {"MODEL_ROUTE": "direct", "ANTHROPIC_API_KEY": INVALID_CREDENTIAL},
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        output = completed.stdout + completed.stderr

        with connection("DATABASE_URL_READONLY") as reader:
            after = snapshot(reader)
            status = reader.execute("select status from notices where id = %s", (notice_id,)).fetchone()[0]
            its_scores = reader.execute("select count(*) from scores where notice_id = %s", (notice_id,)).fetchone()[0]

        drill.check(
            "the run stopped: monitor score exited non-zero",
            completed.returncode != 0,
            f"exit status {completed.returncode}",
        )

        refusal = [line for line in output.splitlines() if any(marker in line for marker in AUTH_MARKERS)]
        if not drill.check(
            "it stopped because the credential was refused, and not for some other reason",
            bool(refusal),
            refusal[-1].strip() if refusal else "no authentication failure in the output",
        ):
            drill.note(
                "no 401 in the output means the request did not reach api.anthropic.com. The write "
                "checks below still hold, but the credential half of this drill is unproven: check "
                "egress with 'uv run python scripts/check_egress.py' and read the output above."
            )

        drill.check(
            "the notice the run reached is still filtered_in, so the next run picks it up",
            status == "filtered_in",
            f"status {status!r} rather than 'parked'; this file's docstring says why",
        )
        drill.check("it has no score of its own", its_scores == 0, f"scores for {notice_id}: {its_scores}")

        print("\n  what the run said, verbatim:")
        drill.show(output.strip())

        # Everything above is attributable to this run. The counts below are the whole
        # database's, so they are only evidence on a quiet system.
        require_no_concurrent_calls(before[4], after[4])
        drill.check(
            "nothing was written anywhere: no model call, no score, no status moved",
            after == before,
            "; ".join(differences(before, after)) or "every count identical, including today's billed calls",
        )

    return drill.verdict()


if __name__ == "__main__":
    raise SystemExit(run(main))
