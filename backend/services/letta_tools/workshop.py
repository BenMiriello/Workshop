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
    import fcntl
    import re
    from pathlib import Path
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    lock_path = Path("/home/clawd-a/workshop/.runtime/TODO.lock")
    if not task_text or len(task_text.strip()) < 5:
        return "ERROR: task_text too short"
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    line = f"[ ] {task_text.strip()} — proposed by overseer ({urgency})"

    # Topic signature = the significant words of a task, so two reworded versions
    # of the same task ("Onboard new bank for Plaid MCP as per project_plaid_mcp.md"
    # vs "Onboard a new bank into Plaid MCP.") collapse to the same set. Generic
    # filler is dropped so it doesn't inflate the overlap.
    _stop = {
        "a", "an", "the", "for", "to", "of", "in", "into", "on", "onto", "with",
        "as", "per", "and", "or", "any", "from", "new", "recent", "please",
        "ensure", "also", "this", "that", "task", "tasks", "todo", "todos",
        "list", "item", "items",
    }
    def _stem(w: str) -> str:
        # Light plural fold so "bank"/"banks", "line"/"lines" match.
        return w[:-1] if len(w) > 3 and w.endswith("s") else w
    def _sig(text: str) -> frozenset:
        return frozenset(
            _stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())
            if w not in _stop and len(w) > 1
        )

    # Single-flight: serialize the read-modify-write so concurrent overseer ticks
    # (and any other automated writer) can't clobber each other's append.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as _lk:
        fcntl.flock(_lk, fcntl.LOCK_EX)
        content = todo_path.read_text()
        norm = " ".join(task_text.strip().lower().split())
        # Reject vacuous, self-referential meta-tasks. Reviewing/prioritizing the
        # list IS the overseer's job, not a deliverable; these slip past dedupe
        # because each is worded slightly differently. Concrete tasks don't match.
        vacuous_markers = (
            "prioritize the top", "prioritize the first", "prioritize the current",
            "address the top", "address the first", "address the remaining",
            "from the todo list", "from todo list", "task from todo", "open todos",
            "oldest open todos", "open tasks", "tbd from todo",
            "begin processing the oldest", "categorize the first",
            "streamline progress", "streamline workflow", "reduce the backlog",
            "reduce backlog", "either complete or delegate", "address immediate",
            "address critical item", "most urgent open", "most urgent todo",
        )
        if any(m in norm for m in vacuous_markers):
            return ("skipped (vacuous self-referential task — just do the work, "
                    f"don't propose reviewing the list): {task_text.strip()}")
        # Backstop for the reworded-meta spam the marker list keeps missing
        # ("Reduce todo backlog by …", "Review and unblock todos", "Review and
        # finalize the documentation for the new feature release"). A concrete
        # task always names a concrete referent (a file, service, endpoint,
        # feature by name). A vacuous meta-task is built ENTIRELY from
        # list-management vocabulary — management verbs applied to the todo list
        # itself. So: strip stopwords, then if every remaining word is generic
        # management vocab with nothing concrete left, reject it. Real tasks
        # survive because they carry at least one domain word.
        _meta_vocab = {
            # management verbs
            "review", "reviewing", "unblock", "unblocking", "resolve", "resolving",
            "reduce", "reducing", "prioritize", "prioritizing", "address",
            "addressing", "escalate", "escalating", "investigate", "investigating",
            "finalize", "finalizing", "evaluate", "evaluating", "complete",
            "completing", "process", "processing", "categorize", "categorizing",
            "streamline", "streamlining", "begin", "identify", "identifying",
            "analyze", "analyzing", "summarize", "summarizing", "handle", "clear",
            "clean", "cleanup", "triage", "sort", "organize",
            # list-referential / generic-filler nouns
            "backlog", "blocked", "block", "blocker", "open", "priority",
            "prioritized", "urgent", "urgency", "oldest", "newest", "top", "next",
            "step", "root", "cause", "critical", "high", "low", "normal",
            "remaining", "current", "multiple", "several", "clear", "progress",
            "workflow", "immediate", "pending", "actionable", "backlog",
            "documentation", "document", "doc", "draft", "feature", "release",
            "design", "presentation", "escalation", "unblocking", "number",
            "highest", "lowest", "topmost", "possible", "necessary", "clearly",
            # bare numerals / count words the tokenizer keeps
            "1", "2", "3", "4", "5", "one", "two", "three", "four", "five",
            # generic connectors/fillers the stopword set doesn't cover
            "by", "up", "out", "if", "so", "then", "else", "via", "at", "not",
        }
        _content = [
            _stem(w) for w in re.findall(r"[a-z0-9]+", norm)
            if w not in _stop and len(w) > 1
        ]
        if _content and all(_stem(w) in _meta_vocab or w in _meta_vocab for w in _content):
            return ("skipped (vacuous meta-task — pure list-management vocabulary "
                    "with no concrete referent; just do the work): "
                    f"{task_text.strip()}")
        # Near-duplicate dedupe: skip if an open task shares most of its topic
        # signature with this one (containment ≥ 0.7 of the smaller set, ≥ 2 words
        # shared). Catches the reworded-same-task spam that exact-match misses.
        new_sig = _sig(task_text)
        for l in content.splitlines():
            if not l.startswith("[ ] "):
                continue
            ex = _sig(l[4:].split(" — proposed by")[0])
            inter = len(new_sig & ex)
            if inter >= 2 and inter / min(len(new_sig), len(ex)) >= 0.6:
                return f"skipped (near-duplicate of an existing open task): {task_text.strip()}"

        lines = content.splitlines()
        if urgency == "high":
            # Top of the open list.
            for i, l in enumerate(lines):
                if l.startswith("[ ] "):
                    lines.insert(i, line)
                    break
            else:
                lines.append(line)
        else:
            # End of the task area — before the "## Log" section so proposals
            # don't pile up after the log. Fall back to EOF if there's no Log.
            log_idx = next((i for i, l in enumerate(lines) if l.strip() == "## Log"), None)
            if log_idx is None:
                lines.append(line)
            else:
                at = log_idx
                while at > 0 and lines[at - 1].strip() == "":
                    at -= 1
                lines.insert(at, line)
        todo_path.write_text("\n".join(lines).rstrip() + "\n")
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
    import fcntl
    import re
    from pathlib import Path
    from datetime import datetime, timezone
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    lock_path = Path("/home/clawd-a/workshop/.runtime/TODO.lock")
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    line = f"[{ts}] [{agent_name}] {entry.strip()}"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as _lk:
        fcntl.flock(_lk, fcntl.LOCK_EX)
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
    import fcntl
    from pathlib import Path
    todo_path = Path("/home/clawd-a/workshop/TODO.md")
    lock_path = Path("/home/clawd-a/workshop/.runtime/TODO.lock")
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as _lk:
        fcntl.flock(_lk, fcntl.LOCK_EX)
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
