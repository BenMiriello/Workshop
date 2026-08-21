"""In-chat background agent: a free-form `claude -p` run launched from a chat.

This is the no-ticket half of fire-and-continue. A chat model calls the
`spawn_background` MCP tool (mcp/workshop_query_server.py); that calls `launch`,
which opens a Job and spawns a DETACHED runner and returns at once, so the chat
turn finishes normally. The runner invokes `claude -p` on the prompt, captures
the final text, posts it back into the chat as an autonomous message, and asks
the thread to take its own next turn (services/chat_drain.request_continuation).
The operator keeps chatting the whole time and the chat picks the result up on
its own when it lands.

Why a Job and not a backgrounded subagent: a chat turn is a one-shot process
that exits when it stops emitting tool calls, so a subagent nested in it cannot
outlive it (services/subagent_guard.py). A Job is the durable, restart-surviving
primitive that can (services/jobs.py); the completion-to-chat delivery reuses
the same seam a ticket run uses (services/run_report + chat_drain).

Two entry points:
  launch(...) -> job_id     opens the Job, spawns the detached runner.
  run(...)  (CLI)           the detached runner; does the work and delivers.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_WORKSHOP_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
_BACKEND = Path(__file__).resolve().parent.parent               # backend/

# Bound a background agent so a single chat cannot spend without limit. These
# match the shape of ralph's per-iteration caps; a background investigation is
# one turn's worth of work, not a sweep.
_TIMEOUT_S = 600
_MAX_TURNS = 40


def _claude_bin() -> str:
    """Resolve the claude CLI to a real executable, mirroring scripts/ralph.sh:
    a pinned nvm path can be a broken shim, so prefer the system install."""
    import shutil
    for c in (os.environ.get("CLAUDE_BIN"), "/usr/local/bin/claude",
              shutil.which("claude")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "/usr/local/bin/claude"


def _job_dir(job_id: str) -> Path:
    d = _WORKSHOP_ROOT / ".runtime" / "background" / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scope_cwd(scope: str) -> Path:
    """Where the background agent runs. The chat's scope root when it resolves,
    else the workshop root so it can at least read and search the tree."""
    try:
        from services import task_scope
        root = task_scope.root_for_context(scope)
        if root:
            return Path(root)
    except Exception:
        pass
    return _WORKSHOP_ROOT


def launch(*, thread_id: str, owner_id: int, scope: str, prompt: str,
           label: str | None = None) -> str:
    """Open a background Job for this chat and spawn its detached runner.

    Returns the job id. Raises ValueError on an empty prompt. Never blocks on the
    work: the runner is a separate, detached process."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("a background agent needs a prompt")
    label = (label or prompt.splitlines()[0][:60]).strip()

    # Only mint the id here; the RUNNER opens the Job record. Why not here: this
    # call runs inside the chat turn's bwrap view, where the run log's path
    # (paths.runtime_dir(), a symlink-resolved host path) is unmounted, so a
    # jobs.start() write here lands in a throwaway layer and the run never shows
    # up. The runner is given WORKSHOP_RUNTIME below so its jobs writes go to the
    # SAME bind-mounted root the chat store already uses.
    from services import jobs, ownership
    job_id = jobs._new_id()

    prompt_file = _job_dir(job_id) / "prompt.txt"
    prompt_file.write_text(prompt)

    runner = [
        sys.executable, "-m", "services.background_runs", "run",
        "--job-id", job_id, "--thread", thread_id, "--owner", str(owner_id),
        "--scope", scope, "--label", label, "--prompt-file", str(prompt_file),
    ]
    logf: object
    try:
        logf = open(_job_dir(job_id) / "runner.log", "a")  # noqa: SIM115
    except OSError:
        logf = subprocess.DEVNULL
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_BACKEND)
        # Pin the runtime root to the one the chat store uses (ownership._RUNTIME,
        # the bind-mounted /workspace/.runtime), so the runner's Job records land
        # in the same jobs.jsonl the backend reads instead of a sandbox-local path
        # that evaporates. See the note above.
        env["WORKSHOP_RUNTIME"] = str(ownership._RUNTIME)
        proc = subprocess.Popen(
            runner, cwd=str(_BACKEND), stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True, env=env,
        )
    finally:
        if hasattr(logf, "close"):
            logf.close()
    log.info("background agent launched: job=%s thread=%s label=%r", job_id, thread_id, label)
    return job_id


# ---------------------------------------------------------------------------
# The detached runner
# ---------------------------------------------------------------------------

