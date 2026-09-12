"""The CLI's surface: every command reachable, every argument validated.

This started at step 1 as a test that each stub exited 2 and said which step would
build it. Every command is now built, so what is left is the thing that would
otherwise go unnoticed: a subcommand added to the parser with no branch in main()
parses cleanly, exits 0, and does nothing at all.
"""

import pytest

from monitor.cli import IMPLEMENTED_BY, NOT_IMPLEMENTED_EXIT, main


def test_no_command_is_a_stub_any_more():
    """Every command in the CLI is implemented. The table is empty and stays empty."""
    assert IMPLEMENTED_BY == {}
    assert NOT_IMPLEMENTED_EXIT == 2


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as raised:
        main(["publish"])

    assert raised.value.code == 2


def test_fetch_requires_a_source():
    with pytest.raises(SystemExit) as raised:
        main(["fetch"])

    assert raised.value.code == 2


def test_every_subcommand_is_reachable():
    """A command in the parser with no branch in main() would silently do nothing."""
    from monitor.cli import build_parser

    parser = build_parser()
    commands = sorted(name for action in parser._subparsers._group_actions for name in action.choices)

    # `metrics` joined the list at BUILD_ORDER step 22, `rescore` and `fx` on
    # 2026-09-12. The
    # assertion is the whole list rather than a membership check so that a command added
    # to the parser and never given a branch in main() fails here rather than doing
    # nothing at runtime.
    assert commands == [
        "fetch",
        "filter",
        "fx",
        "golden",
        "metrics",
        "rescore",
        "run",
        "score",
        "stage",
        "status",
        "translate",
    ]


def test_every_subcommand_actually_has_a_branch_in_main():
    """The assertion the test above only claims to make, made.

    The list check catches a command being ADDED without anyone looking, because it
    fails until a person edits it - but a person editing it can just as easily paste
    the name in and forget the dispatch, and nothing would notice. `rescore` was added
    on 2026-09-12 for a reason that makes the distinction concrete: the escalation it
    calls had existed and been tested since step 20 with NO caller anywhere outside
    `tests/unit/test_rescore.py`, so a tested code path that nothing can invoke is a
    failure mode this project has actually shipped once.
    """
    import inspect

    from monitor.cli import build_parser, main

    source = inspect.getsource(main)
    parser = build_parser()
    commands = [name for action in parser._subparsers._group_actions for name in action.choices]

    missing = [name for name in commands if f'args.command == "{name}"' not in source]
    assert not missing, f"in the parser with no dispatch in main(): {missing}"
