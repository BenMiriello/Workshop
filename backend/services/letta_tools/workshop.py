"""Workshop TODO + log + file tools (Letta-sandbox-safe).

CRITICAL: Letta executes tool functions in an isolated sandbox that does NOT
inherit the module's top-level imports or constants. Every function below
must import its dependencies INSIDE the function body and inline any constants.
"""
from __future__ import annotations


def wkshp_read_todo(section: str = "all", max_items: int = 20) -> str:
    """Read ~/workshop/TODO.md and return a structured summary.

    Args:
        section: Which section to return. One of "open" (only `[ ]` items),
            "blocked" (only `[~]`), "all" (everything: counts + open list).
        max_items: Maximum items to include in the returned list.

    Returns:
        A plain-text summary with counts and the most relevant items.
    """
    from pathlib import Path
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    if not todo_path.exists():
        return "TODO.md does not exist."
    lines = todo_path.read_text().splitlines()
    open_items = [l for l in lines if l.startswith("[ ] ")]
    blocked = [l for l in lines if l.startswith("[~] ")]
    done = [l for l in lines if l.startswith("[x] ")]
    s = section.lower()
    if s == "open":
        pick = open_items[:max_items]
        return f"OPEN tasks ({len(open_items)} total):\n" + "\n".join(pick)
    if s == "blocked":
        pick = blocked[:max_items]
        return f"BLOCKED tasks ({len(blocked)} total):\n" + "\n".join(pick)
    pick = open_items[:max_items]
    return (
        f"TODO summary: open={len(open_items)} blocked={len(blocked)} done={len(done)}\n"
        f"Top {len(pick)} open tasks:\n" + "\n".join(pick)
    )


def wkshp_propose_task(task_text: str, urgency: str = "normal") -> str:
    """Append a new `[ ] task` line to ~/workshop/TODO.md.

    Args:
        task_text: One-line task description. Must be concrete and actionable.
        urgency: One of "low", "normal", "high". High-urgency tasks are
            inserted at the top of the open list; normal/low are appended.

    Returns:
        Confirmation string with the line that was added.
    """
    from pathlib import Path
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    if not task_text or len(task_text.strip()) < 5:
        return "ERROR: task_text too short"
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    line = f"[ ] {task_text.strip()} — proposed by overseer ({urgency})"
    content = todo_path.read_text()
    if urgency == "high":
        lines = content.splitlines()
        for i, l in enumerate(lines):
            if l.startswith("[ ] "):
                lines.insert(i, line)
                break
        else:
            lines.append(line)
        todo_path.write_text("\n".join(lines) + "\n")
    else:
        todo_path.write_text(content.rstrip() + "\n" + line + "\n")
    return f"added: {line}"


def wkshp_write_log(entry: str, agent_name: str = "agent") -> str:
    """Append a timestamped entry to the ## Log section of TODO.md.

    Args:
        entry: The log line content (no need to include timestamp or prefix).
        agent_name: Short identifier for the agent writing the log (e.g.
            "overseer", "plaid-watcher").

    Returns:
        The full line that was written.
    """
    import re
    from pathlib import Path
    from datetime import datetime, timezone
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    line = f"[{ts}] [{agent_name}] {entry.strip()}"
    content = todo_path.read_text()
    if "## Log" in content:
        new = re.sub(r"(## Log\n(?:<!--[^\n]*-->\n?)*)", lambda m: m.group(1) + line + "\n", content, count=1)
        todo_path.write_text(new)
    else:
        todo_path.write_text(content.rstrip() + f"\n\n## Log\n{line}\n")
    return line


def wkshp_mark_blocked(task_substring: str, reason: str) -> str:
    """Find a `[ ]` line containing `task_substring` and mark it `[~] BLOCKED: <reason>`.

    Args:
        task_substring: Distinctive substring identifying which open task to mark.
            Must match exactly one open `[ ]` line (case-insensitive).
        reason: Specific blocker reason. Should include WHO/WHAT can unblock.

    Returns:
        Confirmation with the rewritten line, or an error if no/multiple matches.
    """
    from pathlib import Path
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    lines = todo_path.read_text().splitlines()
    matches = [(i, l) for i, l in enumerate(lines) if l.startswith("[ ] ") and task_substring.lower() in l.lower()]
    if not matches:
        return f"ERROR: no [ ] task matched substring {task_substring!r}"
    if len(matches) > 1:
        return f"ERROR: substring {task_substring!r} matched {len(matches)} tasks; be more specific"
    i, l = matches[0]
    body = l[4:]
    new_line = f"[~] {body} — BLOCKED: {reason}"
    lines[i] = new_line
    todo_path.write_text("\n".join(lines) + "\n")
    return f"marked blocked: {new_line}"
