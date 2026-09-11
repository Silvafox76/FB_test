"""The CLI's contract while its commands are stubs: say what is missing, exit 2.

This is the step 1 suite. It is replaced command by command as each step lands;
it is not a placeholder that survives to week 14.
"""

import pytest

from monitor.cli import IMPLEMENTED_BY, NOT_IMPLEMENTED_EXIT, main


@pytest.mark.parametrize("command", sorted(IMPLEMENTED_BY))
def test_stub_exits_two_and_names_its_step(command, capsys):
    """fetch is not here any more: it was implemented at step 4."""
    assert main([command]) == NOT_IMPLEMENTED_EXIT
    assert "not implemented" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as raised:
        main(["publish"])

    assert raised.value.code == 2


def test_fetch_requires_a_source():
    with pytest.raises(SystemExit) as raised:
        main(["fetch"])

    assert raised.value.code == 2
