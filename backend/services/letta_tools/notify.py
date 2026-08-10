"""Notify-operator tool — self-contained for Letta sandbox."""
from __future__ import annotations


def notify_operator(text: str, channel_hint: str = "default") -> str:
    """Append a one-line notification to the operator notify queue.

    Use this when something genuinely needs the operator's attention:
    - An alert that crosses a threshold (overspend, late commute, security)
    - A milestone landed (a build completed, a feature shipped)
    - A blocker requiring operator decision (credential needed, classifier wall)

    Do NOT use this for routine "I'm alive" status. Heartbeats handle that.

    Args:
        text: The notification message. Should be one line, action-oriented,
            with concrete specifics (numbers, paths, agent names).
        channel_hint: Hint for which channel the bridge should surface it on.
            Defaults to the operator's main channel. Use "commute", "finance",
            etc. for category routing.

    Returns:
        Confirmation with timestamp.
    """
    import os
    from datetime import datetime, timezone
    from pathlib import Path

    _root = Path(os.environ.get("WORKSHOP_ROOT") or (Path.home() / "workshop"))
    notify_file = Path(
        os.environ.get("WORKSHOP_NOTIFY_FILE") or (_root / ".runtime" / "notify.txt")
    )
    if not text or len(text.strip()) < 5:
        return "ERROR: text too short"
    notify_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    line = f"[{ts}] [agent-notify hint={channel_hint}] {text.strip()}"
    with notify_file.open("a") as f:
        f.write(line + "\n")
    return f"queued: {line}"
