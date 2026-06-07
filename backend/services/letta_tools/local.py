"""Local-cognition tools (Ollama-backed). SELF-CONTAINED — Letta sandbox.

These tools run on the local Ollama qwen2.5:32b and are designed for SHORT,
SCOPED queries: classification, routing decisions, single-line summaries.
"""
from __future__ import annotations


def _ollama_call(prompt: str, timeout_s: float = 30) -> str:
    """Internal helper — calls Ollama directly. Returns text or empty string."""
    import httpx
    try:
        with httpx.Client(timeout=timeout_s) as cl:
            r = cl.post(
                "http://127.0.0.1:11434/api/generate",
                json={"model": "qwen2.5:32b", "prompt": prompt, "stream": False,
                      "options": {"num_ctx": 8192, "temperature": 0.2}},
            )
            r.raise_for_status()
            return r.json().get("response", "").strip()
    except Exception:
        return ""


def local_classify(text: str, categories: str) -> str:
    """Classify `text` into ONE of the provided categories via local Ollama.

    Use this for: "is this a banking event?", "spam or not?", "urgency low/medium/high".
    Do NOT use for: anything requiring more than one word of judgement.

    Args:
        text: The content to classify. Will be truncated to ~500 chars before
            sending to Ollama to keep latency low.
        categories: Comma-separated list of category labels. The model picks
            exactly one. Example: "banking,personal,marketing,other".

    Returns:
        The chosen category (single word/phrase, lowercase). If Ollama is
        unavailable, returns "unknown".
    """
    if not text or not categories:
        return "unknown"
    snippet = text[:500].replace("\n", " ")
    cats = [c.strip() for c in categories.split(",") if c.strip()]
    prompt = (
        f"Classify this text into EXACTLY ONE of these categories: {', '.join(cats)}.\n"
        f"Reply with just the category name, nothing else.\n\nText: {snippet}\n\nCategory:"
    )
    result = _ollama_call(prompt, timeout_s=15)
    if not result:
        return "unknown"
    out = result.lower().split()[0] if result.strip() else "unknown"
    for c in cats:
        if c.lower() in out or out in c.lower():
            return c.lower()
    return "unknown"


def local_route_decision(task: str) -> str:
    """Decide which LLM tier should handle a task.

    Use this when planning whether to escalate work to Claude. Returns one of:
        "local"   — Ollama can handle it (short classification, redaction, glue)
        "haiku"   — most reasoning + simple code (default cloud tier)
        "sonnet"  — bigger code work, multi-file refactors
        "opus"    — architecture, complex multi-step plans

    Args:
        task: One-sentence description of what needs to be done.

    Returns:
        One of: "local", "haiku", "sonnet", "opus".
    """
    t = task.lower()
    words = len(task.split())
    if any(k in t for k in ["classify", "yes or no", "category", "extract", "redact", "is this", "summarize in one"]):
        if words < 200:
            return "local"
    if any(k in t for k in ["architect", "design the whole", "complex refactor", "audit the entire"]):
        return "opus"
    if any(k in t for k in ["implement", "write a service", "build a", "refactor"]):
        if words > 50:
            return "sonnet"
        return "haiku"
    return "haiku"


def local_summarize_short(text: str, max_words: int = 30) -> str:
    """Summarize `text` in `max_words` words or fewer via local Ollama.

    Use this for: "give me a one-line gist of this event/email/log line".
    Do NOT use for: anything where the full detail matters (use cloud_run_claude).

    Args:
        text: Content to summarize. Truncated to ~1500 chars on input.
        max_words: Word cap on the output. Default 30.

    Returns:
        The summary. If Ollama is unavailable, returns the first sentence.
    """
    if not text:
        return ""
    snippet = text[:1500]
    prompt = (
        f"Summarize this in at most {max_words} words. No preamble. Just the summary.\n\n"
        f"Text: {snippet}\n\nSummary:"
    )
    result = _ollama_call(prompt, timeout_s=30)
    if not result:
        return text.split(".")[0].strip()[:200]
    return result
