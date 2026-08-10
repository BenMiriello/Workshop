"""A sweep that never ran must not close its Job as `done`.

project_ralph.sh breaks out of its loop when admission refuses the next task,
and the only terminal call at the bottom closed the Job `done` with 0 tasks. So
the record the chat card and the Jobs feed read said the run finished cleanly
when it had not started. The refusal text was also thrown away (`>/dev/null`),
which left nothing anywhere that could explain why nothing happened.

This drives the real script. It is safe to run: admission is refused BEFORE the
claim, so no worker is ever invoked.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from services import paths

RALPH = paths.scripts("project_ralph.sh")

pytestmark = pytest.mark.skipif(
    not RALPH.exists() or shutil.which("jq") is None,
    reason="needs project_ralph.sh and jq",
)


def _sweep(tmp_path, *, tasks, ledger_tokens, args):
    """Run one sweep against an isolated tree/ledger/job log. Returns the
    collapsed Job record."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / ".tasks.json").write_text(json.dumps({"tasks": tasks}))
    (tmp_path / "budget.json").write_text(json.dumps({
        "app_total": 1000, "nominal_task_tokens": 100,
        "autonomy_budget_fraction": 1.0,
        "default_project": {"weight": 1.0, "floor": 0, "ceil": None},
        "projects": {},
    }))
    now = datetime.now(timezone.utc).isoformat()
    (tmp_path / "usage.jsonl").write_text(json.dumps({
        "ts": now, "project": "unbound", "tokens": ledger_tokens, "role": "worker",
    }) + "\n")
    jobs_log = tmp_path / "jobs.jsonl"
    jobs_log.write_text("")

    env = dict(os.environ)
    env.update({
        "WORKSHOP_JOBS_LOG": str(jobs_log),
        "WORKSHOP_USAGE_LEDGER": str(tmp_path / "usage.jsonl"),
        "WORKSHOP_USAGE_BUDGET": str(tmp_path / "budget.json"),
    })
    subprocess.run(
        ["bash", str(RALPH), "--dir", str(tree), "--force", "--job-id", "job_test",
         *args],
        env=env, capture_output=True, timeout=300, check=False,
    )
    rec: dict = {}
    for line in jobs_log.read_text().splitlines():
        if line.strip():
            rec.update(json.loads(line))
    return rec


def _todo(tid="t-halt"):
    return [{"id": tid, "title": "A ticket that will not run",
             "text": "A ticket that will not run", "status": "todo",
             "created": "2026-08-10T00:00:00+00:00"}]


def test_a_refused_sweep_closes_cancelled_not_done(tmp_path):
    rec = _sweep(tmp_path, tasks=_todo(), ledger_tokens=5000,
                 args=["--task-id", "t-halt"])
    assert rec["status"] == "cancelled", \
        "a sweep that was refused before claiming reported success"
    assert rec["tasks_done"] == 0


def test_the_refusal_reason_survives_onto_the_job(tmp_path):
    """The reason is what reaches the chat (run_report reads evidence.halted).
    Discarding it is why a refused run looked like silence."""
    rec = _sweep(tmp_path, tasks=_todo(), ledger_tokens=5000,
                 args=["--task-id", "t-halt"])
    halted = (rec.get("evidence") or {}).get("halted") or ""
    assert "autonomous budget spent" in halted
    assert "5000" in halted and "1000" in halted


def test_the_ticket_is_left_ready_for_a_later_run(tmp_path):
    tree = tmp_path / "tree"
    _sweep(tmp_path, tasks=_todo(), ledger_tokens=5000, args=["--task-id", "t-halt"])
    store = json.loads((tree / ".tasks.json").read_text())
    assert store["tasks"][0]["status"] == "todo"


def test_a_sweep_with_nothing_to_do_still_closes_done(tmp_path):
    """The contrast case: an empty queue under budget is a genuine clean finish,
    so the fix must not turn every quiet sweep into a cancellation."""
    rec = _sweep(tmp_path, tasks=[], ledger_tokens=1, args=[])
    assert rec["status"] == "done"
    assert not (rec.get("evidence") or {}).get("halted")
