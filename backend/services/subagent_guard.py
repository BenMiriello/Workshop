"""PreToolUse hook: a chat turn's subagent must run in the FOREGROUND.

Root cause (docs/platform-redesign/chat-agent-integration-2026-08-18.md, and
verified again 2026-08-20 on a real dead turn): a background Agent/Task call
only delivers its result if the SAME live process survives to receive the CLI's
own `task_notification` and answer it. That process does not survive, so the
work is lost and the model -- believing it will be called back -- writes a
"holding until the mappers report" closing line and stops. The chat then shows
a promised follow-up that can never arrive.

WHY IT DOES NOT SURVIVE, CORRECTED 2026-08-31. This docstring and the deny
reason below both used to say "a chat turn is a one-shot `claude -p` process:
the moment it emits text with no pending tool call it is DONE and the process
exits." That is FALSE, and three places in this tree say so: `_retire_turn_proc`
and `turn_end`'s own header both record that under `--input-format stream-json`
the CLI does NOT exit when a turn finishes -- stdin is still open, so it idles
for the next message -- and the stdin FIFO is opened O_RDWR precisely so the
child never sees EOF. That architecture predates this guard by three weeks, so
the explanation was wrong when it was written.

The real reason is that WE end it: on the turn's balanced `result`,
`_retire_turn_proc` sends SIGTERM to the whole process GROUP, which includes any
subagent still running in the background. (The frequent backend restarts named
in the design doc are a second, independent way to lose it.) The outcome the
guard prevents is real and was measured twice; the mechanism is ours, not the
CLI's. Getting this right matters because the wrong version reads as "the tool
cannot do this", which is the kind of claim that stops anyone looking again.

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

# WHAT THE CALLER IS TOLD. Two things this used to get wrong, both fixed
# 2026-08-31: it asserted a mechanism that is not true (see the docstring), and
# it said "backgrounding is not available here" while the platform's own
# background primitive sat unmentioned in the same toolbox. A model that
# genuinely wanted out-of-band work read that as "give up".
#
# So: say what to do, and name the tool that does the thing being refused.
# Several foreground subagents issued in ONE message still run concurrently,
# which is what most callers actually wanted from backgrounding.
DENY_REASON = (
    "In this chat a subagent runs in the FOREGROUND, and the Agent/Task tool "
    "backgrounds by default, so you have to say so: re-issue this call with "
    "run_in_background set to false. A backgrounded one is killed with the turn "
    "before it can report, so its result never arrives. The turn has no time "
    "limit, so waiting inline is safe, and several subagents issued in one "
    "message still run at the same time. If you want work to outlive this turn "
    "and report back into the chat later, that is what spawn_background is for."
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
