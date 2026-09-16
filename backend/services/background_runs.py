"""In-chat background agent: a free-form `claude -p` run launched from a chat.

This is the no-ticket half of fire-and-continue. A chat model calls the
`spawn_background` MCP tool (mcp/workshop_query_server.py); that calls `launch`,
which opens a Job and spawns a DETACHED runner and returns at once, so the chat
turn finishes normally. The runner invokes `claude -p` on the prompt, captures
the final text, posts it back into the chat as an autonomous message, and asks
the thread to take its own next turn (services/chat_drain.request_continuation).
The operator keeps chatting the whole time and the chat picks the result up on
its own when it lands.

Why a Job and not a backgrounded subagent: a subagent belongs to the turn that
launched it. Since 2026-09-01 a background subagent genuinely works -- `turn_end`
holds the turn open until it reports -- but it reports into THAT turn, so it can
never give the operator an answer now and the result later. A Job is the durable,
restart-surviving primitive that can (services/jobs.py); the completion-to-chat
delivery reuses the same seam a ticket run uses (services/run_report +
chat_drain). The two are not rivals: one is concurrency inside a turn, the other
is work that outlives one.

Two entry points:
  launch(...) -> job_id     opens the Job, spawns the detached runner.
  run(...)  (CLI)           the detached runner; does the work and delivers.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_WORKSHOP_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
_BACKEND = Path(__file__).resolve().parent.parent               # backend/

# Bound a background agent so a single chat cannot spend without limit.
#
# THE OLD CEILING WAS 600s AND IT WAS THE WRONG SHAPE OF BOUND. Measured
# 2026-08-28: an audit agent asked to read forty documents against the tree was
# killed at exactly ten minutes (job_1787934884118_2f00, exit 124) with its
# entire output discarded, and the chat was told it had "finished". Ten minutes
# is under one deep read of a large repo, so the cap was not limiting waste, it
# was truncating every job big enough to be worth delegating. `_MAX_TURNS` is
# the bound that actually limits spend, because it counts model turns rather
# than wall-clock, and it stays where it was.
#
# The remaining wall-clock bound exists only to stop a wedged process living
# forever. `WORKSHOP_BACKGROUND_TIMEOUT_S` overrides it for a host that wants
# a tighter or looser leash.
def _timeout_s() -> int:
    raw = (os.environ.get("WORKSHOP_BACKGROUND_TIMEOUT_S") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return 3600


_MAX_TURNS = 40

# What `_run_claude` returns for a run the wall-clock bound killed. Distinct
# from any exit code the CLI itself produces, and the same number `timeout(1)`
# uses, so a reader who knows one knows the other.
TIMED_OUT = 124

# What the runner reports for a run that was REFUSED before any model turn
# happened: the scope did not resolve to a real tree, or the sandbox could not
# be built. 126 is the shell's "found but could not execute", which is exactly
# what happened — the work was never started, as opposed to started and failed.
REFUSED = 126

# Job dirs older than this are eligible for reaping on the next completed run.
_REAP_AFTER_DAYS = 7


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


def _scope_cwd(scope: str) -> Path | None:
    """Where the background agent runs: the chat's scope root, or None when the
    scope does not resolve to a real directory.

    NO FALLBACK TO THE WORKSHOP ROOT. This used to return `_WORKSHOP_ROOT` --
    the platform's own checkout -- whenever `root_for_context` answered None, on
    the reasoning that an agent that cannot find its scope may as well "read and
    search the tree". But this runner spawns `claude -p` with
    `--permission-mode bypassPermissions`, so the fallback did not grant reading:
    it handed any chat that could call `spawn_background` a writable agent inside
    the Workshop's own source. And the fallback fired in the ordinary case, not a
    freak one -- `root_for_context` returns None for a scope the dev-mode gate
    refuses and for a project whose tree does not exist yet, which is every
    newly-created project.

    This is the same defect, and the same removal, as
    `chat_context.MakerAgentContextProvider.__init__`: it defaulted an unknown
    `build:<slug>` to `workshop_root()` and now propagates the KeyError instead.
    An unresolvable scope is a refusal, not a cwd. See the comment there.

    A root that resolves but is not a directory is unresolvable too: bwrap binds
    the scope root, so a path that is not there cannot be confined to, and the
    honest answer is the same refusal rather than a spawn failure deeper down."""
    try:
        from services import task_scope
        root = task_scope.root_for_context(scope)
    except Exception as e:
        log.warning("background agent: scope %r did not resolve (%s)", scope, e)
        return None
    if not root:
        return None
    p = Path(root)
    return p if p.is_dir() else None


def _refusal_text(scope: str) -> str:
    """Why an unresolvable scope refused, in words the chat can act on. Names the
    dev-mode reason when that is the reason, so a refused scope does not read as
    a scope nobody has heard of."""
    reason = ""
    try:
        from services import task_scope
        reason = task_scope.dev_mode_refusal(scope) or ""
    except Exception:
        pass
    if reason:
        return f"Refused: {reason}"
    return (f"Refused: the scope {scope!r} does not resolve to a working tree, so "
            f"there is nowhere to confine this agent to. A background agent runs "
            f"inside its own scope or it does not run.")


def _check_tree_dirty(cwd: Path) -> bool:
    """True if the tree the agent ran in has uncommitted changes after a run.

    THE RUN'S OWN TREE, not the workshop root. It asked the platform checkout
    before, which was the only tree a background agent could reach back when
    `_scope_cwd` fell back to it. Now the agent is confined to its scope and
    cannot write outside it, so asking the platform tree would report another
    session's work in progress to a chat that did not cause it, and stay silent
    about the tree the agent actually edited."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd), capture_output=True, text=True, timeout=10,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


