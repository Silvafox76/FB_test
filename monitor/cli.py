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
    metrics = subparsers.add_parser("metrics", help="measure the week's numbers and write the row the page reads")
    # 0 rather than a literal 7, so the window has one definition and it is
    # WINDOW_DAYS in monitor/health/metrics.py. Same idiom as --limit above, and it
    # keeps that module's imports out of argument parsing.
    metrics.add_argument(
        "--days", type=int, default=0, help="the window every rate is measured over (0 = the job's weekly cadence)"
    )
    golden = subparsers.add_parser(
        "golden", help="precision, recall and schema validity for the current prompt version"
    )
    golden.add_argument(
        "--export",
        action="store_true",
        help="write the unlabelled golden set and stop; a person labels it, not the pipeline",
    )

    return parser


# How the model is reached. Set explicitly; there is no detection and no falling
# back from one to the other, because which credential a deployment uses is a
# decision a person takes rather than a branch the code takes (rule 1). This is a
# route name rather than a boolean because step 12 added a third value.
#
#   direct   the key is in the environment, as ANTHROPIC_API_KEY. Local runs and
#            the pilot host.
#   proxy    the key is held by Anthropic's agent proxy on the cloud environment
#            and attached after the request leaves the sandbox. The key is never in
#            the environment, never in a log and never in a container image, which
#            is why this is the better route where it is available.
#   bedrock  the EC2 instance profile signs the request with SigV4 (appendix D:
#            `bedrock:InvokeModel` on the named models). There is no model
#            credential on the host at all, which is the point of the cutover.
#
# BUILD_ORDER step 12 calls `direct` "the recorded fallback" for two weeks after
# the cutover. It is a fallback in the operational sense - a route a person can
# switch back to - and not in the sense rule 1 forbids: nothing detects a Bedrock
# failure and nothing reroutes. Someone edits MODEL_ROUTE and restarts.
MODEL_ROUTES = ("direct", "proxy", "bedrock")
DEFAULT_MODEL_ROUTE = "direct"

# Architecture v0.4 section 6's one region, Montreal. A knob rather than a literal
# in the client constructor (rule 6), and deliberately the same variable boto3
# signs with, so a region move is one edit and there are not two values that can
# disagree about where the request went.
DEFAULT_AWS_REGION = "ca-central-1"

# Which cross-region inference profile the Bedrock model ids name, and why there is
# no default.
#
# Checked against the AWS Bedrock model cards on 2026-09-12 (Claude Haiku 4.5 and
# Claude Sonnet 5, "Regional Availability", `bedrock-runtime` endpoint): NEITHER
# MODEL SUPPORTS IN-REGION INFERENCE IN ca-central-1. Architecture v0.4 section 6
# says to "confirm Haiku 4.5 and Sonnet 5 availability in-region before the Bedrock
# cutover (D1, D19)". That is confirmed, and the answer is no. A bare model id is
# rejected for on-demand throughput, so the call must name an inference profile,
# and choosing between the two reachable ones is a data-residency decision:
#
#   us      the `us.` geo profile. From ca-central-1 the request is served in
#           ca-central-1, us-east-1, us-east-2 or us-west-2; the Sonnet 5 card
#           states the US geo "keeps data within US and Canada regions".
#   global  the `global.` profile. Routed anywhere in the world, no residency
#           constraint, widest capacity.
#
# There is no `ca.` profile for either model, and the eu, au and jp geos do not
# list ca-central-1 as a source region, so these two are the entire choice.
#
# No default, deliberately. A default would be this file answering a residency
# question on the account's behalf, and rule 1 says an alternative is a decision
# people take rather than a branch the code takes. Rule 19 bounds what is at stake:
# a prompt carries public notice text and metadata only, so whatever crosses a
# border was already published on a government portal.
BEDROCK_INFERENCE_GEOS = ("us", "global")

# The two models' Bedrock ids, per geo, copied from the AWS model cards on
# 2026-09-12 rather than assembled from a pattern or recalled.
#
# UNVERIFIED AGAINST A LIVE ACCOUNT. This repository has never held AWS
# credentials, so no id here has been seen to return a 200. Before the cutover,
# confirm with `aws bedrock list-inference-profiles --region ca-central-1` and
# correct this table if AWS has moved. Sonnet 5's id carries no date suffix and
# Haiku 4.5's does; that asymmetry is what the cards say, not a typo here.
BEDROCK_MODEL_IDS: dict[str, dict[str, str]] = {
    "us": {
        "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "claude-sonnet-5": "us.anthropic.claude-sonnet-5",
    },
    "global": {
        "claude-haiku-4-5": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
        "claude-sonnet-5": "global.anthropic.claude-sonnet-5",
    },
}


def model_route() -> str:
    route = os.environ.get("MODEL_ROUTE", DEFAULT_MODEL_ROUTE).strip().lower()
    if route not in MODEL_ROUTES:
        raise RuntimeError(f"MODEL_ROUTE is {route!r}; it must be one of {', '.join(MODEL_ROUTES)}")
    return route


def aws_region() -> str:
    """The region the Bedrock request is signed for and sent to."""
    return os.environ.get("AWS_REGION", DEFAULT_AWS_REGION).strip()


def bedrock_inference_geo() -> str:
    """Which inference profile the bedrock route uses. Unset is an error, not a guess."""
    geo = os.environ.get("BEDROCK_INFERENCE_GEO", "").strip().lower()
    if geo not in BEDROCK_INFERENCE_GEOS:
        raise RuntimeError(
            f"BEDROCK_INFERENCE_GEO is {geo!r}; it must be one of {', '.join(BEDROCK_INFERENCE_GEOS)}. "
            f"Neither Haiku 4.5 nor Sonnet 5 runs in-region in {aws_region()}, so the call has to name a "
            "cross-region inference profile, and which one is a residency decision rather than a default "
            "(Architecture v0.4 section 6, D1 and D19)."
        )
    return geo


