"""PreToolUse hook: a chat turn's subagent must run in the FOREGROUND.

Root cause (docs/platform-redesign/chat-agent-integration-2026-08-18.md, and
verified again 2026-08-20 on a real dead turn): a background Agent/Task call
only delivers its result if the SAME live process survives to receive the CLI's
own `task_notification` and answer it. A chat turn is a one-shot `claude -p`
process: the moment it emits text with no pending tool call it is DONE and the
process exits. Nothing in build_streaming re-invokes it to collect a background
subagent's late result, and on top of that this backend restarts often (any
concurrent turn's apply.sh can trigger one). So a backgrounded subagent's work
is lost, and the model -- believing it will be called back -- writes a "holding
until the mappers report" style closing line and stops. The operator then sees
a chat that promised a follow-up it can never make.

THE HOLE THIS CLOSES. The Agent/Task tool in this CLI (2.1.143) runs a subagent
in the BACKGROUND BY DEFAULT: omit `run_in_background` and the call returns
"Async agent launched successfully" immediately. The first version of this hook
denied only an EXPLICIT `run_in_background: true`, so the default-background
call (the field omitted, which is what the model naturally emits) sailed
straight through and backgrounded anyway. That is the exact failure on turn
b35b74f5 (2026-08-20). The fix is to fail closed: allow the call ONLY when it
carries `run_in_background: false` explicitly. Anything else -- true, omitted,
or a non-bool -- is denied with an instruction to re-issue it foreground.

A prompt-only fix (chat_context.DELEGATION_RULE) has been tried twice and did
not hold on its own; the prompt now states the rule truthfully so the common
call is foreground on the first try, and this hook is the structural guarantee
behind it. Chat turns have no wall-clock cap (see the runtime briefing), so
waiting for a foreground subagent inline costs nothing a background attempt
would have saved.

Scope: chat-turn subprocesses only, wired via `--settings` in
`build_streaming._build_cmd`. Does not touch the operator's own interactive CLI
sessions, their `~/.claude/settings.json`, or Workshop autonomy runs (which are
their own durable processes and may legitimately background work).
"""
import json
import sys

DENIED_TOOLS = ("Agent", "Task")

DENY_REASON = (
    "In this chat a subagent must run in the FOREGROUND, and the Agent/Task "
    "tool backgrounds by default, so you must say so explicitly: re-issue this "
    "call with run_in_background set to false. Backgrounding is not available "
    "here -- this turn is a one-shot process that exits when you stop emitting "
    "tool calls, so a background subagent's result is never delivered and the "
    "turn dies waiting for it. The turn has no time limit, so waiting for the "
    "subagent inline is safe and is the only path that reliably returns its "
    "result into this reply."
)


def _is_explicit_false(value) -> bool:
    """True only for a genuine foreground request. A bool False, or the strings
    a JSON-ish caller might send ("false"/"0"/"no"), count; None/absent do not."""
    if value is False:
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "no"):
        return True
    return False


def deny_reason(tool_name: str, tool_input: dict) -> str | None:
    """None to allow; the message to show the model, to deny.

    Fail closed: a chat subagent is allowed ONLY when it explicitly asks for the
    foreground. Omitting run_in_background means background in this CLI, which is
    the failure this hook exists to stop, so an omitted flag is denied too.

    Pure and total: same inputs, same answer, no I/O. What `main` reads off
    stdin and what a test constructs directly are the same call."""
    if tool_name not in DENIED_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        # We cannot read the flag off a malformed input; fail open rather than
        # wedge the turn on a shape we do not understand. This is a reliability
        # guard, not a security boundary.
        return None
    if _is_explicit_false(tool_input.get("run_in_background")):
        return None
    return DENY_REASON


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # fail open -- this is a reliability guard, not a security one
    reason = deny_reason(data.get("tool_name") or "", data.get("tool_input") or {})
    if reason:
        sys.stderr.write(reason)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
