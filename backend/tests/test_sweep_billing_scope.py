"""A sweep must bill to the SCOPE whose tickets it works, not to the basename
of the directory it runs in.

Measured 2026-08-22: an unbound `--dir` sweep against the platform repo logged
`scope=workshop` and billed 1,518,896 tokens there, hitting workshop's ceiling
of 1,500,000 and stopping the loop, while every ticket it worked lives in the
`development` scope whose own 1,500,000 allowance was untouched. The per-scope
ceiling therefore gated a scope the sweep never touched.

The derivation lives in scripts/project_ralph.sh, so these tests run the real
shell function rather than a Python restatement of it: a Python copy could stay
green while the script drifted, which is the failure this is here to catch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from services import task_scope
from wsplatform import paths

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "project_ralph.sh"


def _run_shell_derivation(directory: str) -> str:
    """Execute project_ralph.sh's own scope_key_for_dir against `directory`.

    The function is extracted from the live script (rather than sourced, which
    would run a whole sweep) so a drift between this test and the script shows
    up as a failure here."""
    src = SCRIPT.read_text()
    start = src.index("scope_key_for_dir() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    fn = src[start:end]
    root = str(paths.workshop_root())
    prog = f'WORKSHOP={root!r}\nDIR={directory!r}\nLOG=/dev/null\n{fn}\nscope_key_for_dir\n'
    out = subprocess.run(["bash", "-c", prog], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_the_platform_repo_bills_to_development_not_to_its_basename():
    """The regression itself. basename() of the platform repo is 'workshop',
    but its ticket store is the `development` scope (ADR 0014), so billing by
    basename charged the wrong bucket."""
    root = str(paths.workshop_root())
    # The premise, stated so it holds in either namespace: unconfined the root
    # is ~/workshop and confined it is the /workspace mountpoint. Neither
    # basename is the scope key, which is exactly why guessing from the path
    # was wrong.
    assert Path(root).name != "development"
    assert task_scope.key_for_root(root) == "development"
    assert _run_shell_derivation(root) == "development"


def test_a_project_root_bills_to_its_BARE_slug():
    """The ledger key for a project is the bare slug: dispatch_triggers admits
    with `can_admit(slug)`, so emitting the prefixed `project:<slug>` here would
    split one scope's budget across two ledger keys and silently double it."""
    root = paths.workshop_root() / "projects" / "some-project"
    assert task_scope.key_for_root(root) == "project:some-project"
    assert _run_shell_derivation(str(root)) == "some-project"


def test_an_agent_scope_keeps_its_agent_prefix():
    """dispatch_triggers admits an agent with `can_admit(f"agent:{slug}")`, so
    unlike a project this prefix is the key and must survive."""
    root = paths.workshop_root() / "agent-scopes" / "some-agent"
    assert task_scope.key_for_root(root) == "agent:some-agent"
    assert _run_shell_derivation(str(root)) == "agent:some-agent"


def test_an_unregistered_directory_has_no_scope_key():
    """The fallback must stay reachable: an ad-hoc directory that is not a
    registered scope root still has to bill somewhere rather than fail the
    sweep, so the script keeps basename() behind this."""
    assert task_scope.key_for_root("/tmp/not-a-scope-root") is None
    assert _run_shell_derivation("/tmp/not-a-scope-root") == ""


def test_the_script_no_longer_bills_by_basename_first():
    """Pins the shape, not just the output: basename must be the LAST resort,
    reached only when the scope key is empty."""
    src = SCRIPT.read_text()
    assert 'BILL_SCOPE="${SLUG:-$ASSIGNEE}"; [[ -z "$BILL_SCOPE" ]] && BILL_SCOPE="$(basename "$DIR")"' not in src
    assert 'BILL_SCOPE="$(scope_key_for_dir)"' in src