def model_id(model: str) -> str:
    """What this model is called on the configured route.

    `claude-haiku-4-5` on the direct and proxy routes; a Bedrock inference profile
    id on the bedrock route. Only the wire name changes: the logical name stays what
    `model_calls` records and what the rate card in `config/thresholds.yaml` is keyed
    on, so a row written before the cutover and a row written after are comparable.
    That is what makes step 12's acceptance test - `make golden` on Bedrock
    reproducing the direct-API numbers at the same prompt_version - a comparison of
    like with like rather than of two differently labelled things.

    The route is read from the environment, never inferred from the client object.
    `isinstance(client, anthropic.AnthropicBedrock)` would work and is exactly the
    detection rule 1 forbids.
    """
    if model_route() != "bedrock":
        return model

    geo = bedrock_inference_geo()
    ids = BEDROCK_MODEL_IDS[geo]
    if model not in ids:
        raise RuntimeError(
            f"no {geo} Bedrock inference profile recorded for {model!r}. Add it to BEDROCK_MODEL_IDS in "
            "monitor/cli.py from the model's AWS model card, then confirm it with "
            f"`aws bedrock list-inference-profiles --region {aws_region()}`."
        )
    return ids[model]


def model_client():
    """The Anthropic client for the configured route, or a message a person can act on.

    The SDK raises a TypeError from inside its own constructor when no credential
    is resolvable, which is loud but not legible: in the middle of `monitor run` it
    reads as a bug in the pipeline. This says what is missing and what to do.
    """
    import anthropic

    route = model_route()

    if route == "proxy":
        # The proxy attaches the credential at egress, so the request has to leave
        # with no auth header at all. `anthropic.omit` is the SDK's own sanctioned
        # way to say that: its error names "the `X-Api-Key` or `Authorization`
        # headers explicitly omitted" as the alternative to a key. Verified against
        # the SDK on 2026-09-12: the request goes out carrying neither header.
        return anthropic.Anthropic(api_key=None, default_headers={"X-Api-Key": anthropic.omit})

    if route == "bedrock":
        # `aws_region`, not `region`: BUILD_ORDER step 12 writes
        # `AnthropicBedrock(region="ca-central-1")` and the SDK's keyword is
        # `aws_region`. Checked against anthropic 1.5.0, which is what is installed.
        #
        # Nothing is passed for the credential. The EC2 instance profile supplies it
        # and botocore signs each request with SigV4 at send time, which is why
        # `anthropic[bedrock]` (and so boto3) is a dependency and why rule 20's "no
        # secret in code or config" costs nothing here: on this route there is no
        # model credential anywhere to put in a file.
        #
        # The region is passed explicitly rather than left to the SDK to infer from
        # AWS_REGION, because when it is unset the SDK falls back to a boto3 session
        # and then to an error, and a run that signs for a region nobody chose is
        # the kind of quiet wrong answer rule 4 exists to prevent.
        #
        # The geo is read here only to fail at startup rather than after the first
        # notice: `model_id` resolves it again per call, and an unset one would
        # otherwise surface one cap check into a run of a thousand. Same reason the
        # direct route checks for a key here instead of letting the first request
        # find out.
        bedrock_inference_geo()
        return anthropic.AnthropicBedrock(aws_region=aws_region())

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        raise RuntimeError(
            "no model credential on the direct route: set ANTHROPIC_API_KEY in .env, "
            "or set MODEL_ROUTE=proxy if the credential is held by the cloud environment. "
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


def run_metrics(days: int) -> int:
    """Measure the week, store the run the metrics page reads, print the report.

    Two connections, for the reason `monitor status` uses two: the reads include the
    export backlog, which lives in `approved_records`, and `migrations/002_roles.sql`
    leaves monitor_pipeline no privilege there. Reporting is what monitor_readonly is
    for. The one insert is the pipeline's, because this is a scheduled job and not a
    reviewer action.

    Nothing is notified (rule 18). The row is written, the report is printed, and the
    reviewer reads the page when they read it.
    """
    from monitor.db import connect
    from monitor.health.metrics import WINDOW_DAYS, collect, render, write
    from review.export import reporting_connection

    with reporting_connection() as conn:
        run = collect(conn, window_days=days or WINDOW_DAYS)

    with connect("pipeline") as conn:
        write(conn, run)

    print(render(run))
    print()
    print(f"stored as run {run.run_id}; the metrics page reads it at /metrics")
    return 0


def run_status() -> int:
    """Source health and the filter's arithmetic per source, then the export backlog."""
    from monitor.db import connect
    from monitor.health.status import collect, render
    from review.export import backlog, render_backlog, reporting_connection

    with connect("pipeline") as conn:
        print(render(collect(conn)))

    # On a second connection, as monitor_readonly, because the backlog lives in
    # `approved_records` and `migrations/002_roles.sql` revokes all on that table from
    # monitor_pipeline: the connection above cannot read it, and connecting this command
    # as monitor_review to get at it is rule 11's blocking case. Reporting is what the
    # readonly role is for. It is not wrapped in a try: a status command that silently
    # drops the one number BUILD_ORDER step 16 asks it to show would be worse than one
    # that says the URL is missing (rule 4).
    with reporting_connection() as conn:
        print()
        print(render_backlog(backlog(conn)))
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
    if args.command == "metrics":
        return run_metrics(args.days)

    print(f"monitor {args.command}: not implemented, arrives in {IMPLEMENTED_BY[args.command]}", file=sys.stderr)
    return NOT_IMPLEMENTED_EXIT  # pragma: no cover - every command is implemented


if __name__ == "__main__":
    raise SystemExit(main())