def _write_status(job_dir: Path, outcome: str, exit_code: int, tree_dirty: bool) -> None:
    """Write a terminal status file so a finished run is distinguishable from a dead one."""
    (job_dir / "status.json").write_text(json.dumps({
        "outcome": outcome,
        "exit_code": exit_code,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tree_dirty": tree_dirty,
    }))


def _reap_old_job_dirs() -> int:
    """Remove job dirs older than _REAP_AFTER_DAYS days. Returns count removed."""
    bg = _WORKSHOP_ROOT / ".runtime" / "background"
    if not bg.is_dir():
        return 0
    cutoff = time.time() - _REAP_AFTER_DAYS * 86400
    removed = 0
    for d in bg.iterdir():
        if not d.name.startswith("job_"):
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
                removed += 1
        except Exception as e:
            log.warning("reap: could not remove %s: %s", d, e)
    return removed


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
    rather than not running.

    CONFINED, like the chat turn, the ralph worker and the voice worker. Raises
    `scoped_exec.ConfinementUnavailable` (or `TurnUserUnavailable`) rather than
    spawning with host filesystem access -- see `run()`, which turns that into a
    refused Job the chat can see."""
    mcp_flags: list[str] = []
    try:
        from services import app_registry, llm_router, ownership
        ownership.set_current_owner(owner_id)
        app_registry.set_current_query_scope(scope)
        # INLINE, not a file. The config used to be written under the workshop
        # root's `.runtime` and passed by path, which is a path that does not
        # exist inside the sandbox for any scope but the workshop's own -- the
        # agent would have been handed a `--mcp-config` naming nothing. As JSON
        # on the argv it rides through `wrap()`'s path remap with the rest of
        # the command, which is how build_streaming passes it.
        mcp_flags = ["--mcp-config",
                     llm_router.mcp_config_json(["workshop"], owner_id=owner_id,
                                                context_key=scope)]
    except Exception as e:
        log.warning("background agent: no MCP config (%s); running without search", e)

    # bypassPermissions: a detached process with nobody to ask was ending every
    # run by requesting approval for file writes (measured 2026-08-28, job
    # job_1787934884118_2f00). Destructive git ops are still denied by name.
    #
    # `--mcp-config` is variadic and keeps eating following non-flag tokens, so
    # the JSON is placed where a FLAG follows it. See tests/test_agentic_turn_gets_search.py.
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "json",
           "--max-turns", str(_MAX_TURNS),
           *mcp_flags,
           "--permission-mode", "bypassPermissions",
           "--disallowedTools", "Bash(git push *),Bash(git push),Bash(rm -rf *)"]
    # THE SANDBOX IS THE FLOOR, not an extra. This runner is reachable from any
    # project chat through the `spawn_background` MCP tool, and it spawns a
    # bypassPermissions agent; un-sandboxed, that agent's filesystem view is the
    # whole host. `spawn_argv` confines it to `cwd` mounted at /workspace and
    # RAISES if it cannot, which is the posture to preserve: the walls dropping
    # is a refusal, not a degraded mode. `turn_env` rewrites the inherited
    # WORKSHOP_* paths onto the mountpoint so what the child reads is what is
    # actually mounted. die_with_parent stays off (the default): this runner is
    # detached and outlives the backend, like the chat turn and unlike ralph.
    from services import scoped_exec
    spawn_cmd = scoped_exec.spawn_argv(cmd, str(cwd))
    env = scoped_exec.turn_env(dict(os.environ), str(cwd))
    try:
        out = subprocess.run(
            spawn_cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, env=env,
            capture_output=True, text=True, timeout=_timeout_s(),
        )
    except subprocess.TimeoutExpired as e:
        # KEEP WHAT IT HAD SAID. `capture_output` buffers, so a killed run's
        # stdout is on the exception and used to be dropped on the floor: the
        # chat was handed an empty result for work the agent had actually done.
        # `--output-format json` only emits its envelope at the end, so this is
        # usually partial or empty, but partial beats nothing and an empty one
        # still gets to say WHY it is empty (`_deliver`).
        partial = e.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return TIMED_OUT, partial.strip()[:4000], {}
    raw = out.stdout or ""
    text, usage = "", {}
    try:
        envelope = json.loads(raw)
        text = (envelope.get("result") or "").strip()
        usage = envelope.get("usage") or {}
    except Exception:
        # A stray non-JSON line can land in stdout; keep whatever text there was.
        text = raw.strip()[:4000]
    return out.returncode, text, usage


def _deliver(*, thread_id: str, owner_id: int, job_id: str, label: str,
             status: str, text: str, exit_code: int = 0, tree_dirty: bool = False) -> None:
    """Post the result into the chat and ask it to continue. Mirrors
    run_report's autonomous-tier delivery so the client renders it the same way.

    THE OUTCOME IS STATED, NOT IMPLIED. This used to say "finished" in the
    continuation seed for every outcome, including a run the wall-clock bound
    had killed, so a chat whose agent was cut off at ten minutes was told the
    agent had finished and produced nothing useful. That reads as "the work was
    not worth doing" when the truth was "the work was stopped mid-way", and the
    two call for opposite next steps: drop it, versus re-run it smaller."""
    from services import chat_store, ownership, chat_drain
    prev = ownership.owner_or_none()
    ownership.set_current_owner(owner_id)
    timed_out = exit_code == TIMED_OUT
    mins = max(1, _timeout_s() // 60)
    try:
        if timed_out:
            body = (f"Background agent ran out of time: {label}\n\n"
                    f"It was stopped at the {mins}-minute wall-clock bound before "
                    f"it could finish.\n\n{text or '(nothing had been emitted yet)'}")
        elif status == "done" and text:
            body = f"Background agent finished: {label}\n\n{text}"
        elif status == "done":
            body = f"Background agent finished: {label}\n\n(It produced no text.)"
        else:
            body = (f"Background agent stopped: {label} (exit {exit_code})\n\n"
                    f"{text or '(no output)'}")
        if tree_dirty:
            body += ("\n\n**Warning: the workspace tree has uncommitted changes after "
                     "this run. Review and commit or reset before the next apply.**")
        chat_store.append_message(
            thread_id, "assistant", body,
            # ASKS NOTHING: a background agent reporting what it did. Quiet
            # attributed block, same as a run report. See run_report.py for why
            # this replaced `autonomous: True`.
            meta={"kind": "background_result", "asks": "notify",
                  "sender": f"run:{job_id}", "job": job_id, "status": status},
        )
        if timed_out:
            seed = (
                "[AUTONOMOUS CONTINUATION - not a message from the operator]\n"
                f"A background agent you started ({label!r}) was STOPPED at the "
                f"{mins}-minute wall-clock bound before it finished. It did not "
                "fail at the task and it did not decide there was nothing to "
                "report. Whatever it had emitted is in the message directly "
                "above; treat it as partial. Decide and act: either re-run it "
                "as smaller pieces, or do the work yourself. Tell the operator "
                "in one line that it ran out of time, and do not present its "
                "absence as an answer."
            )
        else:
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


def _refuse(a: argparse.Namespace, reason: str) -> int:
    """Close an already-opened Job as failed and say why, in the chat.

    The refusals this serves happen BEFORE any model turn: an unresolvable scope
    and an unbuildable sandbox. Both used to be impossible to reach (the scope
    fell back to the platform checkout, and nothing asked for confinement), and
    both must now land somewhere a person can see. So the refusal writes the
    same three things a finished run writes -- a terminal status file, a message
    in the chat, a closed Job -- rather than exiting quietly and leaving a
    `running` Job and a chat that waits forever."""
    from services import jobs
    log.error("background agent refused: job=%s scope=%s: %s", a.job_id, a.scope, reason)
    _write_status(_job_dir(a.job_id), outcome="failed", exit_code=REFUSED, tree_dirty=False)
    try:
        _deliver(thread_id=a.thread, owner_id=a.owner, job_id=a.job_id,
                 label=a.label or "background agent", status="failed", text=reason,
                 exit_code=REFUSED, tree_dirty=False)
    except Exception as e:
        log.warning("background agent refusal delivery failed for job %s: %s", a.job_id, e)
    jobs.finish(a.job_id, status="failed", exit_code=REFUSED, evidence={"summary": reason})
    return 0


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="background_runs run")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--thread", required=True)
    ap.add_argument("--owner", required=True, type=int)
    ap.add_argument("--scope", default="workshop")
    ap.add_argument("--label", default="")
    ap.add_argument("--prompt-file", required=True)
    a = ap.parse_args(argv)

    # Wire logging to stdout so every message ends up in runner.log. The root
    # cause of 0-byte logs: the runner's stdout is redirected to runner.log by
    # the Popen in launch(), but the runner previously produced no output — it
    # captured claude's stdout internally via capture_output=True and only wrote
    # through the logging module which had no configured handlers in this
    # detached subprocess.
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, force=True,
                        format="%(asctime)s %(levelname)s %(message)s")

    log.info("runner start: job=%s thread=%s scope=%s label=%r",
             a.job_id, a.thread, a.scope, a.label)

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

    # A REFUSAL IS AN OUTCOME, NOT A DISAPPEARANCE. The Job is already open
    # above, so both refusals below close it as failed and deliver the reason
    # into the chat through the same `_deliver` seam every other outcome uses.
    # Returning early without that would leave a Job running forever and a chat
    # waiting for a result that is never coming.
    cwd = _scope_cwd(a.scope)
    if cwd is None:
        return _refuse(a, _refusal_text(a.scope))

    from services import scoped_exec
    try:
        exit_code, text, usage = _run_claude(prompt, cwd, a.owner, a.scope)
    except (scoped_exec.ConfinementUnavailable, scoped_exec.TurnUserUnavailable) as e:
        return _refuse(a, f"Refused: this background agent cannot run confined. {e}")
    status = "done" if exit_code == 0 else "failed"

    log.info("claude finished: exit=%d status=%s text_chars=%d", exit_code, status, len(text))

    tree_dirty = _check_tree_dirty(cwd)
    if tree_dirty:
        log.warning("tree is dirty: uncommitted changes in workspace after run")

    outcome = "timed_out" if exit_code == TIMED_OUT else status
    _write_status(_job_dir(a.job_id), outcome=outcome, exit_code=exit_code,
                  tree_dirty=tree_dirty)

    # Deliver first, so the chat has the result even if the ledger write hiccups;
    # then close the Job (finish() also runs run_report, which is a no-op here
    # because a background Job worked no ticket).
    try:
        _deliver(thread_id=a.thread, owner_id=a.owner, job_id=a.job_id,
                 label=a.label or "background agent", status=status, text=text,
                 exit_code=exit_code, tree_dirty=tree_dirty)
    except Exception as e:
        log.warning("background agent delivery failed for job %s: %s", a.job_id, e)

    ev = {"summary": text[:2000]} if text else {"summary": ""}
    if usage:
        ev["usage"] = usage
    jobs.finish(a.job_id, status=status, exit_code=exit_code, evidence=ev)

    reaped = _reap_old_job_dirs()
    if reaped:
        log.info("reaped %d old job dir(s) older than %d days", reaped, _REAP_AFTER_DAYS)

    return 0


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "run":
        raise SystemExit(run(sys.argv[2:]))
    sys.stderr.write("usage: python -m services.background_runs run --job-id ... "
                     "--thread ... --owner ... --prompt-file ...\n")
    raise SystemExit(2)


if __name__ == "__main__":
    main()
