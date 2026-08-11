"""Whether this install's two outbound credentials are usable, and how sure we are.

WHY THIS EXISTS. Both credentials this platform depends on could fail silently.
An expired `CLAUDE_CODE_OAUTH_TOKEN` reached the operator as `Ended unexpectedly
(exit 1)`, because the CLI's real complaint went to `stream.err` and nothing read
it. A dead `gh` login surfaced as a 500 from connect-github. Every consumer
treated a missing credential as a no-op and carried on. There was no validity
check anywhere, so "it stopped working" and "you are not signed in" looked
identical from the UI.

THE ASYMMETRY, WHICH IS THE WHOLE DESIGN. These two probes do NOT prove the same
thing, and the difference was measured on this host rather than assumed:

  `gh auth status`      DOES reach GitHub. An invalidated token produces
                        "The github.com token in GH_TOKEN is no longer valid."
                        That sentence can only come from the server, so a pass
                        here is real evidence. `verified: True`.

  `claude auth status`  DOES NOT reach Anthropic. Given a garbage
                        CLAUDE_CODE_OAUTH_TOKEN it still answered
                        `"loggedIn": true` in 0.16s. It reports what is
                        CONFIGURED, not what WORKS. `verified: False`.

So this module must not render the same green tick for both. Claude's row says a
credential is present and which kind it is; the authoritative word on whether it
works is the exit status of the last turn, which is why the other half of this
change makes `stream.err` readable. Showing an unverified "ok" for Claude would
recreate the exact failure being fixed, one layer up.

The only honest liveness check for the Claude token is a real API call, i.e. a
billed turn. A probe that costs money every time a settings page mounts is worse
than the ambiguity, so it is not done here.

WHAT THE COMMANDS ACTUALLY PRINT, captured on this host (gh 2.23.0):

    github.com
      ✓ Logged in to github.com as <account> (~/.config/gh/hosts.yml)
      ✓ Git operations for github.com configured to use https protocol.
      ✓ Token: ghp_************************************
      ✓ Token scopes: read:org, repo

and on an invalidated token, exit 1:

    github.com
      X github.com: authentication failed
      - The github.com token in GH_TOKEN is no longer valid.

Two measured details that a reasonable guess gets wrong: this `gh` writes auth
status to STDERR, not stdout (`gh auth status 2>/dev/null` prints nothing), and
it has no `--json`. Both streams are therefore read and concatenated, and the
parser is line-based. The failure glyph is an ASCII `X`, not the `✗` the docs
show.

`claude auth status` does have `--json`, and its measured shapes are:

    {"loggedIn": true,  "authMethod": "claude.ai",    "subscriptionType": "max"}
    {"loggedIn": true,  "authMethod": "oauth_token"}
    {"loggedIn": true,  "authMethod": "claude.ai", "apiKeySource": "ANTHROPIC_API_KEY"}
    {"loggedIn": false, "authMethod": "none"}                          # exit 1

NO TOKEN EVER COMES BACK OUT. `gh` masks its own token and is never run with
`-t`; any line naming a token is dropped anyway rather than trusted to stay
masked. `claude auth status` returns no secret. The account name and email in
the output are runtime facts about the signed-in user, shown on that user's own
settings screen, and are never written to disk by this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from services.context_report import TtlCache
from services.mcp_health import KIND_DANGER, KIND_OK, KIND_STOPPED, KIND_WARN, SEVERITY

log = logging.getLogger("workshop.auth_health")

# Both commands are local-ish and fast (claude 0.16s; gh is one API round trip).
# Ten seconds is already far past either; past that the network is the problem,
# and a settings page must not hang on it.
PROBE_TIMEOUT_S = 10.0

# Short, because the question "am I still signed in" is exactly the one being
# asked right after someone fixes it. Long enough that a page which polls does
# not shell out on every tick.
AUTH_TTL_S = 60.0

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ACCOUNT_RE = re.compile(r"logged in to\s+(?P<host>\S+)\s+as\s+(?P<account>\S+)", re.I)
_SCOPES_RE = re.compile(r"token scopes:\s*(?P<scopes>.+)", re.I)
# Anything that looks like it carries a credential. Dropped before any line of
# `gh` output is considered for display, so a future version that stops masking
# cannot leak through this path.
_SECRETISH = re.compile(r"token:\s*\S", re.I)


def _clean_lines(raw: str) -> list[str]:
    return [_ANSI.sub("", ln).strip() for ln in (raw or "").splitlines() if ln.strip()]


def parse_gh_auth_status(raw: str, returncode: int) -> dict:
    """`gh auth status` output as a credential row. Pure, total, never raises.

    `returncode` is the primary signal because it is unambiguous; the text only
    supplies the account, the scopes, and the reason a failure happened. A
    non-zero exit with no readable reason still reports failure rather than
    inventing one.
    """
    lines = [ln for ln in _clean_lines(raw) if not _SECRETISH.search(ln)]
    account = ""
    scopes: list[str] = []
    for ln in lines:
        m = _ACCOUNT_RE.search(ln)
        if m and not account:
            account = m.group("account").strip()
        m = _SCOPES_RE.search(ln)
        if m and not scopes:
            scopes = [s.strip() for s in m.group("scopes").split(",") if s.strip()]

    if returncode == 0 and account:
        return {"kind": KIND_OK, "state": "Signed in", "detail": "",
                "account": account, "scopes": scopes}

    # The reason lives on the lines gh prefixes with `X` or `-`. Those glyphs are
    # presentation; the sentence after them is the actionable part.
    reasons = [re.sub(r"^[X✗✘×!⚠-]\s*", "", ln).strip()
               for ln in lines
               if re.match(r"^[X✗✘×!⚠-]\s", ln) or "not logged in" in ln.lower()]
    reasons = [r for r in reasons if r]
    detail = " ".join(reasons)[:300]

    if returncode == 0:
        # Exit 0 but nothing we could read. Do not claim it is fine, and do not
        # claim it is broken: say the check could not be read.
        return {"kind": KIND_STOPPED, "state": "Unknown",
                "detail": detail or "gh reported success but printed no account.",
                "account": "", "scopes": scopes}
    return {"kind": KIND_DANGER, "state": "Not signed in",
            "detail": detail or f"`gh auth status` exited {returncode}.",
            "account": account, "scopes": scopes}


def parse_claude_auth_status(raw: str, returncode: int) -> dict:
    """`claude auth status --json` as a credential row. Pure, total, never raises.

    Reports CONFIGURATION. See the module docstring: a pass here does not mean
    the token works, so no branch of this function returns a state that reads as
    "verified".
    """
    text = (raw or "").strip()
    data: dict = {}
    if text:
        # The CLI may print a banner before the JSON; take the outermost object.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start:end + 1])
                if isinstance(parsed, dict):
                    data = parsed
            except (json.JSONDecodeError, ValueError):
                data = {}

    if not data:
        return {"kind": KIND_STOPPED, "state": "Unknown",
                "detail": f"`claude auth status` returned no readable JSON (exit {returncode}).",
                "method": "", "account": "", "plan": ""}

    method = str(data.get("authMethod") or "")
    account = str(data.get("email") or "")
    plan = str(data.get("subscriptionType") or "")
    api_key_source = str(data.get("apiKeySource") or "")

    if not data.get("loggedIn"):
        return {"kind": KIND_DANGER, "state": "Not signed in",
                "detail": "No Claude credential is configured. Run `claude auth login`, "
                          "or set CLAUDE_CODE_OAUTH_TOKEN in the auth env file.",
                "method": method, "account": account, "plan": plan}

    if api_key_source:
        # This install is subscription-only by policy, and llm_router strips
        # ANTHROPIC_API_KEY from every turn it spawns. Seeing a key source here
        # means one is set in the environment the probe ran in, so turns and
        # anything else reading the ambient environment disagree about which
        # credential is in play. That is worth saying out loud.
        return {"kind": KIND_WARN, "state": "API key present",
                "detail": f"An API key from {api_key_source} is visible to the CLI. "
                          "Turns are spawned with it stripped, so the two disagree; "
                          "unset it to remove the ambiguity.",
                "method": method, "account": account, "plan": plan}

    return {"kind": KIND_OK, "state": "Configured",
            "detail": "", "method": method, "account": account, "plan": plan}


async def _run(cmd: list[str], env: dict | None = None) -> tuple[str, int] | None:
    """`(combined output, returncode)`, or None if the command could not start."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (OSError, FileNotFoundError) as e:
        log.warning("auth health: could not start %s: %s", cmd[0], e)
        return None
    try:
        out, err = await asyncio.wait_for(proc.communicate(), PROBE_TIMEOUT_S)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        log.warning("auth health: %s timed out after %.0fs", cmd[0], PROBE_TIMEOUT_S)
        return "", -1
    # Both streams: this gh writes auth status to stderr, and a future one may
    # not. Order is stdout-then-stderr so a JSON payload stays at the front.
    combined = (out or b"").decode(errors="replace") + "\n" + (err or b"").decode(errors="replace")
    return combined, proc.returncode