def _run_claude(prompt: str, cwd: Path, owner_id: int, scope: str) -> tuple[int, str, dict]:
    """Run one headless claude turn on the prompt. Returns (exit, text, usage).

    `--output-format json` wraps the final assistant text in `.result` and
    carries `.usage`, exactly as ralph reads it. Best-effort MCP config so the
    agent can search the workshop; a missing config means it runs without search
    rather than not running."""
    mcp_flags: list[str] = []
    try:
        from services import app_registry, llm_router, ownership
        ownership.set_current_owner(owner_id)
        app_registry.set_current_query_scope(scope)
        cfg = llm_router.mcp_config_json(["workshop"], owner_id=owner_id)
        cfg_file = _job_dir("_cfg") / "mcp.json"
        cfg_file.write_text(cfg)
        mcp_flags = ["--mcp-config", str(cfg_file)]
    except Exception as e:
        log.warning("background agent: no MCP config (%s); running without search", e)

    cmd = [_claude_bin(), "-p", prompt, "--output-format", "json",
           "--max-turns", str(_MAX_TURNS), *mcp_flags]
    try:
        out = subprocess.run(
            cmd, cwd=str(cwd), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return 124, "", {}
    raw = out.stdout or ""
    text, usage = "", {}
    try:
        env = json.loads(raw)
        text = (env.get("result") or "").strip()
        usage = env.get("usage") or {}
    except Exception:
        # A stray non-JSON line can land in stdout; keep whatever text there was.
        text = raw.strip()[:4000]
    return out.returncode, text, usage


def _deliver(*, thread_id: str, owner_id: int, job_id: str, label: str,
             status: str, text: str) -> None:
    """Post the result into the chat and ask it to continue. Mirrors
    run_report's autonomous-tier delivery so the client renders it the same way."""
    from services import chat_store, ownership, chat_drain
    prev = ownership.owner_or_none()
    ownership.set_current_owner(owner_id)
    try:
        if status == "done" and text:
            body = f"Background agent finished: {label}\n\n{text}"
        elif status == "done":
            body = f"Background agent finished: {label}\n\n(It produced no text.)"
        else:
            body = f"Background agent stopped: {label}\n\n{text or '(no output)'}"
        chat_store.append_message(
            thread_id, "assistant", body,
            meta={"kind": "background_result", "autonomous": True,
                  "job": job_id, "status": status},
        )
        seed = (
            "[AUTONOMOUS CONTINUATION - not a message from the operator]\n"
            f"A background agent you started ({label!r}) has finished, and its "
            "result is the autonomous message directly above this one. Read it "
            "and carry the conversation forward for the operator: give them the "
            "answer plainly, or take the clear next step. Do not greet the "
            "operator or ask what they want. If it produced nothing useful, say "
            "so briefly and stop."
        )
        chat_drain.request_continuation(thread_id, seed=seed, dedupe_id=f"{job_id}-{thread_id}")
    finally:
        if prev is None:
            ownership.clear_current_owner()
        else:
            ownership.set_current_owner(prev)


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="background_runs run")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--owner", required=True, type=int)
    ap.add_argument("--scope", default="workshop")
    ap.add_argument("--label", default="")
    ap.add_argument("--prompt-file", required=True)
    a = ap.parse_args(argv)

    # Open the Job record HERE, not in launch(): this process was given
    # WORKSHOP_RUNTIME pointing at the bind-mounted runtime root, so its jobs
    # writes persist where the backend reads them. pid is ours, the process the
    # liveness watchdog can actually probe.
    from services import jobs
    jobs.start(job_id=a.job_id, trigger="manual", status="running", kind="background",
               chat_id=a.thread, pid=os.getpid(),
               binding={"label": a.label or "background agent", "scope": a.scope,
                        "kind": "chat_background"})

    prompt = Path(a.prompt_file).read_text()
    exit_code, text, usage = _run_claude(prompt, _scope_cwd(a.scope), a.owner, a.scope)
    status = "done" if exit_code == 0 else "failed"

    # Deliver first, so the chat has the result even if the ledger write hiccups;
    # then close the Job (finish() also runs run_report, which is a no-op here
    # because a background Job worked no ticket).
    try:
        _deliver(thread_id=a.thread, owner_id=a.owner, job_id=a.job_id,
                 label=a.label or "background agent", status=status, text=text)
    except Exception as e:
        log.warning("background agent delivery failed for job %s: %s", a.job_id, e)

    ev = {"summary": text[:2000]} if text else {"summary": ""}
    if usage:
        ev["usage"] = usage
    jobs.finish(a.job_id, status=status, exit_code=exit_code, evidence=ev)
    return 0


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        raise SystemExit(run(sys.argv[2:]))
    sys.stderr.write("usage: python -m services.background_runs run --job-id ... "
                     "--thread ... --owner ... --prompt-file ...\n")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
