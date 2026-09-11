"""Command line entry point.

argparse rather than typer: one fewer dependency, and the command surface is
five verbs that do not need a framework.

Every command here is a stub until the step in BUILD_ORDER.md that builds it
lands. A stub exits 2 and says so; it never returns 0 having done nothing.
"""

from __future__ import annotations

import argparse
import os
import sys

NOT_IMPLEMENTED_EXIT = 2

# Command -> the BUILD_ORDER.md step that implements it. Kept here so a stub can
# say what is missing rather than only that something is.
IMPLEMENTED_BY: dict[str, str] = {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="PFM Opportunity Monitor: read notices, score them, stage candidates for a named reviewer.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch", help="run one connector once, or all of them")
    fetch.add_argument("source", help="source id from sources/*.yaml, or 'all'")

    subparsers.add_parser("filter", help="run the free filter over every notice not yet filtered")
    translate = subparsers.add_parser(
        "translate", help="translate notices held for want of a lexicon, then re-filter them"
    )
    translate.add_argument("--limit", type=int, default=0, help="stop after this many notices (0 = all)")
    score = subparsers.add_parser("score", help="score every notice that survived the filter")
    score.add_argument("--limit", type=int, default=0, help="stop after this many notices (0 = all)")
    subparsers.add_parser("stage", help="dedupe scored notices into candidates and stage them for review")
    subparsers.add_parser("run", help="one full pass: fetch all, filter, score, dedupe, stage")
    subparsers.add_parser("status", help="source health, today's calls and cost, queue depth, export backlog")
    golden = subparsers.add_parser(
        "golden", help="precision, recall and schema validity for the current prompt version"
    )
    golden.add_argument(
        "--export",
        action="store_true",
        help="write the unlabelled golden set and stop; a person labels it, not the pipeline",
    )

    return parser


def model_client():
    """The Anthropic client, or a message a person can act on.

    The SDK raises a TypeError from inside its own constructor when no credential
    is resolvable, which is loud but not legible: in the middle of `monitor run` it
    reads as a bug in the pipeline. This says what is missing and what to do.
    """
    import anthropic

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError(
            "no model credential: set ANTHROPIC_API_KEY in .env. "
            "Every model call is capped and logged, so nothing runs without one."
        )
    return anthropic.Anthropic()


def run_fetch(source_id: str) -> int:
    """Run one connector once and report seen, new and failed."""
    from monitor.db import connect
    from monitor.fetch import fetch

    with connect("pipeline") as conn:
        results = fetch(conn, source_id)

    failed = 0
    for result in results:
        if result.failed:
            failed += 1
            print(f"{result.source_id}: FAILED {result.error}", file=sys.stderr)
        else:
            print(f"{result.source_id}: seen {result.seen}, new {result.new}")

    print(f"seen {sum(r.seen for r in results)}, new {sum(r.new for r in results)}, failed {failed}")
    return 1 if failed else 0


def run_filter() -> int:
    """Run the free filter and report what it kept and what it threw away."""
    from monitor.db import connect
    from monitor.filter.run import run

    with connect("pipeline") as conn:
        counts = run(conn)

    print(
        f"considered {counts.considered}, passed {counts.passed}, "
        f"dropped {counts.dropped} (cpv {counts.dropped_cpv}, lexicon {counts.dropped_lexicon}), "
        f"needs translation {counts.needs_translation}"
    )
    if counts.considered:
        print(f"drop rate {counts.drop_rate:.1%}")
    return 0


def run_translate(limit: int) -> int:
    """Translate what the filter held, then put it back through the filter."""
    from monitor.db import connect
    from monitor.translate.run import run

    client = model_client()
    with connect("pipeline") as conn:
        counts = run(conn, client, limit=limit)

    print(
        f"considered {counts.considered}, translated {counts.translated}, "
        f"passed {counts.passed}, dropped {counts.dropped}, parked {counts.parked}"
    )
    if counts.flagged:
        print(f"flagged for a dropped system name: {counts.flagged}")
    print(f"cost USD {counts.cost_usd:.4f}")
    return 0


def run_score(limit: int) -> int:
    """Score what the filter passed and report schema validity."""
    from monitor.db import connect
    from monitor.score.run import run

    client = model_client()
    with connect("pipeline") as conn:
        counts = run(conn, client, limit=limit)

    print(f"considered {counts.considered}, scored {counts.scored}, parked {counts.parked}")
    if counts.considered:
        print(f"schema validity {counts.schema_validity:.1%}")
    print(f"cost USD {counts.cost_usd:.4f}, prompt_version {counts.prompt_version}")
    return 0


def run_golden(export_only: bool) -> int:
    """Export the set for labelling, or measure against the labels a person wrote."""
    from monitor.db import connect
    from monitor.golden import GOLDEN_CSV, HISTORY_CSV, NotLabelled, append_history, export, render
    from monitor.golden import run as run_golden_set

    if export_only:
        with connect("pipeline") as conn:
            rows = export(conn)
        print(f"wrote {GOLDEN_CSV} with {rows} rows and an empty label column.")
        print("Label each row 'relevant' or 'not' by hand, then run 'make golden'.")
        print("The pipeline does not label its own golden set: it would be measuring its own opinion.")
        return 0

    client = model_client()
    with connect("pipeline") as conn:
        try:
            result = run_golden_set(conn, client)
        except NotLabelled as error:
            print(f"golden set not ready: {error}", file=sys.stderr)
            return 2

    print(render(result))
    append_history(result)
    print(f"appended to {HISTORY_CSV}")
    return 0


def run_stage() -> int:
    """Dedupe scored notices into candidates, then stage what clears the bar."""
    from monitor.db import connect
    from monitor.stage.stager import run

    with connect("pipeline") as conn:
        counts = run(conn)

    print(
        f"scored notices {counts.scored_notices}, candidates created {counts.candidates_created}, "
        f"joined {counts.joined}"
    )
    print(
        f"staged {counts.staged}, held by the per-source cap {counts.held_by_cap}, "
        f"below threshold {counts.below_threshold}"
    )
    return 0


def run_full_pass() -> int:
    """fetch all, filter, score, dedupe, stage. Stops at the first stage that fails.

    Each stage commits its own work, so a pass that stops at the scorer keeps what
    was fetched and filtered. That is the point of stopping rather than unwinding:
    the next pass picks up where this one left off.
    """
    for name, step in (
        ("fetch", lambda: run_fetch("all")),
        ("filter", run_filter),
        ("score", lambda: run_score(0)),
        ("stage", run_stage),
    ):
        print(f"\n== {name} ==")
        try:
            code = step()
        except RuntimeError as error:
            print(f"run stopped at {name}: {error}", file=sys.stderr)
            return 1
        if code != 0:
            print(f"run stopped at {name}", file=sys.stderr)
            return code
    return 0


def run_status() -> int:
    """Source health and the filter's arithmetic, per source."""
    from monitor.db import connect
    from monitor.health.status import collect, render

    with connect("pipeline") as conn:
        print(render(collect(conn)))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "fetch":
        return run_fetch(args.source)
    if args.command == "filter":
        return run_filter()
    if args.command == "translate":
        return run_translate(args.limit)
    if args.command == "score":
        return run_score(args.limit)
    if args.command == "golden":
        return run_golden(args.export)
    if args.command == "stage":
        return run_stage()
    if args.command == "run":
        return run_full_pass()
    if args.command == "status":
        return run_status()

    print(f"monitor {args.command}: not implemented, arrives in {IMPLEMENTED_BY[args.command]}", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT  # pragma: no cover - every command is implemented


if __name__ == "__main__":
    raise SystemExit(main())
