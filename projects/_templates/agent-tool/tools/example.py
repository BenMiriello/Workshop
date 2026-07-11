"""Example Letta tool for the {{title}} agent.

Tool functions are plain Python with Google-style docstrings. Letta's
`tools.upsert_from_function` parses the `Args:` and `Returns:` blocks into the
JSON schema the agent sees, so the docstring MUST follow Google format exactly or
the tool registers with no arguments. Keep tools self-contained: import inside the
function so they also work when Letta runs them in its isolated sandbox.
"""
from __future__ import annotations


def record_event(summary: str, category: str = "general") -> str:
    """Record a short event to the {{title}} agent's local log.

    Use this to persist a durable one-line record the agent can refer back to
    later: a decision made, an observation, or a completed step.

    Args:
        summary: One-line description of what happened. Keep it concrete and
            specific (names, numbers, outcomes).
        category: Optional bucket for the event, e.g. "decision" or
            "observation". Defaults to "general".

    Returns:
        A confirmation string including the stored UTC timestamp.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    summary = (summary or "").strip()
    if len(summary) < 3:
        return "ERROR: summary too short"
    cat = (category or "general").strip() or "general"
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log_path = Path(__file__).resolve().parent.parent / "events.jsonl"
    entry = {"ts": ts, "category": cat, "summary": summary}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return f"recorded [{ts}] ({cat}): {summary}"
