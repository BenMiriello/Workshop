"""A due trigger fires from inside the backend, with no crontab.

THE DEFECT THIS COVERS. `scripts/dispatch_triggers.sh` is the only thing that
turns a declared `cron` or `loop` trigger into a running sweep, and its only
caller was `cron_agent.sh`, which is reached only from a crontab. There is no
crontab on this host -- not a disabled entry, no crontab at all -- so the
dispatcher had not run since 2026-07-18, `.runtime/trigger-state.json` was frozen
at that date, and every scheduled feature above it read as configured while doing
nothing. `services/trigger_dispatch` ticks it from the backend lifespan instead.

MEASURED, NOT ASSERTED. These tests run the REAL dispatcher through the REAL
backend entry point (`trigger_dispatch.dispatch_once`) against a sandbox workshop
root: its own `.runtime`, its own agent directory, its own registries, and stub
runner scripts in place of the three that would otherwise start a model. Nothing
is stubbed on the deciding side, so a test passes only if the shipped decision
engine fires the trigger.

The sandbox borrows the real tree for CODE (`backend/` is symlinked, as
`test_makers.py` does) and owns every piece of STATE the dispatcher reads or
writes. It carries no autonomy override, so the tick reaches the decision engine;
the operator's own installation has one engaged, which is why a tick there stops
at "dispatching nothing" and why making the dispatcher tick is not the same act
as arming a scope.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import trigger_dispatch  # noqa: E402
from wsplatform import paths  # noqa: E402

REPO = paths.workshop_root()

# Every runner the dispatcher can launch. In the sandbox each is a stub that
# records its arguments, because the real ones start a model. Substituting the
# TARGET is not the same as substituting the decision: the script still chooses
# which one to launch and with what, and the choice is what these tests read.
STUB_RUNNERS = ("memory_keeper_run.sh", "triager_run.sh", "project_ralph.sh")

ALWAYS_DUE = "* * * * *"
# 04:00 on January 1st. Paired with a `last_fired` of "now" below, so it is not
# due on any calendar day: the next occurrence after now is always in the future.
NEVER_DUE_SOON = "0 4 1 1 *"


def _agent_definition(*, cron: str) -> str:
    """A keeper agent carrying one cron trigger.

    `keeper` because its dispatch handler is the one with no gate but the usage
    budget -- no circuit breaker, no ready-task predicate, no run target. So when
    this agent does not fire, dueness is the only thing that can have stopped it,
    which is exactly the distinction these two tests are drawing.
    """
    return (
        "---\n"
        "name: Sandbox keeper\n"
        "purpose: ''\n"
        "capabilities: []\n"
        f"triggers:\n  - kind: cron\n    cron_expression: '{cron}'\n"
        "limits: {}\n"
        "memory: null\n"
        "role: keeper\n"
        "autonomy: autonomous\n"
        "---\n\nConsolidate.\n"
    )


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """A workshop root the dispatcher can be pointed at without touching this
    installation's state. Returns the root."""
    root = tmp_path / "ws"
    (root / ".runtime").mkdir(parents=True)
    (root / "agents").mkdir()
    (root / "backend").symlink_to(REPO / "backend")
    scripts = root / "scripts"
    scripts.mkdir()
    for entry in sorted((REPO / "scripts").iterdir()):
        if entry.name in STUB_RUNNERS:
            continue
        (scripts / entry.name).symlink_to(entry)
    launched = root / ".runtime" / "launched.txt"
    for name in STUB_RUNNERS:
        stub = scripts / name
        stub.write_text(f'#!/bin/bash\necho "{name} $*" >> "{launched}"\n')
        stub.chmod(0o755)
    (root / ".runtime" / "projects.json").write_text(json.dumps({"projects": {}}))

    for key, value in {
        "WORKSHOP": str(root),
        "WORKSHOP_ROOT": str(root),
        "WORKSHOP_RUNTIME": str(root / ".runtime"),
        "WORKSHOP_AGENTS_DIR": str(root / "agents"),
        "WORKSHOP_PROJECTS_REGISTRY": str(root / ".runtime" / "projects.json"),
    }.items():
        monkeypatch.setenv(key, value)
    return root


