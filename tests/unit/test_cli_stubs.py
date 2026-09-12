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

    # `metrics` joined the list at BUILD_ORDER step 22. The assertion is the whole
    # list rather than a membership check so that a command added to the parser and
    # never given a branch in main() fails here rather than doing nothing at runtime.
    assert commands == ["fetch", "filter", "golden", "metrics", "run", "score", "stage", "status", "translate"]
