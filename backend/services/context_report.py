"""What is actually filling a chat's context window.

The CLI's `/context` computes this, and the measurement that makes it worth
building on is that it costs NOTHING: run against the real CLI on 2026-08-10 it
reported `num_turns: 0`, `duration_api_ms: 0`, `total_cost_usd: 0` in ~2.8s. It
is a local calculation, not a model call, so a panel may refresh it as often as
the operator likes. With `--resume <session>` it reports THAT thread's real
usage (verified: the CLI said 43k where the thread's stored count was 43004).

TWO RULES THIS MODULE EXISTS TO HOLD:

1. **Take its TOKENS, never its PERCENTAGES.** The CLI computes percentages
   against the window it thinks it has (200k in the probe). Workshop threads run
   a 1M window — the operator's own thread has measured 440k, which a 200k
   window cannot hold. Rendering its percentages would put a second, disagreeing
   context figure a few pixels from the meter, which is the exact class of bug
   this month was spent removing. Percentages are computed by the caller from
   the caller's own window.

2. **"Free space" and "Autocompact buffer" are not things filling the context.**
   They are what is left. A bar of "what is using your window" that includes
   free space is a pie chart of pie.

The parser is pure and total: it never raises on unexpected input, because a
formatting change in a vendor CLI must degrade this panel, not break the chat
it hangs off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from services import llm_router

log = logging.getLogger("workshop.context_report")

CLAUDE_CLI = llm_router.CLAUDE_CLI

# Where a non-project chat's session lives. Same default as everywhere else.
WORKSHOP_ROOT = Path(os.environ.get("WORKSHOP_ROOT", str(Path(__file__).resolve().parents[2])))

# Generous: the command is local and took ~3s in the probe, but a cold CLI start
# on a loaded box is slower, and the caller renders a stale reading meanwhile.
REPORT_TIMEOUT_S = 45.0

# Categories that describe what is LEFT, not what is used. See rule 2.
NOT_IN_USE = ("free space", "autocompact buffer")


def parse_tokens(raw: str) -> int:
    """"8.7k" -> 8700, "241" -> 241, "~150" -> 150, "< 20" -> 20, "" -> 0.

    The CLI hedges small numbers with `~` and `< `, and those are still the best
    estimate available; dropping them would silently zero the Skills section."""
    s = (raw or "").strip().lstrip("~<").strip()
    s = s.replace(",", "")
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*([kKmM]?)", s)
    if not m:
        return 0
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit == "k":
        value *= 1_000
    elif unit == "m":
        value *= 1_000_000
    return int(round(value))


def _rows(md: str, heading: str) -> list[list[str]]:
    """The cells of the markdown table under `### heading`, header row and the
    `|---|` separator dropped. Empty list when the section is absent, which is
    normal: a session with no MCP servers has no MCP Tools table."""
    pattern = re.compile(
        r"^###\s+" + re.escape(heading) + r"\s*$(.*?)(?=^###\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(md)
    if not m:
        return []
    out: list[list[str]] = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            continue  # the |---|---| separator
        out.append(cells)
    return out[1:] if out else []   # drop the header row


def parse_context_report(md: str) -> dict:
    """The CLI's `/context` markdown, as data.

    Returns `{model, cli_used_tokens, cli_window_tokens, categories, mcp,
    agents, memory, skills}`. `categories` excludes free space and the
    autocompact buffer (rule 2) and is ordered largest first, because the
    question the panel answers is "what should I get rid of".
    """
    model = ""
    m = re.search(r"\*\*Model:\*\*\s*(.+?)\s*$", md, re.MULTILINE)
    if m:
        model = m.group(1).strip()

    used = window = 0
    m = re.search(r"\*\*Tokens:\*\*\s*([0-9.,km]+)\s*/\s*([0-9.,km]+)", md, re.IGNORECASE)
    if m:
        used, window = parse_tokens(m.group(1)), parse_tokens(m.group(2))

    categories = []
    for row in _rows(md, "Estimated usage by category"):
        if len(row) < 2:
            continue
        name = row[0]
        if name.strip().lower() in NOT_IN_USE:
            continue
        tokens = parse_tokens(row[1])
        if tokens > 0:
            categories.append({"name": name, "tokens": tokens})
    categories.sort(key=lambda c: c["tokens"], reverse=True)

    # Grouped by SERVER, not listed as tools. Twenty-two tool rows is a wall;
    # "4 servers, and this one costs 4.3k" is the shape of an actionable answer.
    servers: dict[str, dict] = {}
    for row in _rows(md, "MCP Tools"):
        if len(row) < 3:
            continue
        tool, server, tokens = row[0], row[1], parse_tokens(row[2])
        entry = servers.setdefault(server, {"server": server, "tokens": 0, "tools": []})
        entry["tokens"] += tokens
        entry["tools"].append({"name": tool, "tokens": tokens})
    mcp = sorted(servers.values(), key=lambda s: s["tokens"], reverse=True)

    def _three(heading: str, a: str, b: str) -> list[dict]:
        out = []
        for row in _rows(md, heading):
            if len(row) < 3:
                continue
            out.append({a: row[0], b: row[1], "tokens": parse_tokens(row[2])})
        return sorted(out, key=lambda x: x["tokens"], reverse=True)

    return {
        "model": model,
        "cli_used_tokens": used,
        "cli_window_tokens": window,
        "categories": categories,
        "mcp": mcp,
        "agents": _three("Custom Agents", "name", "source"),
        "memory": _three("Memory Files", "kind", "path"),
        "skills": _three("Skills", "name", "source"),
    }


async def run_context_report(session_id: str | None, cwd: str,
                             model: str | None = None) -> dict | None:
    """Ask the CLI what is in this session's window. None when it cannot answer.

    A SIDE QUERY, NOT A TURN. It must never claim the thread, write a run event,
    or append a message: the operator opening a panel is not a thing that
    belongs in the transcript, and a `/context` that took the thread's run claim
    would block the next message they typed."""
    cmd = [CLAUDE_CLI, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--resume", session_id]
    cmd.append("/context")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=llm_router._claude_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("context report: could not start the CLI: %s", e)
        return None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), REPORT_TIMEOUT_S)
    except asyncio.TimeoutError:
        # Local computation that has not finished in 45s is wedged. Kill it
        # rather than leaving a process behind every time the panel is opened.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        log.warning("context report: timed out after %.0fs", REPORT_TIMEOUT_S)
        return None
    if proc.returncode != 0:
        log.warning("context report: CLI exited %s: %s",
                    proc.returncode, (err or b"")[:400].decode(errors="replace"))
        return None
    import json
    try:
        payload = json.loads(out.decode(errors="replace"))
    except (ValueError, AttributeError):
        log.warning("context report: unparseable CLI response")
        return None
    text = payload.get("result") or ""
    if "isn't available" in text or "## Context Usage" not in text:
        # A CLI build where /context does not run here. Say nothing rather than
        # rendering an empty panel that looks like an empty context.
        log.info("context report: /context not available in this environment")
        return None
    return parse_context_report(text)
