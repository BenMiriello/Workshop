"""What a chat turn may do without asking: everything but deleting.

Reading, searching, fetching and using an app's tools run. An edit runs, because
a turn's edits are reversible (the checkpoint before every turn, git history).
A delete is the one thing that cannot be taken back, so it asks the operator.
What can be taken back is not a delete: the turn's own `rm` moves what it removes
to the trash (`turn_rm`, installed by `turn_trash`), and the hook saves what a
git command would discard under `refs/wip/` before letting it run
(`tool_policy_git.keep`). A permanent delete that has such an undoable
equivalent is refused with that equivalent named, so the turn redoes it at once
instead of waiting on a card nobody may be looking at.
The rule is applied by a PreToolUse hook that answers the CLI per call, which
is the one place every tool, built-in or MCP, passes through. The hook is this
file itself, run as `python3 -c <its source>` (`hook_entry`): a turn is
confined to its scope, and the backend's tree is not inside most scopes, so a
path to a script would answer nothing there.

Two postures, picked per turn by `posture`:

- OPEN: the turn allows every call that does not delete. This is what `auto`
  means on every model: the CLI is not run at its own `auto`, whose second-model
  check refused ordinary work and told the turn to stop (`model_caps.AUTO_RUNS_AS`).
- GUARD: the turn's own mode decides, and a delete asks on top. For a mode the
  operator chose (Manual, Plan, Accept edits), which this never loosens.

A bypass turn is unattended and has no one to ask; it gets no hook.

`allow_rules` are the ones the operator gave with "Allow and stop asking", in
the CLI's own rule syntax (`Tool` or `Tool(content)`, `prefix:*` for a prefix).
A call they cover is not asked again, deletes included: that is what the
operator chose. For a delete that means the rule covers the deleting command
itself, so a rule given for a harmless prefix never carries a delete chained
after it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Iterable

from services.tool_policy_shell import (  # hook: joined
    _HTTP_DELETE, _SQL_DELETE, _parts, _without_heredocs, command_deletes, deleting_parts,
    preserve,
)

OPEN = "open"
GUARD = "guard"

# Verbs that, in an MCP tool's own name, mean it destroys something. A tool that
# hides a delete behind another verb asks nothing; the contract for app tools
# tells apps to name them plainly.
_DELETE_VERBS = frozenset({
    "delete", "remove", "destroy", "drop", "purge", "erase", "wipe", "unlink",
    "truncate", "uninstall", "discard", "rm",
})


def _words(name: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return {w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w}


# The undoable way to do what a permanent delete was reaching for, by why it
# deletes. Offered only where the turn's `rm` goes to the trash.
_UNDOABLE = {
    "find -delete": "`find -delete` deletes for good. Use `-exec rm {} +` instead: "
                    "this session's rm moves what it removes to the trash and runs "
                    "without asking.",
    "a program that deletes files": "Deleting from inside a program is permanent. "
                                    "Remove the files with rm in the shell instead: "
                                    "it moves them to the trash and runs without asking.",
    "git clean": "`git clean` deletes untracked files for good. List them with "
                 "`git clean -n` (same flags) and remove them with rm, which moves "
                 "them to the trash and runs without asking.",
    "git worktree remove discarding changes": "That worktree holds uncommitted files, "
                                              "which `git worktree remove --force` "
                                              "deletes for good. Run `rm -rf <the "
                                              "worktree>` and then `git worktree "
                                              "prune` instead: rm moves the whole copy "
                                              "to the trash, and both run without asking.",
}


def shim_dir() -> str:
    """Where `turn_trash` installs the turn's rm (per user, outside any tree)."""
    return f"/tmp/workshop-bin-{os.getuid()}"


def trash_active(session_id: str | None) -> bool:
    """Whether the turn's rm moves to the trash in this session: the installer
    leaves a mark per session only after it put rm first on that PATH."""
    if not session_id or not re.fullmatch(r"[A-Za-z0-9-]+", session_id):
        return False
    return (os.path.exists(os.path.join(shim_dir(), f"on-{session_id}"))
            and os.access(os.path.join(shim_dir(), "rm"), os.X_OK))


def deletes(tool_name: str, tool_input: dict | None, cwd: str | None = None,
            trash: bool = False, kept: frozenset[str] = frozenset()) -> str | None:
    """Why this call deletes something, or None. `cwd`: where the turn runs.
    `trash` and `kept`: see `tool_policy_shell`."""
    tool_input = tool_input or {}
    if tool_name == "Bash":
        return command_deletes(str(tool_input.get("command") or ""), cwd, trash, kept)
    if tool_name.startswith("mcp__"):
        verbs = _words(tool_name.rsplit("__", 1)[-1]) & _DELETE_VERBS
        if verbs:
            return f"{tool_name.rsplit('__', 1)[-1]} ({sorted(verbs)[0]})"
    return None


def _rule_parts(rule: str) -> tuple[str, str | None]:
    m = re.match(r"^([^()]+)\((.*)\)$", rule.strip())
    return (m.group(1), m.group(2)) if m else (rule.strip(), None)


