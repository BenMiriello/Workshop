"""What a turn on a local model does not carry, because its window is small.

A local model has an 80k-token window and the CLI compacts at about three
quarters of it. A default turn spends 40k or more of that before the first word:
the CLI's built-in tool schemas (23 tools, ~17k tokens), the account's own
CLAUDE.md files and memory index (~13k more than a lean turn), and whatever the
platform adds. What is left is room for one large file read, so the chat
compacts, lands back near where it was, and the CLI stops it with "Autocompact is
thrashing" (2026-10-11).

So a turn the CLI runs against another provider's server (`redirects_cli`)
starts as an app's own assistant does (`app_scope_turn`): no settings files, no
CLAUDE.md, no auto-memory, and only the built-in tools a coding turn uses. The
platform's own instructions for the scope still arrive as they do for any turn,
repo rules included. Measured against CLI 2.1.296 with a scripted model: tool
schemas 66.9k to 13.1k characters, first-request messages 63.8k to 11.1k.
"""
from __future__ import annotations

import json
from pathlib import Path

from services import app_scope_turn, tool_policy, turn_sandbox, turn_trash

__all__ = ["BUILTIN_TOOLS", "MAX_TOOL_CALLS", "SERVER_SIDE_TOOLS", "cli_env", "cli_flags",
           "settings"]

GUARD = Path(__file__).resolve().parents[1] / "scripts" / "local_turn_guard.py"
# The guard's source travels in the turn's environment, which its hook
# inherits, rather than inline in the hook's command: the CLI quotes a hook's
# whole command in every refusal it relays, and a local model's window paid
# about 4,000 characters for each one.
GUARD_ENV = "WORKSHOP_GUARD_SRC"

# One turn's tool calls before the guard asks for the report. A research turn
# that needs more ends with its notes and the operator's next message goes on.
MAX_TOOL_CALLS = 150

# Output the CLI hands back to the model, by characters and by tokens. Its own
# limits (30k characters, 25k tokens) are a third of this window.
BASH_OUTPUT_CHARS = 8000
MCP_OUTPUT_TOKENS = 5000

# Rules, not a model's judgement: the turn already runs confined to its
# project. What asks is decided per call by `tool_policy` (a delete asks, and in
# the open posture everything else runs); these are the floor under it. DENY
# never runs whatever the hook says, ALLOW keeps the common research tools
# running in a mode the operator chose. Patterns on a shell command are speed
# bumps, not a boundary, which is why the confinement is what stops a write
# elsewhere.
ALLOW = ("WebFetch", "WebSearch", "Bash(curl *)", "Bash(wget *)", "Bash(rg *)",
         "Bash(jq *)", "Bash(python3 *)", "Bash(mkdir *)")
DENY = ("Bash(sudo *)", "Bash(git push *)", "Bash(dd *)", "Bash(mkfs*)")


def settings(posture: str | None, *, shell: bool = False) -> str:
    """The `--settings` document: the permission rules, the loop guard and the
    tool policy. Given as a flag because the turn loads no settings files
    (`app_scope_turn`). The guard runs as its own source, like the policy hook,
    because an app's turn is confined to a tree that does not hold this one.
    `shell`: the turn has Bash, so its rm goes to the trash (`turn_trash`)."""
    guard = [{"matcher": "*", "hooks": [{
        "type": "command", "command": f'python3 -c "${GUARD_ENV}"'}]}]
    pre = list(guard)
    if posture:
        pre.append(tool_policy.hook_entry(posture))
    doc: dict = {
        "permissions": {"allow": list(ALLOW), "deny": list(DENY)},
        # The guard also runs after each call, to record its answer.
        "hooks": {"PreToolUse": pre, "PostToolUse": guard},
    }
    if shell:
        doc["hooks"]["SessionStart"] = [turn_trash.hook_entry()]
    if (box := turn_sandbox.settings(posture)):
        doc["sandbox"] = box
    return json.dumps(doc)

# Built-in tools the API serves itself rather than the model calling out:
# against a local model's server they answer nothing, so they are not offered.
SERVER_SIDE_TOOLS: tuple[str, ...] = ("WebSearch",)

# The built-in tools a local coding turn uses. Everything else the CLI offers
# (scheduling, worktrees, notifications, notebooks, subagents) costs 54k
# characters of schema on every request and is not what a local model is for.
BUILTIN_TOOLS: tuple[str, ...] = (
    "Bash", "Read", "Edit", "Write", "Glob", "Grep", "WebFetch")


def cli_flags(*, tools_narrowed: bool, posture: str | None = None,
              shell: bool = False) -> list[str]:
    """The flags of a lean local turn. `tools_narrowed` is a turn whose caller
    already chose its tools, which keeps its own list; `posture` is the turn's
    `tool_policy` posture, None for none; `shell`: see `settings`."""
    flags = [*app_scope_turn.CLI_FLAGS, "--settings", settings(posture, shell=shell)]
    if not tools_narrowed:
        flags += ["--tools", ",".join(BUILTIN_TOOLS)]
    return flags


def cli_env(env: dict, turn_id: str = "") -> dict:
    """`env` with CLAUDE.md loading and auto-memory switched off, tool output
    bounded, and the guard told which turn it is counting."""
    return {**app_scope_turn.cli_env(env),
            "BASH_MAX_OUTPUT_LENGTH": str(BASH_OUTPUT_CHARS),
            "MAX_MCP_OUTPUT_TOKENS": str(MCP_OUTPUT_TOKENS),
            "WORKSHOP_GUARD_TURN": turn_id,
            GUARD_ENV: GUARD.read_text(),
            "WORKSHOP_GUARD_MAX_CALLS": str(MAX_TOOL_CALLS)}