def _state(root: Path) -> dict:
    try:
        return json.loads((root / ".runtime" / "trigger-state.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _dispatch_log(root: Path) -> str:
    try:
        return (root / ".runtime" / "dispatch.log").read_text()
    except OSError:
        return ""


def _launched(root: Path, *, wait_s: float = 5.0) -> str:
    """What the dispatcher actually started. Polled, because every sweep is
    launched detached (`setsid nohup ... &`) precisely so a tick never blocks on
    one -- so the launch is observable slightly after the tick returns."""
    marker = root / ".runtime" / "launched.txt"
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        try:
            text = marker.read_text()
        except OSError:
            text = ""
        if text.strip():
            return text
        time.sleep(0.1)
    return ""


def test_a_due_trigger_fires_from_the_backend_with_no_crontab(sandbox):
    """The acceptance. One agent, one cron trigger that is due, no crontab, no
    external process: the backend's own dispatch entry point runs and the trigger
    fires -- the sweep is launched and `trigger-state.json` records the firing."""
    (sandbox / "agents" / "sandbox-keeper.md").write_text(_agent_definition(cron=ALWAYS_DUE))
    assert not (sandbox / ".runtime" / "trigger-state.json").exists()

    res = trigger_dispatch.dispatch_once()

    assert res["ran"], res
    assert res["rc"] == 0, res
    key = f"agent:sandbox-keeper:cron:{ALWAYS_DUE}"
    state = _state(sandbox)
    assert key in state, (state, res)
    fired = datetime.fromisoformat(state[key])
    assert abs((datetime.now(timezone.utc) - fired).total_seconds()) < 300
    assert "launch memory consolidation" in _dispatch_log(sandbox), _dispatch_log(sandbox)
    assert "memory_keeper_run.sh workshop" in _launched(sandbox)


def test_a_trigger_that_is_not_yet_due_does_not_fire(sandbox):
    """The other half, and the one that makes the first half mean something: the
    same agent, the same entry point, the same tick -- only the schedule differs,
    and nothing is launched. A dispatcher that fired everything every tick would
    pass the test above."""
    (sandbox / "agents" / "sandbox-keeper.md").write_text(
        _agent_definition(cron=NEVER_DUE_SOON))
    key = f"agent:sandbox-keeper:cron:{NEVER_DUE_SOON}"
    before = {key: datetime.now(timezone.utc).replace(
        second=0, microsecond=0).isoformat()}
    (sandbox / ".runtime" / "trigger-state.json").write_text(json.dumps(before))

    res = trigger_dispatch.dispatch_once()

    assert res["ran"], res
    assert res["rc"] == 0, res
    assert _state(sandbox) == before
    assert "launch" not in _dispatch_log(sandbox), _dispatch_log(sandbox)
    assert _launched(sandbox, wait_s=1.0) == ""


def test_two_backends_cannot_both_dispatch(sandbox):
    """The caps inside the dispatcher bound ONE tick, and the cron firing it
    records is written by the last writer of `trigger-state.json`, so two
    dispatchers would double-launch and then disagree about what had fired. A
    second backend is another PROCESS, which no module-level ownership flag can
    see, so the gate is an exclusive lock: held elsewhere, the tick is skipped
    rather than run."""
    (sandbox / "agents" / "sandbox-keeper.md").write_text(_agent_definition(cron=ALWAYS_DUE))
    fd = os.open(trigger_dispatch._lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        res = trigger_dispatch.dispatch_once()
    finally:
        os.close(fd)

    assert res["ran"] is False
    assert "dispatch lock" in res["skipped"]
    assert _state(sandbox) == {}
    assert _launched(sandbox, wait_s=1.0) == ""

    # And released, the very same call dispatches -- so the skip above is the
    # lock and not some other property of the sandbox.
    assert trigger_dispatch.dispatch_once()["rc"] == 0
    assert f"agent:sandbox-keeper:cron:{ALWAYS_DUE}" in _state(sandbox)


def test_the_loop_is_actually_started_by_the_backend():
    """A dispatcher nobody calls is what this ticket was about. The module could
    be perfect and the system would still be exactly as inert as it was before,
    so the registration is asserted, not assumed."""
    src = (REPO / "backend" / "main.py").read_text(encoding="utf-8")
    assert "trigger_dispatch" in src
    assert "_trigger_dispatch.dispatch_loop()" in src