async def check_github(env: dict | None = None) -> dict:
    """Is the GitHub credential usable? Reaches GitHub, so a pass is evidence."""
    row = {"name": "github", "label": "GitHub", "verified": True,
           "checks": "`gh auth status`"}
    res = await _run(["gh", "auth", "status"], env=env)
    if res is None:
        row.update({"kind": KIND_STOPPED, "state": "Unavailable",
                    "detail": "The `gh` CLI is not installed or not on PATH.",
                    "verified": False})
        return row
    raw, rc = res
    if rc == -1:
        row.update({"kind": KIND_STOPPED, "state": "Unavailable",
                    "detail": f"`gh auth status` did not finish within {int(PROBE_TIMEOUT_S)}s.",
                    "verified": False})
        return row
    row.update(parse_gh_auth_status(raw, rc))
    return row


async def check_claude() -> dict:
    """Is a Claude credential configured? Local only — see the module docstring."""
    from services import llm_router

    row = {"name": "claude", "label": "Claude", "verified": False,
           "checks": "`claude auth status`",
           # Said once, here, so every surface that renders this row carries the
           # caveat rather than each one inventing its own wording.
           "unverified_because": "This check reads local configuration and does not "
                                 "contact Anthropic, so it cannot detect an expired "
                                 "token. A failing turn is the authoritative signal."}
    res = await _run([llm_router.CLAUDE_CLI, "auth", "status", "--json"],
                     env=llm_router._claude_env())
    if res is None:
        row.update({"kind": KIND_STOPPED, "state": "Unavailable",
                    "detail": "The `claude` CLI is not installed or not on PATH."})
        return row
    raw, rc = res
    if rc == -1:
        row.update({"kind": KIND_STOPPED, "state": "Unavailable",
                    "detail": f"`claude auth status` did not finish within {int(PROBE_TIMEOUT_S)}s."})
        return row
    row.update(parse_claude_auth_status(raw, rc))
    return row


