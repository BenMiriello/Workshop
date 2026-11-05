"""Puts the trash-moving `rm` (`turn_rm`) first on a turn's PATH and tells the
turn how deleting works there.

Claude Code's own mechanism does both: a SessionStart hook may append shell to
`CLAUDE_ENV_FILE`, which every Bash call of the session sources, and may return
`additionalContext`, which the model reads. The installer copies `turn_rm` into
`tool_policy.shim_dir()`, prepends that to PATH, and leaves the per-session mark
`tool_policy.trash_active` reads, last, so the policy never trusts a PATH that
was not set. Any failure installs nothing and says nothing: rm is then the
system's and deleting asks, as before.

The explanation is said once per session and again after a compaction or a
clear, which are the points a session forgets it. A session is resumed every
turn, so saying it on every start would repeat it into the history each turn.
The installer runs inline (`python3 -c`), like the policy hook, because a turn
is confined to a tree that does not hold this one.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from services import tool_policy


def context(trash_days: float) -> str:
    days = f"{trash_days:g}"
    return (
        "HOW DELETING WORKS IN THIS SESSION\n"
        f"- `rm` and `unlink` move what they remove to the Workshop trash, where it "
        f"can be restored for {days} days, and they run without asking. Scratch "
        "paths (tmp/, scratch/, /tmp) and dependency or cache folders "
        "(node_modules, __pycache__, .venv, dist and the like) are deleted "
        "outright, since they hold nothing to lose. `rmdir` removes only empty "
        "folders, so it runs too.\n"
        "- Git saves before it discards: deleting a branch, dropping a stash, and "
        "a checkout, restore or `reset --hard` over unsaved changes first record "
        "what they would lose under `refs/wip/`, so these run without asking.\n"
        "- Cleaning up after yourself is part of finishing the work, and you are "
        "authorized to do it without asking: your scratch files, test output, and "
        "your worktrees and branches once their work is merged. Do it in the same "
        "turn. Never list it as waiting for approval.\n"
        "- Still asked of the operator, by a card, because it cannot be undone: "
        "`/bin/rm` (a permanent delete, for when disk space must come back now), "
        "deleting through a database, an HTTP DELETE, an app tool that deletes, a "
        "container delete, and a force push. When the task needs one, make the "
        "call; the card is the question, so do not ask in prose first. If the "
        "card is not answered within a minute the call is refused: carry on with "
        "the rest and say in one line at the end what did not run.\n"
        "- To restore something: each removal is a folder under the "
        "`.workshop-trash-<uid>` folder at the top of its disk's writable tree, "
        "holding `payload` and `entry.json` (which records where it came from). "
        "Move `payload` back to that path."
    )


_INSTALL = r'''
import json, os, sys
try:
    event = json.load(sys.stdin)
except Exception:
    event = {}
session = str(event.get("session_id") or "")
mark = os.path.join(SHIM_DIR, "on-" + session)
try:
    if session and os.path.exists(mark):
        os.remove(mark)
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file and session and session.replace("-", "").isalnum():
        os.makedirs(SHIM_DIR, mode=0o700, exist_ok=True)
        tmp = os.path.join(SHIM_DIR, ".rm-%d" % os.getpid())
        with open(tmp, "w") as f:
            f.write("#!/usr/bin/env python3\n" + RM_SOURCE)
        os.chmod(tmp, 0o755)
        os.replace(tmp, os.path.join(SHIM_DIR, "rm"))
        try:
            os.symlink("rm", os.path.join(SHIM_DIR, "unlink"))
        except FileExistsError:
            pass
        with open(env_file, "a") as f:
            f.write('export PATH="%s:$PATH"\n' % SHIM_DIR)
        open(mark, "w").close()
        told = os.path.join(os.path.dirname(env_file), "workshop-trash-told")
        if event.get("source") in ("startup", "clear", "compact") or not os.path.exists(told):
            open(told, "w").close()
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "SessionStart", "additionalContext": CONTEXT}}))
except Exception:
    pass
'''


def installer_source(trash_days: float) -> str:
    rm_source = Path(__file__).with_name("turn_rm.py").read_text()
    return (f"SHIM_DIR = {tool_policy.shim_dir()!r}\nRM_SOURCE = {rm_source!r}\n"
            f"CONTEXT = {context(trash_days)!r}\n" + _INSTALL)


def hook_entry() -> dict:
    """The settings `SessionStart` entry. Reads the trash window the storage
    pass empties by, so what the turn is told is what happens."""
    from services.storage import policy
    source = installer_source(policy.read()["trash_days"])
    return {"hooks": [{"type": "command", "command": f"python3 -c {shlex.quote(source)}"}]}
