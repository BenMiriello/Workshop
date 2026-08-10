"""Which MCP servers this machine has configured, and whether they work.

WHY THIS EXISTS. On 2026-08-10 `claude mcp list` reported three servers failing
to connect and two unauthenticated, and nothing in the app said so. A server
that fails to connect still costs a startup attempt on every turn that tries to
reach it and returns nothing, silently. The context modal already shows what MCP
costs; this is the other half — whether the thing being paid for is alive.

WHAT THE CLI ACTUALLY PRINTS, and it prints TWO DIFFERENT THINGS. Both captured
verbatim on this host on 2026-08-10, both exit code 0.

On a terminal:

    Checking MCP server health…

    claude.ai Slack: https://mcp.slack.com/mcp - ! Needs authentication
    claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ✓ Connected
    plugin:discord:discord: bun run --cwd /home/... --shell=bun --silent start - ✗ Failed to connect
    plaid: node /home/clawd-a/clawdbot/plaid-mcp/server.js - ✓ Connected
    playwright: npx @playwright/mcp@latest --headless --browser chromium - ✗ Failed to connect

Down a pipe, which is how THIS code runs it:

    Checking MCP server health…

    plugin:discord:discord: bun run --cwd /home/... start - ✘ Failed to connect — ENOENT: Executable not found in $PATH: "bun"
    plaid: node /home/clawd-a/clawdbot/plaid-mcp/server.js - ✔ Connected
    google-docs: npx -y @a-bonus/google-docs-mcp - ✘ Failed to connect — -32000: MCP error -32000: Connection closed
    playwright: npx @playwright/mcp@latest --headless --browser chromium - ✘ Failed to connect — -32000: MCP error -32000: Connection closed

Three differences, all of which this module had to be corrected for after
measuring rather than assuming:

- **Different glyphs.** `✔`/`✘` down a pipe, `✓`/`✗` on a terminal.
- **A reason, after an em-dash.** The pipe form says WHY, and the why is the
  most actionable string on the screen ("bun is not in $PATH"). It is kept as
  `detail` rather than discarded into a generic "Failed to connect".
- **Fewer servers.** The four `claude.ai` connectors do not appear
  non-interactively. That is not a bug to paper over: this panel reports what
  the CLI sees IN THE ENVIRONMENT THE TURNS RUN IN, which is the only set a turn
  can actually reach. A list padded out with servers the backend cannot see
  would be a screen that lies in the operator's favour.

There is no `--json`, so this is line parsing, and the shape it has to survive
is nastier than it looks: a server name may itself contain colons
(`plugin:discord:discord`), a command may contain dashes
(`npx -y @a-bonus/google-docs-mcp`), and a failure detail may contain both
(`-32000: MCP error -32000: Connection closed`). The split points are therefore
the FIRST `": "` for the name, the LAST `" - "` for the state, and the first
em-dash inside the state for the detail. A line that yields no name is reported
as one server in an unknown state rather than dropped.

THREE THINGS THIS MODULE REFUSES TO DO:

1. **Invent a server.** If the CLI cannot be run, the answer is "unavailable"
   with the reason. A hardcoded list would be a screen that says everything is
   fine on a host where nothing is configured.
2. **Invent a state.** An unrecognised status line becomes `stopped` — neutral,
   captioned with the CLI's own words. Unknown is a state; guessing is not.
3. **Claim to know the scope.** `claude mcp list` prints no user/project/local
   column, so this reports `origin`, which IS derivable from the line: a
   `plugin:` prefix, a `claude.ai ` prefix, an http(s) target, or a local
   command.

The parser is pure and total, for the same reason `context_report`'s is: a
formatting change in a vendor CLI must degrade a panel, never break the chat it
hangs off.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from services import llm_router
from services.context_report import TtlCache

log = logging.getLogger("workshop.mcp_health")

CLAUDE_CLI = llm_router.CLAUDE_CLI

# `claude mcp list` does not merely read config: its own help says "stdio
# servers from .mcp.json are spawned for health checks". It is therefore several
# process launches, and the measured 2.2s on a warm box is a floor, not a
# ceiling. 90s before we call it wedged.
MCP_LIST_TIMEOUT_S = 90.0

# FIVE MINUTES, five times the context report's TTL, and deliberately so.
#
# This probe is the more expensive of the two (it spawns every stdio server) and
# the one whose answer changes least often: whether a server is configured and
# whether it can be reached are machine facts, not per-turn ones. Nothing a chat
# does alters them, so unlike the context report there is no fingerprint that
# could invalidate this early — time is the only signal available, which argues
# for erring long rather than short. A newly-fixed server shows up on the next
# Refresh, which bypasses the cache; a broken one stays broken for far longer
# than five minutes.
MCP_HEALTH_TTL_S = 300.0

# The five status kinds the design system allows (frontend/CLAUDE.md rule 3).
# `running` is not among the mappings on purpose: it means work in flight, and
# by the time a line has been printed the check for that server has finished.
# The UI uses `running` for the probe itself while a refresh is in the air.
KIND_OK = "ok"
KIND_WARN = "warn"
KIND_DANGER = "danger"
KIND_STOPPED = "stopped"

# Ordered: first match wins, so "not connected" cannot be read as "connected".
_STATE_MAP: tuple[tuple[str, str, str], ...] = (
    ("failed to connect", KIND_DANGER, "Failed to connect"),
    ("not connected", KIND_DANGER, "Not connected"),
    ("connection error", KIND_DANGER, "Connection error"),
    ("needs authentication", KIND_WARN, "Needs authentication"),
    ("authentication required", KIND_WARN, "Needs authentication"),
    ("disabled", KIND_STOPPED, "Disabled"),
    ("connected", KIND_OK, "Connected"),
)

# The mark the CLI puts in front of the words, used only when the words are
# unfamiliar — a new phrasing behind a known glyph should still land in the
# right bucket.
_MARK_MAP = {"✓": KIND_OK, "✔": KIND_OK,
             "✗": KIND_DANGER, "✘": KIND_DANGER, "×": KIND_DANGER,
             "!": KIND_WARN, "⚠": KIND_WARN}

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# The status suffix: the LAST " - " on the line, an optional mark, then words.
_STATE_RE = re.compile(r"^(?P<target>.*)\s+-\s+(?P<mark>[✓✔✗✘×!⚠])?\s*(?P<state>\S.*)$")
# "Failed to connect — ENOENT: Executable not found in $PATH" splits into the
# state and the reason. An em-dash, not a hyphen: a hyphen appears inside the
# reasons themselves (`-32000: MCP error -32000`).
_DETAIL_RE = re.compile(r"\s+[—–]\s+|\s+--\s+")
# The name: everything before the FIRST colon-space.
_NAME_RE = re.compile(r"^(?P<name>\S.*?):\s+(?P<rest>\S.*)$")
# Chatter the command prints around the list.
_NOISE = ("checking mcp server health", "no mcp servers configured")

# How broken something is, for ordering. A list whose first row is the failure
# is a list that has already answered the question.
SEVERITY = {KIND_DANGER: 0, KIND_WARN: 1, KIND_STOPPED: 2, KIND_OK: 3}


def classify_state(text: str, mark: str = "") -> tuple[str, str]:
    """`(kind, label)` for one CLI status phrase.

    Unrecognised text keeps its own words and gets the neutral kind — the point
    of the panel is to report what the CLI said, and a phrase we have not seen
    before is still information. Only an empty phrase becomes "Unknown".
    """
    low = (text or "").strip().lower()
    for needle, kind, label in _STATE_MAP:
        if needle in low:
            return kind, label
    if mark in _MARK_MAP:
        return _MARK_MAP[mark], (text or "").strip()
    if not low:
        return KIND_STOPPED, "Unknown"
    return KIND_STOPPED, text.strip()


def _read_state(text: str, mark: str = "") -> tuple[str, str, str]:
    """`(kind, label, detail)` — the state phrase split from its reason.

    "Failed to connect — ENOENT: Executable not found in $PATH" is one string
    from the CLI and two facts: WHAT happened, which belongs in a status pill,
    and WHY, which belongs in the line under it. Folding the second into the
    first would produce a pill the width of the panel; dropping it would throw
    away the only part of this feature that tells the operator what to do.
    """
    parts = _DETAIL_RE.split((text or "").strip(), 1)
    kind, label = classify_state(parts[0], mark)
    return kind, label, (parts[1].strip() if len(parts) > 1 else "")


def classify_origin(name: str, target: str) -> tuple[str, str]:
    """`(origin, transport)` — where a server comes from and how it is reached.

    Derived from the line, never guessed at: `claude mcp list` prints no scope
    column, so this reports only what the name and target actually show.
    """
    low_name = (name or "").strip().lower()
    low_target = (target or "").strip().lower()
    transport = "http" if low_target.startswith(("http://", "https://")) else "stdio"
    if low_name.startswith("plugin:"):
        return "plugin", transport
    if low_name.startswith("claude.ai "):
        return "claude.ai", transport
    if transport == "http":
        return "remote", transport
    return "local", transport


def parse_mcp_list(raw: str) -> list[dict]:
    """`claude mcp list`'s stdout, as a list of servers.

    Never raises. A line it cannot split into name and target is skipped (it is
    prose, not a server); a line with a name but no recognisable status suffix
    becomes a server in the unknown state, because a configured server we cannot
    read the health of is exactly the thing this panel must not hide.
    """
    servers: list[dict] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        line = _ANSI.sub("", line).strip()
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(n) for n in _NOISE):
            continue
        nm = _NAME_RE.match(line)
        if not nm:
            continue
        name = nm.group("name").strip()
        rest = nm.group("rest").strip()
        sm = _STATE_RE.match(rest)
        if sm:
            target = sm.group("target").strip()
            kind, label, detail = _read_state(sm.group("state"), sm.group("mark") or "")
        elif rest[:1] in _MARK_MAP:
            # A build that prints the health mark with no transport target.
            target = ""
            kind, label, detail = _read_state(rest[1:], rest[:1])
        else:
            target, kind, label, detail = rest, KIND_STOPPED, "Unknown", ""
        if name in seen:
            continue
        seen.add(name)
        origin, transport = classify_origin(name, target)
        servers.append({
            "name": name,
            "target": target,
            "origin": origin,
            "transport": transport,
            "kind": kind,
            "state": label,
            # Why, in the CLI's words. Empty on a terminal, which does not print
            # one; the most useful string on the screen when it is not.
            "detail": detail,
        })
    servers.sort(key=lambda s: (SEVERITY.get(s["kind"], 2), s["name"].lower()))
    return servers


def summarize(servers: list[dict]) -> dict:
    """Counts per status kind, plus the total. The one-line answer to "is
    anything wrong", so the caller does not have to re-derive it."""
    counts = {KIND_OK: 0, KIND_WARN: 0, KIND_DANGER: 0, KIND_STOPPED: 0}
    for s in servers:
        kind = s.get("kind", KIND_STOPPED)
        counts[kind] = counts.get(kind, 0) + 1
    counts["total"] = len(servers)
    return counts


