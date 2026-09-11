"""Command line entry point.

argparse rather than typer: one fewer dependency, and the command surface is
five verbs that do not need a framework.

Every command here is a stub until the step in BUILD_ORDER.md that builds it
lands. A stub exits 2 and says so; it never returns 0 having done nothing.
"""

from __future__ import annotations

import argparse
import sys

NOT_IMPLEMENTED_EXIT = 2

# Command -> the BUILD_ORDER.md step that implements it. Kept here so a stub can
# say what is missing rather than only that something is.
IMPLEMENTED_BY = {
    "stage": "step 8 (dedupe, candidates, stager)",
    "golden": "step 7 (mini golden set)",
}


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
    subparsers.add_parser("status", help="source health, today's calls and cost, queue depth, export backlog")
    subparsers.add_parser("golden", help="precision, recall and schema validity for the current prompt version")

    return parser


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
    import anthropic

    from monitor.db import connect
    from monitor.translate.run import run

    client = anthropic.Anthropic()
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
    import anthropic

    from monitor.db import connect
    from monitor.score.run import run

    client = anthropic.Anthropic()
    with connect("pipeline") as conn:
        counts = run(conn, client, limit=limit)

    print(f"considered {counts.considered}, scored {counts.scored}, parked {counts.parked}")
    if counts.considered:
        print(f"schema validity {counts.schema_validity:.1%}")
    print(f"cost USD {counts.cost_usd:.4f}, prompt_version {counts.prompt_version}")
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
    if args.command == "status":
        return run_status()

    print(f"monitor {args.command}: not implemented, arrives in {IMPLEMENTED_BY[args.command]}", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