def summarize(rows: list[dict]) -> dict:
    counts = {KIND_OK: 0, KIND_WARN: 0, KIND_DANGER: 0, KIND_STOPPED: 0}
    for r in rows:
        kind = r.get("kind", KIND_STOPPED)
        counts[kind] = counts.get(kind, 0) + 1
    counts["total"] = len(rows)
    return counts


async def check_all() -> list[dict]:
    """Both credential rows, worst first. Never raises."""
    rows = await asyncio.gather(check_claude(), check_github(), return_exceptions=True)
    out: list[dict] = []
    for name, row in zip(("claude", "github"), rows):
        if isinstance(row, BaseException):
            log.exception("auth health: %s probe raised", name, exc_info=row)
            out.append({"name": name, "label": name.title(), "kind": KIND_STOPPED,
                        "state": "Unknown", "verified": False,
                        "detail": f"The {name} check failed to run ({row.__class__.__name__})."})
        else:
            out.append(row)
    out.sort(key=lambda r: (SEVERITY.get(r.get("kind", KIND_STOPPED), 2), r.get("name", "")))
    return out


_CACHE = TtlCache(AUTH_TTL_S)
_CACHE_KEY = "credentials"


async def cached_check_all(*, force: bool = False) -> tuple[list[dict], float, bool]:
    """`check_all`, at most once per `AUTH_TTL_S`. Returns `(rows, checked_at, cached)`.

    A run in which anything was unreadable is not cached, so a CLI that was busy
    a second ago is retried rather than remembered as broken.
    """
    if not force:
        hit = _CACHE.get(_CACHE_KEY)
        if hit is not None:
            return hit[0], hit[1], True
    rows = await check_all()
    if any(r.get("kind") == KIND_STOPPED for r in rows):
        _CACHE.invalidate(_CACHE_KEY)
        return rows, time.time(), False
    return rows, _CACHE.put(_CACHE_KEY, rows), False
