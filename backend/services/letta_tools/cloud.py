"""Cloud-escalation tools — self-contained for Letta sandbox.

These tools shell out to `claude -p` via subscription auth. They BLOCK the
agent's Letta turn while running. The caller sets a generous /messages timeout.
"""
from __future__ import annotations

import os as _os
import shutil as _shutil
from pathlib import Path as _Path


# Inlined model IDs (sandbox can't import from llm_router)
_MODEL_HAIKU = "claude-haiku-4-5-20251001"
_MODEL_SONNET = "claude-sonnet-4-6"
_MODEL_OPUS = "claude-opus-4-7"

# Resolved from the environment, never from one machine's layout. WORKSHOP_CLAUDE_CLI
# pins a specific binary when several are installed; otherwise take whatever `claude`
# is on PATH.
_WORKSHOP_ROOT = str(_os.environ.get("WORKSHOP_ROOT") or (_Path.home() / "workshop"))
_CLAUDE_CLI = _os.environ.get("WORKSHOP_CLAUDE_CLI") or _shutil.which("claude") or "claude"
# DELIBERATELY DUPLICATED from services.auth_env, which is the one loader
# everywhere else. This module is copied into the Letta sandbox and executed
# there with no access to the `services` package (the same reason the model IDs
# above are inlined), so importing the shared loader would break the tool at
# runtime rather than at import in a test. The env var name and the default
# directory must stay in step with services/auth_env.py; nothing enforces that
# automatically, which is why it is written down here.
_AUTH_ENV = str(
    _os.environ.get("WORKSHOP_AUTH_ENV")
    or (_Path.home() / ".config" / "workshop" / "auth.env")
)

# Recursion + cost caps (security-audit-2026-07-18 A4). These tools spawn full
# agentic `claude -p` turns with Bash; caller-supplied max_turns/max_iter used to
# be unbounded, and nothing stopped claude→claude→claude fan-out. A depth counter
# rides in the child env so a spawned turn that calls these tools again is refused
# past _MAX_DEPTH.
_MAX_TURNS_CAP = 12
_MAX_ITER_CAP = 8
_MAX_DEPTH = 2
_MAX_CONCURRENT = 3

import threading
_CONC_LOCK = threading.Lock()
_active = {"n": 0}


def _too_deep() -> bool:
    # Best-effort recursion brake: works when the caller's env carries the counter
    # (direct subprocess chain). It does NOT cross the /tools HTTP boundary — a
    # nested claude that re-enters via host.docker.internal:8000 hits a depth-0
    # backend — so the real fan-out bound is _try_enter() below (concurrency cap)
    # plus the max_turns/timeout clamps, not this counter alone.
    import os
    try:
        return int(os.environ.get("WORKSHOP_AGENT_DEPTH", "0") or 0) >= _MAX_DEPTH
    except ValueError:
        return False


def _try_enter() -> bool:
    """Bound simultaneous cloud-run spawns regardless of how they were reached
    (direct or via the /tools HTTP re-entry). Cost-safety, not user rate-limiting.
    (security-audit-2026-07-18 A4 + re-audit finding 7)"""
    with _CONC_LOCK:
        if _active["n"] >= _MAX_CONCURRENT:
            return False
        _active["n"] += 1
        return True


def _leave() -> None:
    with _CONC_LOCK:
        _active["n"] = max(0, _active["n"] - 1)


def _build_env() -> dict:
    """Subscription-only env: load CLAUDE_CODE_OAUTH_TOKEN from auth.env, drop API key."""
    import os
    from pathlib import Path
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)
    # Increment the recursion-depth counter for the spawned turn (A4).
    try:
        _d = int(os.environ.get("WORKSHOP_AGENT_DEPTH", "0") or 0)
    except ValueError:
        _d = 0
    env["WORKSHOP_AGENT_DEPTH"] = str(_d + 1)
    p = Path(_AUTH_ENV)
    if p.is_file():
        try:
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return env


