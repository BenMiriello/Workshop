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
import time
from pathlib import Path
from typing import Any

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

# How long a parsed report stands in for a fresh one.
#
# TWO MINUTES, and the reason it can be that long is that TTL is not what keeps
# this correct — the FINGERPRINT is. The route passes the thread's own token
# count and message count, and any turn changes both, so a report is thrown out
# the instant the conversation it describes moves. What the TTL bounds is only
# the drift the fingerprint cannot see: a memory file edited on disk, a skill
# added, an MCP server installed. Those change on a human timescale, not a
# per-turn one.
#
# The cost being avoided is real and was measured: every open of the panel
# re-spawned the CLI for ~2.8s. Opening the panel, closing it, and opening it
# again while reading is now instant, and the footer prints the reading's age so
# it is never anonymous. Anything the operator judges stale is one Refresh away,
# and Refresh bypasses this entirely.
CONTEXT_TTL_S = 120.0


class TtlCache:
    """A tiny time-and-fingerprint cache for expensive local CLI probes.

    Two independent reasons to miss: the entry is older than `ttl_s`, or the
    caller's `fingerprint` differs from the one stored with it. The fingerprint
    is what makes a long TTL safe — it lets the caller say "this reading
    describes a state that has since changed" without knowing anything about
    clocks.

    `at` is wall-clock epoch seconds, because it is shown to a person
    ("Refreshed 2m ago"). Expiry uses the monotonic clock instead, so an NTP
    step cannot make an entry immortal or instantly dead.
    """

    def __init__(self, ttl_s: float):
        self.ttl_s = float(ttl_s)
        self._entries: dict[str, tuple[Any, float, float, str]] = {}

    def get(self, key: str, fingerprint: str = "") -> tuple[Any, float] | None:
        """`(value, at)` for a live entry matching `fingerprint`, else None."""
        hit = self._entries.get(key)
        if hit is None:
            return None
        value, at, mono, fp = hit
        if fp != fingerprint:
            del self._entries[key]
            return None
        if time.monotonic() - mono >= self.ttl_s:
            del self._entries[key]
            return None
        return value, at

    def put(self, key: str, value: Any, fingerprint: str = "") -> float:
        """Store `value` and return the wall-clock time it was recorded at."""
        at = time.time()
        self._entries[key] = (value, at, time.monotonic(), fingerprint)
        return at

    def invalidate(self, key: str) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()


# Keyed by session id + cwd: the same session inspected from a different working
# directory is a different reading (different project memory, different skills).
_REPORT_CACHE = TtlCache(CONTEXT_TTL_S)


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


# Whether this CLI build accepts `--fork-session`. Resolved once, by trying it,
# because there is no version to ask and a `claude --help` grep is a second
# thing that can be wrong. None = not yet determined.
_FORK_SUPPORTED: bool | None = None


async def _spawn_report(session_id: str | None, cwd: str, model: str | None,
                        *, fork: bool):
    cmd = [CLAUDE_CLI, "-p", "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if session_id:
        cmd += ["--resume", session_id]
        # A COPY OF THE SESSION, NOT THE SESSION. Without this, `--resume`
        # RESUMES: the /context turn appends to the very window it is
        # measuring. Measured 2026-09-01 on the operator's own thread: eight
        # `/context` records injected in one day, and the 19:55:54 compaction
        # preserved one of them as a `tailUuid`, so a table ABOUT the context
        # window became one of the two messages that survived into the new
        # one. Opening the panel was spending the post-compaction window on a
        # report about the window.
        if fork:
            cmd.append("--fork-session")
    cmd.append("/context")
    return await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=llm_router._claude_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def run_context_report(session_id: str | None, cwd: str,
                             model: str | None = None) -> dict | None:
    """Ask the CLI what is in this session's window. None when it cannot answer.

    A SIDE QUERY, NOT A TURN. It must never claim the thread, write a run event,
    or append a message: the operator opening a panel is not a thing that
    belongs in the transcript, and a `/context` that took the thread's run claim
    would block the next message they typed.

    That promise was not kept between the docstring and the process: `--resume`
    without `--fork-session` writes into the session. It forks now, and falls
    back on a CLI build that does not know the flag, on the same terms as the
    parser below -- a vendor CLI's flag set must degrade this panel, not break
    the chat it hangs off.
    """
    global _FORK_SUPPORTED
    fork = session_id is not None and _FORK_SUPPORTED is not False
    try:
        proc = await _spawn_report(session_id, cwd, model, fork=fork)
    except (OSError, FileNotFoundError) as e:
        log.warning("context report: could not start the CLI: %s", e)
        return None
    if fork and _FORK_SUPPORTED is None:
        # First fork attempt. A build that does not know the flag exits fast
        # with a usage error; anything else means it took it.
        out, err = await proc.communicate()
        blob = ((err or b"") + (out or b"")).decode(errors="replace")
        if proc.returncode != 0 and "fork-session" in blob:
            _FORK_SUPPORTED = False
            log.info("context report: this CLI has no --fork-session; "
                     "the report will resume the session instead")
            try:
                proc = await _spawn_report(session_id, cwd, model, fork=False)
            except (OSError, FileNotFoundError) as e:
                log.warning("context report: could not start the CLI: %s", e)
                return None
        else:
            _FORK_SUPPORTED = True
            return _parse_report_output(proc.returncode, out, err)
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
    return _parse_report_output(proc.returncode, out, err)


def _parse_report_output(returncode: int | None, out: bytes | None,
                         err: bytes | None) -> dict | None:
    """One reading of the CLI's answer, for both the forked and plain spawns."""
    if returncode != 0:
        log.warning("context report: CLI exited %s: %s",
                    returncode, (err or b"")[:400].decode(errors="replace"))
        return None
    import json
    try:
        payload = json.loads((out or b"").decode(errors="replace"))
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


async def cached_context_report(
    session_id: str, cwd: str, model: str | None = None,
    *, fingerprint: str = "", force: bool = False,
) -> tuple[dict | None, float, bool]:
    """`run_context_report`, but not twice in two minutes for the same reading.

    Returns `(report, checked_at, cached)`. `checked_at` is epoch seconds and is
    what the panel's "Refreshed Nm ago" counts from — a cached answer keeps the
    timestamp of the run that produced it, because that is when the reading was
    true. Reporting the moment of the cache HIT would be a lie about freshness,
    which is the one thing a freshness line must not be.

    `force=True` is the explicit Refresh control: it skips the lookup entirely.
    A failed run is not cached — an unreachable CLI should be retried on the
    next open, not remembered for two minutes.
    """
    key = f"{session_id}\x00{cwd}"
    if not force:
        hit = _REPORT_CACHE.get(key, fingerprint)
        if hit is not None:
            return hit[0], hit[1], True
    report = await run_context_report(session_id, cwd, model)
    if report is None:
        _REPORT_CACHE.invalidate(key)
        return None, time.time(), False
    return report, _REPORT_CACHE.put(key, report, fingerprint), False
