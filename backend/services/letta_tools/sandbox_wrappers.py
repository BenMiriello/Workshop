"""HTTP-proxy versions of letta_tools functions for the Letta sandbox.

Letta's sandbox extracts each function's source via inspect.getsource() and
runs it in isolation. Module-level helpers are NOT available — every tool
must inline its own HTTP-call code.

Pattern (repeated in every tool):
    import httpx
    try:
        with httpx.Client(timeout=N) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/<name>",
                        json={"kwargs": {...}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))

Yes it's repetitive. It's required.
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
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/wkshp_read_todo",
                        json={"kwargs": {"section": section, "max_items": max_items}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def wkshp_propose_task(task_text: str, urgency: str = "normal") -> str:
    """Append a new `[ ] task` line to ~/workshop/TODO.md.

    Args:
        task_text: One-line task description. Must be concrete and actionable.
        urgency: One of "low", "normal", "high". High-urgency tasks are
            inserted at the top of the open list.

    Returns:
        Confirmation string with the line that was added.
    """
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/wkshp_propose_task",
                        json={"kwargs": {"task_text": task_text, "urgency": urgency}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def wkshp_write_log(entry: str, agent_name: str = "agent") -> str:
    """Append a timestamped entry to the ## Log section of TODO.md.

    Args:
        entry: The log line content.
        agent_name: Short identifier for the agent writing the log.

    Returns:
        The full line that was written.
    """
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/wkshp_write_log",
                        json={"kwargs": {"entry": entry, "agent_name": agent_name}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def wkshp_mark_blocked(task_substring: str, reason: str) -> str:
    """Find a `[ ]` line containing `task_substring` and mark it `[~] BLOCKED: <reason>`.

    Args:
        task_substring: Distinctive substring identifying which open task to mark.
        reason: Specific blocker reason.

    Returns:
        Confirmation with the rewritten line, or an error if no/multiple matches.
    """
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/wkshp_mark_blocked",
                        json={"kwargs": {"task_substring": task_substring, "reason": reason}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def local_classify(text: str, categories: str) -> str:
    """Classify `text` into ONE of the provided categories via local Ollama.

    Use this for: "is this a banking event?", "spam or not?", "urgency low/medium/high".
    Do NOT use for: anything requiring more than one word of judgement.

    Args:
        text: The content to classify. Truncated to ~500 chars.
        categories: Comma-separated list of category labels.

    Returns:
        The chosen category (single word/phrase, lowercase). "unknown" if unavailable.
    """
    import httpx
    try:
        with httpx.Client(timeout=30) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/local_classify",
                        json={"kwargs": {"text": text, "categories": categories}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def local_route_decision(task: str) -> str:
    """Decide which LLM tier should handle a task: "local"/"haiku"/"sonnet"/"opus".

    Args:
        task: One-sentence description of what needs to be done.

    Returns:
        One of: "local", "haiku", "sonnet", "opus".
    """
    import httpx
    try:
        with httpx.Client(timeout=15) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/local_route_decision",
                        json={"kwargs": {"task": task}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def local_summarize_short(text: str, max_words: int = 30) -> str:
    """Summarize `text` in `max_words` words or fewer via local Ollama.

    Args:
        text: Content to summarize. Truncated to ~1500 chars.
        max_words: Word cap on the output. Default 30.

    Returns:
        The summary.
    """
    import httpx
    try:
        with httpx.Client(timeout=60) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/local_summarize_short",
                        json={"kwargs": {"text": text, "max_words": max_words}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def cloud_run_claude(task: str, model: str = "haiku", redact_pii: bool = True, max_turns: int = 6) -> str:
    """Run a task on cloud Claude via subscription `claude -p`. Default model is Haiku.

    Args:
        task: The task description.
        model: Tier — "haiku" (default, cheap), "sonnet" (mid), "opus" (heavy).
        redact_pii: Whether to PII-redact the task before sending. Default True.
        max_turns: Maximum agentic turns within Claude. Default 6.

    Returns:
        Claude's response text with PII restored.
    """
    import httpx
    try:
        with httpx.Client(timeout=650) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/cloud_run_claude",
                        json={"kwargs": {"task": task, "model": model,
                                         "redact_pii": redact_pii, "max_turns": max_turns}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def cloud_run_ralph(task_substring: str, max_iter: int = 6) -> str:
    """Dispatch a TODO.md task to Ralph (drives to completion via reviewer.sh).

    Args:
        task_substring: Distinctive substring of the TODO.md `[ ]` line.
        max_iter: Maximum Ralph iterations. Default 6.

    Returns:
        Status: "completed", "incomplete", "blocked", or error.
    """
    import httpx
    try:
        with httpx.Client(timeout=1200) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/cloud_run_ralph",
                        json={"kwargs": {"task_substring": task_substring, "max_iter": max_iter}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def notify_operator(text: str, channel_hint: str = "default") -> str:
    """Append a one-line notification to the operator notify queue (chat-bound).

    Args:
        text: The notification message.
        channel_hint: Hint for which Discord channel.

    Returns:
        Confirmation with timestamp.
    """
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/notify_operator",
                        json={"kwargs": {"text": text, "channel_hint": channel_hint}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))


def system_health() -> str:
    """Report current system health: Ollama up?, GPU util/free, subscription auth.

    Returns:
        Multi-line status summary.
    """
    import httpx
    try:
        with httpx.Client(timeout=10) as cl:
            r = cl.post("http://host.docker.internal:8000/tools/system_health",
                        json={"kwargs": {}})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    if "error" in data:
        return f"ERROR: {data['error']}"
    return str(data.get("result", ""))