def _redact_inline(text: str) -> tuple[str, dict]:
    """Inline PII redaction via local Ollama. Returns (redacted_text, mapping).
    Skips on Ollama unavailability — returns original text with empty mapping."""
    import re
    import httpx
    import json as _json

    # Regex pre-pass — high-confidence patterns only.
    mapping: dict = {}
    counter = 0
    redacted = text

    PATTERNS = [
        ("SSN", re.compile(r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
        ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
        ("PHONE", re.compile(r"(?:\+?1[-.\s]?)?\(?\b[2-9]\d{2}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
        ("BANK_ACCT", re.compile(r"(?:account|acct|card)\s+(?:ending|last\s*4|number|#)?\s*(?:in\s+|with\s+)?[:\-]?\s*\d{4}\b", re.IGNORECASE)),
    ]
    for label, pat in PATTERNS:
        for m in pat.finditer(redacted):
            val = m.group(0)
            if val in mapping.values():
                continue
            ph = f"<<PII_{label}_{counter}>>"
            mapping[ph] = val
            counter += 1
    # Apply replacements (longest first to avoid overlap)
    for ph, val in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        redacted = redacted.replace(val, ph)

    return redacted, mapping


def _restore_inline(text: str, mapping: dict) -> str:
    """Substitute placeholders back. Longest first."""
    if not mapping:
        return text
    for k in sorted(mapping.keys(), key=len, reverse=True):
        text = text.replace(k, mapping[k])
    return text


def cloud_run_claude(task: str, model: str = "haiku", redact_pii: bool = True, max_turns: int = 6) -> str:
    """Run a task on cloud Claude via subscription `claude -p`.

    Use this when: local Ollama isn't enough, you need real reasoning, you
    need to write code, you need multi-step action. Prefer Haiku unless you
    know the task is hard.

    Args:
        task: The task description. Will be sent to Claude as the prompt.
        model: Tier — "haiku" (default, cheap), "sonnet" (mid), "opus" (heavy).
        redact_pii: Whether to PII-redact the task before sending (default True).
            Set False only when literal PII values are needed by Claude.
        max_turns: Maximum agentic turns within Claude. Default 6.

    Returns:
        Claude's response text. PII placeholders are restored to original
        values before return.
    """
    import subprocess
    if _too_deep():
        return "ERROR: recursion depth cap reached (WORKSHOP_AGENT_DEPTH >= %d)" % _MAX_DEPTH
    try:
        max_turns = max(1, min(int(max_turns), _MAX_TURNS_CAP))
    except (TypeError, ValueError):
        max_turns = 6
    model_id = {"haiku": _MODEL_HAIKU, "sonnet": _MODEL_SONNET, "opus": _MODEL_OPUS}.get(model.lower(), _MODEL_HAIKU)

    if redact_pii:
        prompt_to_send, mapping = _redact_inline(task)
    else:
        prompt_to_send = task
        mapping = {}

    cmd = [
        _CLAUDE_CLI, "-p", prompt_to_send,
        "--model", model_id,
        "--output-format", "text",
        "--max-turns", str(max_turns),
    ]
    if not _try_enter():
        return "ERROR: cloud-run concurrency cap (%d) reached; try again shortly" % _MAX_CONCURRENT
    try:
        proc = subprocess.run(
            cmd, env=_build_env(), cwd=_WORKSHOP_ROOT,
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: claude -p timed out (>600s)"
    finally:
        _leave()
    if proc.returncode != 0:
        return f"ERROR: claude -p exit {proc.returncode}: {proc.stderr[:200]}"

    out = proc.stdout.strip()
    if mapping:
        out = _restore_inline(out, mapping)
    return out


def cloud_run_ralph(task_substring: str, max_iter: int = 6) -> str:
    """Dispatch a TODO.md task to the Ralph re-fire loop (drives to completion).

    Use this when: a TODO.md `[ ]` item needs to be DONE, not just discussed.
    Ralph fires claude -p iteratively until reviewer.sh approves the diff.

    The TODO.md this reads is the platform repo's own — this is `development`
    work (ADR 0014), so the target is named explicitly with `--dir` rather than
    inherited from a default. `ralph.sh` no longer has one, and it refuses the
    platform repo outright unless WORKSHOP_DEV_MODE=1, which is reported back
    here as "blocked: developer mode is off" instead of a bare exit code.

    Args:
        task_substring: Distinctive substring of the TODO.md `[ ]` line to drive.
        max_iter: Maximum Ralph iterations. Default 6.

    Returns:
        Status: "completed", "incomplete", "blocked", or an error string.
    """
    import subprocess
    from pathlib import Path
    if _too_deep():
        return "blocked: recursion depth cap (WORKSHOP_AGENT_DEPTH >= %d)" % _MAX_DEPTH
    try:
        max_iter = max(1, min(int(max_iter), _MAX_ITER_CAP))
    except (TypeError, ValueError):
        max_iter = 6
    todo_path = Path(_WORKSHOP_ROOT) / "TODO.md"
    if not todo_path.exists():
        return "ERROR: TODO.md does not exist"
    lines = todo_path.read_text().splitlines()
    matches = [l for l in lines if l.startswith("[ ] ") and task_substring.lower() in l.lower()]
    if not matches:
        return f"ERROR: no [ ] task matched {task_substring!r}"
    if len(matches) > 1:
        return f"ERROR: {len(matches)} tasks matched {task_substring!r}; be more specific"
    task_text = matches[0][4:]
    if not _try_enter():
        return "blocked: cloud-run concurrency cap (%d) reached; try again shortly" % _MAX_CONCURRENT
    try:
        proc = subprocess.run(
            [f"{_WORKSHOP_ROOT}/scripts/ralph.sh", task_text,
             "--dir", _WORKSHOP_ROOT,
             "--max-iter", str(max_iter), "--max-wall", "900"],
            cwd=_WORKSHOP_ROOT,
            capture_output=True, text=True, timeout=1100,
        )
    except subprocess.TimeoutExpired:
        return "timeout: ralph exceeded wall budget"
    finally:
        _leave()
    exit_code = proc.returncode
    if exit_code == 0:
        return "completed: ralph approved by reviewer.sh"
    if exit_code == 1:
        return "incomplete: iter cap hit (will retry next tick)"
    if exit_code == 2:
        return "incomplete: wall cap hit (will retry next tick)"
    if exit_code == 3:
        return "blocked: classifier"
    if exit_code == 4:
        # ralph.sh's startup refusal — here, always the developer-mode gate.
        return ("blocked: developer mode is off, so the platform repo is not a "
                "permitted run target (set WORKSHOP_DEV_MODE=1; ADR 0014)")
    return f"failed: ralph exit {exit_code}"
