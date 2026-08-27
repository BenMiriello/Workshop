"""An empty fencing token is refused, not silently promoted to an operator write.

`tasks.update` reads `expect_claim_token is None` as "this is an operator write,
never fence it", which is correct and is what lets an operator take a ticket back
from a worker. The CLI's argument parser used to map an empty string to None, so
a caller that MEANT to present a fence and presented an empty one did not fail:
it was upgraded to the more authoritative form. Update's Fix 1 then saw an
unfenced material edit to a ticket a worker held and bumped the generation,
fencing out the very run that made the call while its status change still landed.

Both halves were silent. Nothing logged and the exit code was 0, so a caller
could not distinguish a fence that passed from a fence that was never applied.

Every fence call site in `project_ralph.sh` interpolates `"${CLAIM_TOKEN:-}"`,
which is exactly the shell idiom that produces an empty string, so this was one
unset variable away from firing at any of them.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[2] / "scripts" / "project_tasks.py"


@pytest.fixture
def cli():
    spec = importlib.util.spec_from_file_location("project_tasks_cli", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_an_omitted_flag_still_means_an_operator_write(cli):
    """The supported way to make an unfenced write, unchanged."""
    assert cli._opt_int(None, "--expect-claim-token") is None


def test_a_real_token_still_parses(cli):
    assert cli._opt_int("7", "--expect-claim-token") == 7
    assert cli._opt_int(" 7 ", "--expect-claim-token") == 7


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_token_is_refused_rather_than_promoted(cli, value):
    with pytest.raises(ValueError) as e:
        cli._opt_int(value, "--expect-claim-token")
    assert "--expect-claim-token" in str(e.value)


def test_an_unparseable_token_is_refused(cli):
    with pytest.raises(ValueError):
        cli._opt_int("abc", "--expect-claim-token")


def test_the_error_says_what_omitting_the_flag_would_mean(cli):
    """A caller that hits this needs to know the supported alternative, and the
    alternative differs per flag: absence is an operator write for the fence and
    'no pid recorded' for the pid."""
    with pytest.raises(ValueError) as e:
        cli._opt_int("", "--expect-claim-token")
    assert "operator write" in str(e.value)
    with pytest.raises(ValueError) as e:
        cli._opt_int("", "--pid")
    assert "no pid" in str(e.value)


def test_a_malformed_pid_is_an_error_too(cli):
    """Deliberate, not incidental: a malformed pid read as 'no pid recorded'
    changes which tickets `reclaim` considers stale."""
    with pytest.raises(ValueError):
        cli._opt_int("", "--pid")
    with pytest.raises(ValueError):
        cli._opt_int("not-a-pid", "--pid")
    assert cli._opt_int(None, "--pid") is None


def test_every_verb_that_takes_the_flag_agrees(cli, tmp_path, monkeypatch):
    """set-status, block and check-touches all take --expect-claim-token, and a
    rule enforced at only one of them is not a rule. Each must exit non-zero and
    report the error the way the CLI reports any bad argument."""
    import io
    import json
    from contextlib import redirect_stdout

    cases = [
        ["project_tasks.py", "set-status", str(tmp_path), "t1", "done",
         "--expect-claim-token", ""],
        ["project_tasks.py", "block", str(tmp_path), "t1", "why",
         "--expect-claim-token", ""],
        ["project_tasks.py", "check-touches", str(tmp_path), "t1",
         "--expect-claim-token", ""],
    ]
    for argv in cases:
        buf = io.StringIO()
        # check-touches reads its file list from stdin before it parses flags,
        # so it needs one even on the path that is about to refuse the token.
        monkeypatch.setattr("sys.stdin", io.StringIO("some/file.py\n"))
        with redirect_stdout(buf):
            rc = cli.main(argv)
        assert rc == 1, f"{argv[1]} did not fail on an empty token"
        assert "expect-claim-token" in json.loads(buf.getvalue())["error"]