def rule_covers(rule: str, tool_name: str, tool_input: dict | None) -> bool:
    """Whether an allow rule, in the CLI's syntax, covers this call."""
    name, content = _rule_parts(rule)
    if name.endswith("*"):
        if not tool_name.startswith(name[:-1]):
            return False
    elif name != tool_name and not (name.startswith("mcp__") and name.count("__") == 1
                                     and tool_name.startswith(name + "__")):
        return False
    if content is None:
        return True
    subject = str((tool_input or {}).get("command") or (tool_input or {}).get("file_path") or "")
    if content.endswith(":*"):
        return subject.startswith(content[:-2])
    if content.endswith("*"):
        return subject.startswith(content[:-1])
    return subject == content


def _delete_allowed(rules: list[str], tool_name: str, tool_input: dict | None,
                    cwd: str | None = None) -> bool:
    """Whether the operator's rules cover this delete: for a shell command,
    each deleting command in it, read on its own."""
    if tool_name != "Bash":
        return any(rule_covers(r, tool_name, tool_input) for r in rules)
    command = str((tool_input or {}).get("command") or "")
    data_out = _without_heredocs(command)
    whole = any(rule_covers(r, tool_name, tool_input) for r in rules)
    if _SQL_DELETE.search(data_out) or _HTTP_DELETE.search(data_out):
        return whole and len(_parts(data_out)) == 1
    parts = [p for p, _ in deleting_parts(command, cwd)]
    return bool(parts) and all(any(rule_covers(r, "Bash", {"command": p}) for r in rules) for p in parts)


def _undoable(command: str, cwd: str | None, kept: frozenset[str]) -> str | None:
    """What to do instead, when every deleting part has an undoable way."""
    data_out = _without_heredocs(command)
    if _SQL_DELETE.search(data_out) or _HTTP_DELETE.search(data_out):
        return None
    found = deleting_parts(command, cwd, True, kept)
    ways = [_UNDOABLE.get(why) for _, why in found]
    return " ".join(dict.fromkeys(ways)) if found and all(ways) else None


def decision(posture: str, tool_name: str, tool_input: dict | None,
             allow_rules: Iterable[str] = (), cwd: str | None = None,
             trash: bool = False, kept: frozenset[str] = frozenset()) -> tuple[str | None, str]:
    """("allow" | "ask" | "deny" | None, reason). None leaves the call to the
    turn's mode. "deny" names the undoable way to do the same thing."""
    rules = list(allow_rules)
    why = deletes(tool_name, tool_input, cwd, trash, kept)
    if why:
        if _delete_allowed(rules, tool_name, tool_input, cwd):
            return "allow", "allowed for this chat by the operator"
        if trash and tool_name == "Bash":
            instead = _undoable(str((tool_input or {}).get("command") or ""), cwd, kept)
            if instead:
                return "deny", instead
        return "ask", f"Deletes for good ({why}), so it asks the operator."
    if any(rule_covers(r, tool_name, tool_input) for r in rules):
        return "allow", "allowed for this chat by the operator"
    if posture == OPEN:
        return "allow", "does not delete"
    return None, ""


def posture(requested: str | None) -> str | None:
    """The posture for a chat that asked for mode `requested` (None: the
    default, which is `auto`)."""
    if requested == "bypassPermissions":
        return None
    return OPEN if requested in (None, "", "auto") else GUARD


def hook_source() -> str:
    """This module and the readers it joins as one program, for `python3 -c`."""
    here = Path(__file__)
    parts = [here.with_name(f"tool_policy_{n}.py").read_text() for n in ("git", "shell")]
    joined = "\n".join([*parts, here.read_text()])
    joined = re.sub(r"^from services\.tool_policy_\w+ import (\([^)]*\)|[^\n]*)\n", "", joined, flags=re.M)
    joined = joined.replace("from __future__ import annotations\n", "")
    return "from __future__ import annotations\n" + joined


def hook_entry(posture: str) -> dict:
    """The settings `PreToolUse` entry that applies `posture` to every call. The
    operator's standing rules travel in `WORKSHOP_ALLOW_RULES` (a JSON list)."""
    return {"matcher": "*", "hooks": [{
        "type": "command", "command": f"python3 -c {shlex.quote(hook_source())} {shlex.quote(posture)}"}]}


def _hook_main() -> int:
    """Print the CLI's decision for the call on stdin, or nothing to leave it to
    the turn's mode. Any failure prints nothing: a broken hook must neither stop
    nor loosen a turn."""
    try:
        event = json.load(sys.stdin)
        rules = json.loads(os.environ.get("WORKSHOP_ALLOW_RULES") or "[]")
        tool, tool_input = str(event.get("tool_name") or ""), event.get("tool_input") or {}
        cwd = str(event.get("cwd") or "") or None
        kept = preserve(str(tool_input.get("command") or ""), cwd) if tool == "Bash" else frozenset()
        verdict, reason = decision(
            sys.argv[1] if len(sys.argv) > 1 else GUARD, tool, tool_input,
            [r for r in rules if isinstance(r, str)], cwd,
            trash_active(str(event.get("session_id") or "")), kept)
    except Exception:
        return 0
    if verdict:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict,
            "permissionDecisionReason": reason,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(_hook_main())
