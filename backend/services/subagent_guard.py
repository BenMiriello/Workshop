"""PreToolUse hook: a chat turn's subagent may not run in the background.

Root cause (docs/platform-redesign/chat-agent-integration-2026-08-18.md): a
background Agent/Task call only delivers its result if the SAME live process
survives long enough to receive the CLI's own `task_notification` and answer
it. That already works when nothing disturbs the process (verified empirically
2026-08-19). But this box restarts the backend often -- every concurrent chat
turn's own final step runs `apply.sh`, and reattaching a turn's OUTER process
across a restart is a solved problem; reattaching a background subagent's
IN-FLIGHT work across one is not. Measured 2026-08-19 on a real thread
(chat 89223a47...): a background investigation was reported "interrupted by a
session restart" twice in seven minutes, each retry repeating the same failure
before the model gave up and did the work in the foreground instead.

A prompt-only fix (chat_context.DELEGATION_RULE) shipped 2026-08-18 and did not
hold -- the model still tried it hours later. This hook makes the failure
structurally impossible instead of asking nicely: reject the call before it
starts, so the model is forced onto the reliable path (a synchronous subagent
inside the same reply) on the first attempt. Chat turns have no wall-clock cap
(see the runtime briefing), so waiting in the foreground costs nothing a
background attempt would have saved.

Scope: chat-turn subprocesses only, wired via `--settings` in
`build_streaming._build_cmd`. Does not touch the operator's own interactive
CLI sessions or their `~/.claude/settings.json`.
"""
import json
import sys

DENIED_TOOLS = ("Agent", "Task")

DENY_REASON = (
    "Backgrounded subagents are not available in this chat: this backend "
    "restarts often (any concurrent turn's apply.sh can trigger one), and a "
    "background subagent's in-flight work does not survive that the way the "
    "turn's own process does. Retry the same call with run_in_background "
    "omitted or set to false -- the turn has no time limit, so waiting for it "
    "inline is safe and is the only path that reliably delivers a result."
)


def deny_reason(tool_name: str, tool_input: dict) -> str | None:
    """None to allow; the message to show the model, to deny.

    Pure and total: same inputs, same answer, no I/O. What `main` reads off
    stdin and what a test constructs directly are the same call."""
    if tool_name not in DENIED_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return None
    if tool_input.get("run_in_background"):
        return DENY_REASON
    return None


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