async def run_mcp_health(cwd: str) -> dict:
    """Ask the CLI which MCP servers exist here and whether they connect.

    A SIDE QUERY, exactly like the context report: it spawns no session, claims
    no thread, writes nothing. Always returns a dict — `available: False` with a
    `reason` when the CLI could not be run or said nothing usable, never an
    exception and never a fabricated server list.
    """
    cmd = [CLAUDE_CLI, "mcp", "list"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, env=llm_router._claude_env(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("mcp health: could not start the CLI: %s", e)
        return {"available": False,
                "reason": f"The claude CLI could not be started ({e.__class__.__name__})."}
    try:
        out, err = await asyncio.wait_for(proc.communicate(), MCP_LIST_TIMEOUT_S)
    except asyncio.TimeoutError:
        # The command spawns every stdio server; one that hangs on start would
        # otherwise leave a process behind on every open of the panel.
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        log.warning("mcp health: timed out after %.0fs", MCP_LIST_TIMEOUT_S)
        return {"available": False,
                "reason": f"`claude mcp list` did not finish within {int(MCP_LIST_TIMEOUT_S)}s."}
    text = (out or b"").decode(errors="replace")
    servers = parse_mcp_list(text)
    if not servers and proc.returncode != 0:
        detail = (err or b"")[:200].decode(errors="replace").strip()
        log.warning("mcp health: CLI exited %s: %s", proc.returncode, detail)
        return {"available": False,
                "reason": f"`claude mcp list` exited {proc.returncode}."
                          + (f" {detail}" if detail else "")}
    # Exit 0 with no parseable lines is a real answer: nothing is configured.
    return {"available": True, "servers": servers, "counts": summarize(servers)}


# Keyed by cwd, because that is what the command's answer depends on: a project
# directory's `.mcp.json` adds servers that another directory does not have.
_HEALTH_CACHE = TtlCache(MCP_HEALTH_TTL_S)


async def cached_mcp_health(cwd: str, *, force: bool = False) -> tuple[dict, float, bool]:
    """`run_mcp_health`, at most once per `MCP_HEALTH_TTL_S` per directory.

    Returns `(result, checked_at, cached)`, where `checked_at` is when the CLI
    actually ran — not when the cache was read. A failure is not cached, so a
    CLI that was busy a second ago is retried rather than remembered.
    """
    if not force:
        hit = _HEALTH_CACHE.get(cwd)
        if hit is not None:
            return hit[0], hit[1], True
    result = await run_mcp_health(cwd)
    if not result.get("available"):
        _HEALTH_CACHE.invalidate(cwd)
        return result, time.time(), False
    return result, _HEALTH_CACHE.put(cwd, result), False
