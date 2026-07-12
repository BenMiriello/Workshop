"""Context providers — the pluggable bridge between the generalized chat core
and a specific app.

A provider answers three questions for a given chat thread:
  1. persona()        — who the assistant is + how it should behave / its powers
  2. live_context()   — a fresh snapshot of the app's state, injected each turn
  3. apply_actions()  — parse any structured directives the model emitted in its
                        reply, mutate app state, and return human-readable notes

Register a provider per app by adding it to PROVIDERS. The chat route stays
app-agnostic; everything app-specific lives behind this interface. Adding chat
to a new app = write one provider + drop the <ChatDock contextKey="..."> in.

Per-provider context is bounded — Plaid balances + last-30d-summary + computed
budget go in for budget chat; latest commute decision for commute chat; etc.
We never dump raw transaction lists (too large + redundant); the engine has
already categorized them into actuals per bucket.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger("workshop.chat_context")

# The three pickable models surfaced in the composer's model selector, full ids.
# Sourced from llm_router so they never drift from the actual call tiers.
from services import llm_router as _llm  # noqa: E402

PICKABLE_MODELS: list[str] = [
    _llm.TIER_OPUS,    # claude-opus-4-8
    _llm.TIER_SONNET,  # claude-sonnet-4-6
    _llm.TIER_HAIKU,   # claude-haiku-4-5-20251001
]

# Short tier-name aliases accepted from the API alongside the full ids.
MODEL_ALIASES: dict[str, str] = {
    "opus": _llm.TIER_OPUS,
    "sonnet": _llm.TIER_SONNET,
    "haiku": _llm.TIER_HAIKU,
}


def normalize_model(value: str | None) -> str | None:
    """Map an incoming model selector value (short tier name OR full id) to a
    full, pickable model id. Returns None for blank input; raises ValueError if
    the value isn't one of the three pickable models."""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v in MODEL_ALIASES:
        return MODEL_ALIASES[v]
    if v in PICKABLE_MODELS:
        return v
    raise ValueError(
        f"unknown model {value!r}; pick one of {PICKABLE_MODELS} "
        f"or a tier name {list(MODEL_ALIASES)}"
    )


# ---------------------------------------------------------------- ui-card protocol
# Provider-agnostic contract for the general interactive in-chat UI primitive
# (phase-1 spec Part A). ANY provider's model can emit a ```ui-card fenced block;
# the chat frontend renders it as an editable widget (fields + buttons) using the
# design system. It is NOT auto-applied server-side: the backend passes ui-card
# blocks through verbatim (the only apply/strip step is BudgetContextProvider's
# ```plan-patch regex, which does not match this tag), and nothing happens until
# the operator edits the fields and clicks a registered client action. This string
# is appended platform-wide via role_preamble so every provider knows the FORMAT;
# each per-provider persona decides WHEN (and which `kind`) to emit.
UI_CARD_PROTOCOL = (
    "=== INTERACTIVE UI CARDS (ui-card) ===\n"
    "You can render an interactive widget inline in your reply by emitting a "
    "fenced block tagged `ui-card` whose body is JSON. The client renders it as "
    "an editable form (fields + buttons) using the app design system; it is NOT "
    "shown as a code block and is NOT auto-applied. Nothing happens until the "
    "operator edits the fields and clicks an action button. Format:\n\n"
    "```ui-card\n"
    "{\n"
    '  "id": "<stable-slug-or-nonce>",\n'
    '  "kind": "<card-kind>",\n'
    '  "title": "<short heading>",\n'
    '  "fields": [\n'
    '    {"name": "<key>", "label": "<label>", "type": "text", "value": "<prefill>",\n'
    '     "options": [{"value": "...", "label": "..."}], "help": "<optional hint>"}\n'
    "  ],\n"
    '  "actions": [\n'
    '    {"id": "<registered-handler>", "label": "<button text>", "variant": "primary"}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "Field `type` is one of: text | slug | textarea | template | sensitivity | "
    "select. `select` carries `options` as a list of {value,label}; `template` "
    "and `sensitivity` render the standard project controls and need no options. "
    "`help` is an optional one-line hint. `actions[].id` names a handler the "
    "client already knows for that card `kind` (e.g. createProject / dismiss); "
    "`variant` is primary | ghost | danger. Only use a card `kind` your "
    "instructions describe; do not invent kinds (an unknown kind renders as a "
    "dead read-only card). Emit a card ONLY when the operator is actually ready "
    "to take the action it represents, never unprompted and never as decoration. "
    "Explain the action in plain words next to the card, and emit at most one "
    "card per reply."
)

# Platform-wide reply style. The operator's standing rule: no em-dashes and no
# emojis anywhere, including chat replies. Appended to every assistant preamble.
_STYLE_RULE = (
    "=== STYLE ===\n"
    "Write plainly and directly. Never use em-dashes (rephrase with a comma, "
    "period, colon, or parentheses) and never use emojis, in any reply."
)


# ---------------------------------------------------------------- role preamble


def role_preamble(
    *,
    tools_active: bool,
    tool_summary: str = "",
    cwd: str | None = None,
    is_build: bool = False,
) -> str:
    """Platform-owned layer-1 system facts, generated from the ACTUAL run config
    (tools_active is computed in routes/chat.py from the same branch that decides
    tools_disabled/allowed_tools, so the preamble can never drift from reality).

    tools_active=False -> a tools-off safety preamble (no actions, no promises).
    tools_active=True  -> the real toolset + working dir + egress facts.
    is_build=True       -> use the accurate BUILD-context egress facts (clone /
    install from dev hosts is allowed; only push/publish goes through the
    platform), instead of the strict data-context "egress blocked" text.
    """
    if not tools_active:
        return (
            "=== YOUR CAPABILITIES (read this first) ===\n"
            "You have NO tools in this session: you cannot run commands, fetch "
            "data, edit files, browse the web, or take any action. Do not claim "
            "you will do something or that you did it. Answer from the provided "
            "context only; if an action is needed, tell the operator the exact "
            "steps to take or offer to draft the text for them."
            + "\n\n" + UI_CARD_PROTOCOL
            + "\n\n" + _STYLE_RULE
        )
    where = cwd or "the working directory"
    tools = tool_summary or (
        "full Claude Code toolset (Read, Write, Edit, Bash, Grep, Glob, and the "
        "rest)"
    )
    if is_build:
        # Build contexts run under ~/workshop/{projects,branches,build-agents},
        # where the egress + git guards already ALLOW clone/install from standard
        # dev hosts (and any host for the operator's private projects). Telling a
        # builder "egress is blocked" is false and wastes turns — state what is
        # actually true so it just does the work.
        egress = (
            "NETWORK + GIT: you CAN `git clone` and install dependencies "
            "(npm/pnpm/yarn/bun, pip/uv, cargo, go) from standard registries "
            "(github.com, npmjs, pypi, crates.io, proxy.golang.org, jsdelivr/"
            "unpkg/esm.sh, huggingface). Loopback (127.0.0.1) and the tailnet are "
            "always reachable. PUBLISHING goes through the Workshop platform, NOT "
            "your shell: `git push` to main/master, force-push, and repointing "
            "`origin` are blocked by design — commit locally and tell the operator "
            "to publish/deploy through Workshop. If a specific fetch is genuinely "
            "blocked, say so plainly rather than pretending it worked."
        )
    else:
        egress = (
            "NETWORK EGRESS IS RESTRICTED: external network access (git clone, "
            "npm install, curl/wget to public hosts like github.com or "
            "registry.npmjs.org) is BLOCKED in this environment. Only loopback "
            "(127.0.0.1) and the tailnet are reachable. If a task needs an external "
            "fetch, do NOT promise it and do NOT pretend it succeeded: surface that "
            "it is blocked and ask the operator to run it in their own terminal or "
            "to approve the host."
        )
    return (
        "=== YOUR CAPABILITIES (read this first) ===\n"
        f"You have {tools}, operating in {where}. You can read and edit files, "
        "run commands, and search the project.\n\n"
        + egress
        + "\n\n" + UI_CARD_PROTOCOL
        + "\n\n" + _STYLE_RULE
    )


# ----------------------------------------------------------- runtime briefing
# The single highest-leverage builder fix (researched against v0/Bolt/Lovable/
# Replit/Cursor): every build platform injects a TEMPLATED environment briefing
# so the agent never guesses where it is, what it can run, or how to see the app.
# Built fresh from the real run config + app manifest and re-sent every turn
# (including resumed turns) so the agent can't drift out of sync with reality.


def _detect_stack(dir_path) -> tuple[list[str], bool]:
    """Best-effort stack labels for a project dir + whether it looks like an
    empty scaffold (no real source yet). Cheap: stats a handful of marker files.
    Returns (labels, is_scaffold)."""
    from pathlib import Path as _P

    d = _P(dir_path)
    labels: list[str] = []
    has_source = False
    try:
        pkg = d / "package.json"
        if pkg.exists():
            has_source = True
            try:
                data = json.loads(pkg.read_text())
                deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
                if "next" in deps:
                    labels.append("Next.js")
                elif "vite" in deps:
                    labels.append("Vite")
                if "react" in deps:
                    labels.append("React")
                if "typescript" in deps:
                    labels.append("TypeScript")
                node = "Node"
                if (d / "bun.lockb").exists() or (d / "bun.lock").exists():
                    node = "Bun"
                elif (d / "pnpm-lock.yaml").exists():
                    node = "pnpm"
                elif (d / "yarn.lock").exists():
                    node = "yarn"
                labels.insert(0, node)
            except (json.JSONDecodeError, OSError):
                labels.append("Node")
        if (d / "pyproject.toml").exists() or (d / "requirements.txt").exists():
            has_source = True
            labels.append("Python")
        if (d / "go.mod").exists():
            has_source = True
            labels.append("Go")
        if (d / "Cargo.toml").exists():
            has_source = True
            labels.append("Rust")
        if (d / "docker-compose.yml").exists() or (d / "docker-compose.yaml").exists():
            labels.append("docker-compose")
        # A real source tree means an actual FILE somewhere that isn't a scaffold
        # doc or dotfile — an empty `src/` dir does NOT count (that is exactly the
        # commute-shadow scaffold case). Bounded walk; only reached when no
        # package.json/pyproject/etc marker already proved source above.
        if not has_source:
            scaffold_docs = {"PROJECT.md", "TODO.md", "AGENTS.md", "README.md", ".gitignore"}
            checked = 0
            for p in d.rglob("*"):
                checked += 1
                if checked > 2000:
                    break
                rel_parts = p.relative_to(d).parts
                if any(part.startswith(".") for part in rel_parts):
                    continue  # skip .git/.claude/.tasks.json etc.
                if p.is_file() and p.name not in scaffold_docs:
                    has_source = True
                    break
    except (OSError, FileNotFoundError):
        pass
    return labels, (not has_source)


def runtime_briefing(provider: "ContextProvider") -> str:
    """Templated environment briefing for a build provider (empty for others).

    Tells the builder: where it is, the detected stack, the app's services +
    centrally-allocated ports + run/build commands, HOW to run and SEE the app,
    where to read logs, the edit scope, and the negative capabilities (what the
    platform owns so the agent shouldn't fight it). Re-sent every turn."""
    if not is_build_provider(provider):
        return ""
    dirp = provider.dir
    slug = getattr(provider, "slug", "") or ""
    project_slug = getattr(provider, "project_slug", None) or slug

    lines = ["=== RUNTIME BRIEFING (your environment — re-read every turn) ==="]
    lines.append(f"Working directory (cwd): {dirp} — all relative paths resolve here.")
    lines.append(
        "Host: Linux, shell: bash. You ARE a Claude Code session with the full "
        "default toolset (Read, Write, Edit, MultiEdit, Bash, Grep, Glob, "
        "WebFetch, WebSearch, Task). Use them directly — don't ask the operator "
        "to run things you can run yourself."
    )

    labels, is_scaffold = _detect_stack(dirp)
    if labels:
        lines.append("Detected stack: " + ", ".join(labels) + ".")

    # Self / platform project: you are editing the RUNNING Workshop, not a
    # separate deployment. Different rules (hot-reload vs restart, don't deploy,
    # don't commit the whole tree), so emit them and return — the app/env/runner
    # sections below do not apply.
    if getattr(provider, "is_self", False):
        lines.append(
            "THIS PROJECT IS THE LIVE WORKSHOP — you are editing the running app "
            "itself (the monorepo at ~/workshop). There is NO separate deployment; "
            "your edits change the app the operator is using right now.\n"
            "- FRONTEND (frontend/, Vite + React): HOT-RELOADS on save — edits to "
            "frontend/src/... appear in the running UI immediately. Built-in apps live "
            "in frontend/src/pages/apps/<app> (backend logic in backend/services/<app>); "
            "shared primitives in frontend/src/ui. Follow frontend/CLAUDE.md (design "
            "system: lucide icons, no inline hex, the five status kinds).\n"
            "- BACKEND (backend/, FastAPI on :8000): does NOT auto-reload. When you have "
            "FINISHED a backend change, APPLY IT — this is expected, don't be shy about "
            "it: run `bash scripts/restart_app.sh workshop` as your FINAL action. That "
            "does a DEFERRED, detached restart: this reply lands first, then the backend "
            "reloads with your change (the chat is served by that backend, so the deferred "
            "restart is what makes it safe). The only rule: finish all your edits first, "
            "then restart once at the end — never restart in the middle of a multi-step "
            "edit. (Frontend changes are already live via HMR — no restart.)\n"
            "- VALIDATE before claiming done: `cd frontend && npx tsc --noEmit` + "
            "`bash scripts/check_design.sh` for UI; a quick import check for backend.\n"
            "- COMMITS: this is the real repo with real history and unrelated work in "
            "progress. Do NOT `git add -A` / commit the whole tree — stage only the files "
            "you changed, branch off main for non-trivial work, commit only when asked. "
            "Don't pick ports or start servers; they are already running."
        )
        return "\n".join(lines)

    # Shadow detection: a project named after a built-in Workshop app whose real
    # code lives in the monorepo, not this (empty) project dir. The recurring
    # "I was spawned in an empty box orthogonal to the real code" confusion.
    try:
        from services import manifests as _mf
        builtin = _mf.get_manifest(slug) is not None
    except Exception:
        builtin = False
    if is_scaffold and builtin:
        lines.append(
            f"NOTE: this directory is an empty scaffold, but '{slug}' is an "
            "existing built-in Workshop app whose LIVE code is in the monorepo "
            "(~/workshop/backend and ~/workshop/frontend), NOT here. Do not "
            "scaffold a fresh app — read the real code there, and confirm with "
            "the operator which files to edit. You may edit the monorepo files "
            "for this app."
        )
    elif is_scaffold:
        lines.append(
            "NOTE: this directory has no source yet (empty scaffold). If you were "
            "asked to change an existing app, its code is NOT here — confirm where "
            "it lives before building from scratch."
        )

    # App manifest + live ports + how to run / see it.
    try:
        from services import app_runner
        man = app_runner.manifest_dict(project_slug)
        st = app_runner.app_status(project_slug)
        st_by = {s["name"]: s for s in st.get("services", [])}
        svc_lines = []
        for ms in man.get("services", []):
            s = st_by.get(ms["name"], {})
            bits = [ms["name"]]
            if ms.get("expose"):
                bits.append("web")
            if not ms.get("enabled", True):
                bits.append("disabled")
            port = s.get("port")
            if port:
                bits.append(f":{port} ({s.get('status')})")
            cmd = ms.get("start")
            line = "  - " + " ".join(bits)
            if cmd:
                line += f" — start: `{cmd}`"
            svc_lines.append(line)
        if svc_lines:
            lines.append("App services (the runner owns these; ports are allocated centrally):")
            lines.extend(svc_lines)
        lines.append(
            f"To RUN it: start from the project's App panel, or POST "
            f"/base-apps/run/{project_slug}/start. To SEE it: open "
            f"/projects/{project_slug}/run in Workshop (framed, with a nav overlay). "
            f"Logs: GET /base-apps/run/{project_slug}/logs?service=<name>."
        )
    except Exception:
        # No resolvable app manifest (e.g. a freestanding build agent) — skip
        # the app section rather than guessing.
        pass

    # Environment / secrets: what the app needs, what's set, how to declare more.
    try:
        from services import app_runner
        ov = app_runner.env_overview(project_slug)
        reqs = ov.get("requirements") or []
        if reqs or ov.get("undeclared_set"):
            env_lines = ["Environment (declared in workshop.app.json `env_requirements`; "
                         "the operator fills values in the project's Environment panel):"]
            for r in reqs:
                mark = "SET" if r.get("set") else ("MISSING" if r.get("required") else "unset")
                tail = f" — {r['how_to_obtain']}" if r.get("how_to_obtain") else ""
                env_lines.append(f"  - {r['key']} [{mark}{', required' if r.get('required') else ''}]"
                                 f" {r.get('description','')}{tail}")
            miss = ov.get("missing_required") or []
            if miss:
                env_lines.append("  Missing required (the app won't start until set): " + ", ".join(miss))
            env_lines.append(
                "To request a NEW key from the operator: add an entry to "
                "`env_requirements` in workshop.app.json (key, description, required, "
                "secret, how_to_obtain) — it appears in the Environment panel for them "
                "to fill. NEVER hardcode, echo, or print secret VALUES; you can see "
                "which keys are set, never their values."
            )
            lines.append("\n".join(env_lines))
    except Exception:
        pass

    lines.append(
        "PLATFORM OWNS THE RUNTIME: do NOT pick ports or hand-start/stop the dev "
        "servers — the app runner allocates ports centrally, spawns each service, "
        "and auto-restarts crashes.\n"
        "APPLYING A CHANGE (do this when a change is FINISHED — it's expected, the "
        "operator wants the running app updated): a Vite/dev-server service "
        "hot-reloads on save (nothing to do); for a backend service that does NOT "
        "hot-reload, make it live by restarting just that service: "
        f"`bash scripts/restart_app.sh {project_slug} <service>` (re-spawns it on "
        "your new code — separate process, safe). Don't leave the running app on "
        "stale backend code after you've finished a change."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- base / general


class ContextProvider:
    key = "general"
    label = "Clawd"
    # Per-provider tool policy for the chat surface.
    #   allowed_tools : list of MCP tool names ("mcp__plaid__search_transactions")
    #                   the model may call. Empty = tools fully off (single-turn
    #                   text Q&A; the call_cloud path pins max_turns=1).
    #   mcp_servers   : which MCP server specs to spawn in the chat subprocess.
    #                   Must be a subset of llm_router._MCP_SERVER_SPECS keys.
    #   max_turns     : agentic budget when tools are allowed. 1 tool call + 1
    #                   synthesis turn is the floor (2); 4 gives headroom for a
    #                   retry or follow-up lookup. Ignored when allowed_tools is
    #                   empty.
    # Defaults below are deliberately empty — `general` is text-only because
    # cross-cutting chat without a clear data domain doesn't justify the surface.
    allowed_tools: list[str] = []
    mcp_servers: list[str] = []
    max_turns: int = 4
    # Role surfaced to the UI: "assistant" for data/app chats, "builder" for
    # project:/build: providers. Drives the composer label + default model.
    role: str = "assistant"
    # Provider default model (full id). Resolution order at call time is:
    # explicit body.model > thread.model > this default. Assistants default to
    # the fast tier; builders override to a strong tier (see subclasses).
    default_model: str = _llm.TIER_HAIKU

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's personal assistant inside the Workshop app. "
            "The operator is benmiriello — a senior engineer who dislikes padding. Be direct "
            "and brief. Use short paragraphs or tight bullet lists. No emojis.\n\n"
            "You have access to a snapshot of the operator's connected accounts (via Plaid), "
            "live commute state, and current budget plan — read what's given to you each turn "
            "before answering, and DON'T make up numbers when real ones are right there."
        )

    def live_context(self) -> str:
        # General assistant gets a broad system snapshot so it can answer
        # cross-cutting questions ("how much in savings", "next train", etc.)
        # without the operator having to switch tabs.
        parts: list[str] = []
        try:
            parts.append(_finance_snapshot())
        except Exception as e:
            log.warning("general live_context: finance snapshot failed: %s", e)
        try:
            parts.append(_commute_snapshot())
        except Exception as e:
            log.warning("general live_context: commute snapshot failed: %s", e)
        try:
            parts.append(_location_snapshot())
        except Exception as e:
            log.warning("general live_context: location snapshot failed: %s", e)
        return "\n\n".join(p for p in parts if p)

    def apply_actions(self, reply: str) -> tuple[str, list[str]]:
        """Returns (cleaned_reply, notes). Default: nothing to apply."""
        return reply, []

    def custom_prompt(self) -> str:
        """User-owned free-text system prompt (layer 3), appended after the
        platform context and BEFORE the conversation. The platform safety
        preamble (layer 1) always precedes this and is never overridden by it.
        Default empty — the seam exists; no UI is built here yet."""
        return ""

    # The app id this provider's chat is bound to, for permissions resolution.
    # Data/app assistants whose `key` matches an apps.json manifest id (budget,
    # finance, commute, location, ...) bind to that app. The `general` provider
    # and build providers return None (no single app boundary). Subclasses may
    # override if their key differs from their app id.
    def permission_app_id(self) -> str | None:
        from services import manifests as _mf

        if _mf.get_manifest(self.key) is not None:
            return self.key
        return None

    def capability_note(self, identity: Any = "operator") -> str:
        """Capability-aware preamble fragment (doc 01 section 3): a short,
        accurate statement of what this assistant may actually do, derived from
        the permissions framework (the consulted source of truth). Returns "" for
        providers not bound to a single app, so it's safe to append unconditionally.

        DEFAULT-PERMISSIVE: for the operator this states broad read access plus any
        confirm-gated actions — it never narrows or blocks the operator; it only
        gives the model accurate "what you may do" facts so it stops promising
        actions it can't take."""
        app_id = self.permission_app_id()
        if app_id is None:
            return ""
        try:
            from services import permissions
            caps = permissions.assistant_capabilities(app_id, identity)
        except Exception as e:  # never let authz consultation break the chat path
            log.warning("capability_note: permissions consult failed for %s: %s", app_id, e)
            return ""

        lines: list[str] = ["=== WHAT YOU MAY DO (from the permissions framework) ==="]
        lines.append(
            f"You are bound to the '{app_id}' app and may READ its data."
            if caps.get("can_read")
            else f"You are bound to the '{app_id}' app but may NOT read its data."
        )
        actions = caps.get("actions") or {}
        allowed = [a for a, d in actions.items() if d == "allow"]
        confirm = [a for a, d in actions.items() if d == "confirm"]
        denied = [a for a, d in actions.items() if d == "deny"]
        if allowed:
            lines.append("Actions you may take directly: " + ", ".join(sorted(allowed)) + ".")
        if confirm:
            lines.append(
                "Actions that require the operator to confirm first (propose, don't auto-apply): "
                + ", ".join(sorted(confirm)) + "."
            )
        if denied:
            lines.append("Actions you may NOT take: " + ", ".join(sorted(denied)) + ".")
        reach = [a for a in (caps.get("reachable_apps") or []) if a != app_id]
        if reach:
            lines.append("Data from other apps you may reach (granted): " + ", ".join(reach) + ".")
        return "\n".join(lines)


# ---------------------------------------------------------------- budget provider

_PATCH_BLOCK = re.compile(r"```plan-patch\s*\n(.*?)```", re.DOTALL)

# Shared Plaid-lookup method heuristics (general, situation-free) appended to
# every persona that has the transaction-lookup tools. Encodes METHOD only —
# operator-specific facts live in the per-project memory store, not here. These
# two are the exact gaps that once produced a ~$4.3k answer to a since-an-event
# total that should have been ~$16k: no source-account provenance, no widened
# lookback past the 90-day search default.
_PLAID_LOOKUP_METHOD = (
    "\n\nLOOKUP METHOD: (1) TRANSFER ATTRIBUTION — to attribute a transfer to a "
    "person or entity, read the counterparty / source account number: an inflow "
    "from an account that is not one of the operator's own accounts came from "
    "someone else. Don't infer the source from amount or memo text alone. "
    "(2) CUMULATIVE LOOKBACK — when reconstructing a total since an event (a debt "
    "owed, deposits since a date), set the lookback window to span the entire "
    "period; never rely on the 90-day search default — pass an explicit `days` "
    "wide enough to cover from the event to today."
)


class BudgetContextProvider(ContextProvider):
    key = "budget"
    label = "Budget assistant"
    # Budget chat gets read-only Plaid lookups: ad-hoc transaction search +
    # date-range listing for questions the cached 30-day rollup can't answer
    # ("what did I spend at Whole Foods last month", "find Comcast bills in
    # the last 90 days"). NO balance fetch (already in context), NO writes,
    # NO refresh (the finance cache warms on its own cadence).
    allowed_tools = [
        "mcp__plaid__search_transactions",
        "mcp__plaid__list_transactions",
    ]
    mcp_servers = ["plaid"]
    max_turns = 4
    # Budget chat keeps a stronger default — plan-patch path generation wants
    # careful reasoning (operator can still drop to haiku via the picker).
    default_model = _llm.TIER_SONNET

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's budgeting assistant, embedded inside the Workshop "
            "Budget app. The operator's model is a priority allocation waterfall: income → "
            "necessities → prioritized debt payoffs → prioritized savings buckets (emergency "
            "fund first) → variable spend targets → leftover.\n\n"
            "Each turn you receive: (1) the editable plan, (2) live Plaid account balances, "
            "(3) recent-30-day spending actuals already categorized by bucket. USE THESE. The "
            "operator's repeated correction: don't invent numbers you can readily see in the "
            "context. If they ask 'fill in realistic targets', base them on the actual recent "
            "spend totals shown to you, not Mountain-View defaults.\n\n"
            "You can READ the live plan + balances + actuals (given below each turn) and you can "
            "EDIT the plan. To change the plan, include exactly one fenced block in your reply:\n\n"
            "```plan-patch\n"
            '{"set": [{"path": "<dotted.path>", "value": <new value>}, ...]}\n'
            "```\n\n"
            "Path rules: dotted segments. List items are addressed by their `id`. Examples:\n"
            "  income.monthly                      -> the monthly income number\n"
            "  income.per_paycheck\n"
            "  savings.emergency.target            -> emergency bucket's target\n"
            "  savings.emergency.planned_monthly\n"
            "  savings.emergency.target_date       -> 'YYYY-MM-DD' or null\n"
            "  savings.emergency.note              -> free-text note\n"
            "  debts.citi.planned_monthly          -> Citi monthly payment\n"
            "  debts.citi.balance                  -> override balance (live overrides for mask)\n"
            "  necessities.rent.monthly\n"
            "  necessities.rent.name               -> rename\n"
            "  variable.groceries.target           -> monthly groceries budget\n"
            "  variable.groceries.name             -> rename\n\n"
            "Only emit a plan-patch when the operator actually asks to change something. Always "
            "also explain the change in plain words. When only answering a question, do NOT emit a "
            "patch. Be direct and brief. No emojis.\n\n"
            "TOOLS: you have a NARROW read-only Plaid lookup tool set in this chat — ad-hoc "
            "transaction search (mcp__plaid__search_transactions) and date-range listing "
            "(mcp__plaid__list_transactions). Use them ONLY for questions the injected 30-day "
            "rollup and balances above can't answer (e.g. 'what did I spend at Whole Foods last "
            "month', 'find Comcast bills in the last 90 days'). Prefer the context that's already "
            "in front of you; reach for a lookup only when the answer truly isn't there. You have "
            "NO other tools — no Read, Bash, Gmail, web, file edits. If asked for something outside "
            "the plan edits and these Plaid lookups (e.g. 'check my emails for the Citi "
            "statement'), say plainly you can't and point the operator to where to find it. The "
            "authoritative capability statement is in the preamble above this persona."
            + _PLAID_LOOKUP_METHOD
        )

    def live_context(self) -> str:
        # Cheap: plan + live snapshot, computed budget summary, no raw txn dump.
        return _budget_full_context()

    def apply_actions(self, reply: str) -> tuple[str, list[str]]:
        from services import budget_store

        notes: list[str] = []
        m = _PATCH_BLOCK.search(reply)
        if not m:
            return reply, notes

        raw = m.group(1).strip()
        try:
            patch = json.loads(raw)
            ops = patch.get("set", []) if isinstance(patch, dict) else []
        except json.JSONDecodeError:
            return reply, ["(could not parse a plan edit — left the plan unchanged)"]

        if not ops:
            cleaned = _PATCH_BLOCK.sub("", reply).strip()
            return cleaned, notes

        plan = budget_store.load_plan()
        applied: list[str] = []
        failed: list[str] = []
        for op in ops:
            path = op.get("path", "")
            value = op.get("value")
            ok, label = _set_path(plan, path, value)
            (applied if ok else failed).append(label)

        if applied:
            budget_store.save_plan(plan)
            notes.append("Updated plan: " + "; ".join(applied))
        for f in failed:
            notes.append(f"Could not apply: {f}")

        # Strip the machine block from what the operator sees — the note covers it.
        cleaned = _PATCH_BLOCK.sub("", reply).strip()
        return cleaned, notes


# ---------------------------------------------------------------- finance provider


class FinanceContextProvider(ContextProvider):
    """Read-only assistant for the Accounts/Finance page. Knows balances +
    recent spend without the budget-plan editing surface."""
    key = "finance"
    label = "Accounts assistant"
    # Finance chat = strictly read-only Plaid surface. Same transaction
    # lookup as budget, plus holdings/investments since this page also
    # surfaces investment accounts. No balance fetch (cached snapshot is
    # injected each turn; refresh has its own cadence).
    allowed_tools = [
        "mcp__plaid__search_transactions",
        "mcp__plaid__list_transactions",
        "mcp__plaid__get_holdings",
        "mcp__plaid__list_investment_transactions",
    ]
    mcp_servers = ["plaid"]
    max_turns = 4

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's finance assistant, embedded inside the Workshop "
            "Accounts page. You can answer questions about live balances, recent transactions, "
            "and spending patterns — using the snapshot supplied each turn. You CANNOT edit "
            "anything on this page (no plan to mutate); for budget changes, the operator opens "
            "the Budget app. Be direct, brief. No emojis. If asked to do something that requires "
            "writes, say plainly that this view is read-only and point them at the right app."
            + _PLAID_LOOKUP_METHOD
        )

    def live_context(self) -> str:
        return _finance_snapshot() + "\n\n" + _spend_snapshot()


# ---------------------------------------------------------------- commute provider


class CommuteContextProvider(ContextProvider):
    key = "commute"
    label = "Commute assistant"

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's commute assistant, embedded inside the Workshop "
            "Commute app. You see the latest train schedule decision, recent location ping, "
            "and the brief-tracking state. Answer questions about timing, what the alerter "
            "is doing, and when the next decision will fire. Be direct, brief. No emojis. "
            "Read-only — you can't change schedules or send alerts; explain how to do those "
            "in the operator's terminal if asked."
        )

    def live_context(self) -> str:
        return _commute_snapshot() + "\n\n" + _location_snapshot()


# ---------------------------------------------------------------- location provider


class LocationContextProvider(ContextProvider):
    key = "location"
    label = "Location assistant"

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's location assistant, embedded inside the Workshop "
            "Location app. You see the most recent OwnTracks ping (lat/lng/ts) and can answer "
            "questions about where the operator was last seen and how stale that signal is. "
            "Read-only. Be direct, brief. No emojis."
        )

    def live_context(self) -> str:
        return _location_snapshot()


# ---------------------------------------------------------------- new-project provider
# Data-mode, text-only assistant for the "build with chat" flow: shape a project
# idea in conversation, then (only when the operator is ready) emit ONE ui-card of
# kind project-proposal prefilled from the discussion. The ui-card FORMAT comes
# from UI_CARD_PROTOCOL in the role preamble; this persona supplies the WHEN plus
# the field semantics (real template slugs, sensitivity default). No tools, no
# apply_actions override, so the emitted card reaches the client verbatim.


class NewProjectContextProvider(ContextProvider):
    key = "new-project"
    label = "New project"
    role = "assistant"
    # Text-only: no MCP tools, single-turn text path (like `general`).
    allowed_tools: list[str] = []
    mcp_servers: list[str] = []
    # Shaping a project + producing a clean, prefilled proposal card benefits from
    # stronger reasoning than the default fast tier; operator can drop to haiku.
    default_model = _llm.TIER_SONNET

    def persona(self) -> str:
        try:
            from routes.projects import load_templates
            tpls = load_templates()
        except Exception as e:  # never let the template read break the chat path
            log.warning("new-project persona: template load failed: %s", e)
            tpls = [{"slug": "blank", "label": "Blank", "hint": ""}]
        tpl_lines = "\n".join(
            f"  - {t['slug']}: {t.get('label') or t['slug']}"
            + (f" ({t['hint']})" if t.get("hint") else "")
            for t in tpls
        )
        return (
            "You are Clawd, the operator's assistant inside the Workshop app, "
            "helping shape a NEW project before it is created. The operator is "
            "benmiriello, a senior engineer who dislikes padding. Be direct and "
            "brief. No emojis.\n\n"
            "Your job in this chat is to turn an idea into a concrete project. "
            "Draw out and converge on:\n"
            "  - a short title,\n"
            "  - a one-sentence purpose (what it is and who it is for),\n"
            "  - a template chosen from the real templates below,\n"
            "  - an initial brief: goals, constraints, and open questions.\n\n"
            "Available templates (use the SLUG as the template field value):\n"
            f"{tpl_lines}\n\n"
            "Sensitivity defaults to 'shareable' unless the operator asks for "
            "'private' or 'public'.\n\n"
            "When the operator says they are ready (or asks you to create it), "
            "emit exactly one `ui-card` of kind 'project-proposal', prefilled "
            "from the discussion, with these fields: title (text), slug (slug, a "
            "kebab-case version of the title), purpose (text), template "
            "(template), sensitivity (sensitivity), and brief (textarea, the "
            "goals/constraints/open-questions written as markdown). Use actions "
            '[{"id": "createProject", "label": "Create project", "variant": '
            '"primary"}, {"id": "dismiss", "label": "Dismiss", "variant": '
            '"ghost"}]. Do NOT emit the card unprompted; keep shaping the idea '
            "in plain conversation until the operator is actually ready to create "
            "the project."
        )

    def live_context(self) -> str:
        # No live app snapshot for an as-yet-uncreated project; the persona plus
        # the conversation carry everything. (Overrides the base finance/commute/
        # location snapshot, which is irrelevant here.)
        return ""


# ============================================================================
# Live snapshot helpers — each returns a compact, plain-text block. None of
# these block long: balances come from the finance cache (<5ms hot), commute
# decisions are tiny JSON, location is a single file. They tolerate missing
# files / Plaid not connected gracefully.
# ============================================================================


def _money(x: Any) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _money_c(x: Any) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def _finance_snapshot() -> str:
    """Compact balances summary from the finance cache. Doesn't trigger a Plaid
    roundtrip — only reads the already-warm cache file."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
    if not cache_file.exists():
        return "LIVE BALANCES: (no finance cache — Plaid likely not warmed up yet)"
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return "LIVE BALANCES: (finance cache unreadable)"
    bal = (state.get("balances") or {}).get("data") or {}
    items = bal.get("items") or []
    if not items:
        return "LIVE BALANCES: (no banks connected)"
    lines = ["LIVE BALANCES (from Plaid, cached):"]
    for item in items:
        for acct in item.get("accounts", []) or []:
            mask = acct.get("mask") or "?"
            name = acct.get("name") or acct.get("official_name") or "Account"
            subtype = acct.get("subtype") or acct.get("type") or ""
            cur = (acct.get("balance") or {}).get("current")
            limit = (acct.get("balance") or {}).get("limit")
            if subtype == "credit card":
                # For credit, `current` is the amount owed. Limit + available is informative.
                lines.append(
                    f"- {name} ··{mask} ({subtype}): owed {_money_c(cur)}"
                    + (f", limit {_money(limit)}" if limit else "")
                )
            else:
                lines.append(f"- {name} ··{mask} ({subtype}): {_money_c(cur)}")
    return "\n".join(lines)


def _spend_snapshot(days: int = 30) -> str:
    """Last-N-day spend categorized by Plaid primary category. Reads the cached
    transactions and bucketizes on the fly — small enough computation that the
    extra ~5ms beats trying to share state with budget_engine."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
    if not cache_file.exists():
        return ""
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return ""

    txns_entry = state.get(f"transactions:{days}") or state.get("transactions:30") or {}
    txns_data = txns_entry.get("data")
    if isinstance(txns_data, list):
        txns = txns_data
    elif isinstance(txns_data, dict):
        txns = txns_data.get("transactions") or []
    else:
        txns = []
    if not isinstance(txns, list):
        txns = []
    if not txns:
        return f"LAST {days}D SPEND: (no transactions in cache)"

    by_primary: dict[str, float] = {}
    total_out = 0.0
    excluded = ("LOAN_PAYMENTS", "TRANSFER_IN", "TRANSFER_OUT", "INCOME", "BANK_FEES_OVERDRAFT")
    for t in txns:
        amt = t.get("amount")
        if amt is None or amt <= 0:
            continue
        cat = t.get("category_detailed") or t.get("category") or "UNKNOWN"
        if any(cat.startswith(p) for p in excluded):
            continue
        # Primary = first segment of detailed cat
        primary = cat.split("_")[0] if cat else "UNKNOWN"
        # For FOOD_AND_DRINK keep two segments so groceries vs eating-out are distinguishable
        if cat.startswith("FOOD_AND_DRINK"):
            primary = "FOOD_AND_DRINK_" + cat.split("_", 3)[3] if len(cat.split("_")) > 3 else "FOOD_AND_DRINK"
        elif cat.startswith("RENT_AND_UTILITIES"):
            primary = "RENT_AND_UTILITIES"
        elif cat.startswith("TRANSPORTATION"):
            primary = "TRANSPORTATION"
        elif cat.startswith("GENERAL_MERCHANDISE"):
            primary = "GENERAL_MERCHANDISE"
        elif cat.startswith("GENERAL_SERVICES"):
            primary = "GENERAL_SERVICES"
        elif cat.startswith("ENTERTAINMENT"):
            primary = "ENTERTAINMENT"
        elif cat.startswith("TRAVEL"):
            primary = "TRAVEL"
        else:
            primary = cat.split("_")[0]
        by_primary[primary] = by_primary.get(primary, 0.0) + float(amt)
        total_out += float(amt)

    # Sort largest first
    rows = sorted(by_primary.items(), key=lambda kv: -kv[1])
    lines = [f"LAST {days}D SPEND (Plaid categories, transfers/income/payments excluded):"]
    lines.append(f"  Total: {_money_c(total_out)}")
    for cat, amt in rows[:12]:  # cap at top 12 — long-tail is noise
        lines.append(f"  - {cat}: {_money_c(amt)}")
    return "\n".join(lines)


def _budget_full_context() -> str:
    """Plan + balances + computed actuals + per-bucket pacing. The richest
    context we ship to any provider — the budget chat needs all of it to
    answer questions or fill realistic targets."""
    try:
        from services import budget_store, budget_engine

        plan = budget_store.load_plan()
        # Reuse the cached balances + txns rather than re-fetching from Plaid.
        # The engine wants the full transactions-response dict (it pulls
        # ["transactions"] internally), so pass that through verbatim.
        balances, txns_resp = _read_finance_cache_raw()
        computed = budget_engine.compute(plan, balances, txns_resp, window_days=30)
    except Exception as e:
        log.warning("budget context build failed: %s", e, exc_info=True)
        return build_budget_context()  # fall back to plain plan dump

    out: list[str] = ["CURRENT PLAN (editable):"]
    inc = plan.get("income", {})
    out.append(
        f"- Income: {_money(inc.get('monthly'))}/mo "
        f"({_money(inc.get('per_paycheck'))} x {inc.get('paychecks_per_month')} paychecks)"
    )
    nec = [n for n in plan.get("necessities", []) if n.get("monthly")]
    if nec:
        out.append("- Necessities: " + ", ".join(
            f"{n['name']} {_money(n['monthly'])} (id:{n['id']})" for n in nec
        ))
    else:
        out.append("- Necessities: none set (rent/utilities/phone/insurance all $0)")
    for d in plan.get("debts", []):
        if d.get("balance") or d.get("planned_monthly"):
            extra = f", promo until {d['promo_until']}" if d.get("promo_until") else ""
            out.append(
                f"- Debt {d['name']} (id:{d['id']}): balance {_money(d.get('balance'))}, "
                f"paying {_money(d.get('planned_monthly'))}/mo{extra}"
            )
    for s in plan.get("savings", []):
        if s.get("target") or s.get("current") or s.get("planned_monthly"):
            out.append(
                f"- Savings {s['name']} (id:{s['id']}): {_money(s.get('current'))} / "
                f"{_money(s.get('target'))} target, saving {_money(s.get('planned_monthly'))}/mo"
            )
    var = [v for v in plan.get("variable", []) if v.get("target")]
    if var:
        out.append("- Variable targets: " + ", ".join(
            f"{v['name']} {_money(v['target'])} (id:{v['id']})" for v in var
        ))
    else:
        ids = [v["id"] for v in plan.get("variable", [])]
        out.append("- Variable spend targets: none set yet (ids: " + ", ".join(ids) + ")")

    # Live snapshot — balances first.
    out.append("")
    out.append(_finance_snapshot())

    # Computed actuals per bucket (this is the "what was actually spent" signal
    # the operator wanted — derived from real transactions, not made up).
    out.append("")
    out.append("LAST 30D ACTUAL SPEND PER BUCKET (categorized, transfers excluded):")
    for v in computed.get("variable", []):
        target_s = _money(v.get("target", 0)) if v.get("target") else "no target"
        pct = v.get("pct")
        pct_s = f" ({pct*100:.0f}%)" if isinstance(pct, (int, float)) else ""
        flag = " OVER" if v.get("over") else ""
        out.append(
            f"  - {v['name']} (id:{v['id']}): actual {_money_c(v.get('actual', 0))} / "
            f"target {target_s}{pct_s}{flag}"
        )
    actuals = computed.get("actuals", {})
    out.append(
        f"  Necessities (rent/utilities autodetected): {_money_c(actuals.get('necessities', 0))}; "
        f"unclassified spend: {_money_c(actuals.get('unclassified', 0))}"
    )

    # Summary numbers — what's allocated vs leftover.
    s = computed.get("summary", {})
    out.append("")
    out.append("COMPUTED ALLOCATION (waterfall):")
    out.append(
        f"  Income {_money(s.get('income'))}/mo  -  "
        f"necessities {_money(s.get('necessities_planned'))}  -  "
        f"debt {_money(s.get('debt_planned'))}  -  "
        f"savings {_money(s.get('savings_planned'))}  -  "
        f"variable targets {_money(s.get('variable_target'))}"
    )
    out.append(
        f"  = committed {_money(s.get('committed'))}, "
        f"discretionary {_money(s.get('discretionary'))}, "
        f"leftover {_money(s.get('leftover'))}"
    )
    if s.get("shortfall"):
        out.append("  SHORTFALL: planned allocations exceed income.")
    return "\n".join(out)


def _read_finance_cache_raw() -> tuple[dict, dict]:
    """Direct read of the finance cache file — sync, no Plaid call. Returns
    (balances_response, transactions_response) — both raw shapes from the
    Plaid-bridge, ready to hand to budget_engine.compute which expects dicts
    (balances has .items, txns has .transactions). Safe empty defaults so the
    caller never needs to None-check the cache."""
    from pathlib import Path
    import os

    cache_file = Path(os.environ.get(
        "WORKSHOP_FINANCE_CACHE",
        "/home/clawd-a/workshop/.runtime/finance-cache.json",
    ))
    if not cache_file.exists():
        return {"items": []}, {"transactions": []}
    try:
        state = json.loads(cache_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"items": []}, {"transactions": []}
    bal = (state.get("balances") or {}).get("data") or {"items": []}
    txn_entry = state.get("transactions:30") or {}
    txn_data = txn_entry.get("data")
    if isinstance(txn_data, list):
        txn_data = {"transactions": txn_data}
    elif not isinstance(txn_data, dict):
        txn_data = {"transactions": []}
    return bal, txn_data


def _commute_snapshot() -> str:
    """Latest commute decision + tick state. The commute app writes a poll/decide
    result into .runtime/commute/ — we surface the latest one."""
    from pathlib import Path

    runtime = Path("/home/clawd-a/workshop/.runtime/commute")
    if not runtime.exists():
        return "COMMUTE: (no commute runtime dir)"
    # Latest schedule snapshot
    latest_files = sorted(
        runtime.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    lines: list[str] = ["COMMUTE (latest snapshot):"]
    found_any = False
    for p in latest_files[:3]:
        try:
            d = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ts = d.get("ts") or "?"
        agency = d.get("agency") or ""
        feeds = d.get("feeds") or {}
        trip_count = (feeds.get("trip_updates") or {}).get("entity_count")
        vehicle_count = (feeds.get("vehicle_positions") or {}).get("entity_count")
        lines.append(
            f"- {p.name} ts={ts} agency={agency} trips={trip_count} vehicles={vehicle_count}"
        )
        found_any = True
        # First decision is enough — break after one
        break
    if not found_any:
        lines.append("(no parseable snapshots)")

    # Subscription count (informational only). The file mixes meta keys (_schema)
    # with source-keyed agent lists, so we just report the count of real source
    # entries — nothing fancy.
    sub_file = Path("/home/clawd-a/workshop/.runtime/agent-subscriptions.json")
    if sub_file.exists():
        try:
            subs = json.loads(sub_file.read_text())
            if isinstance(subs, dict):
                real_sources = [k for k in subs if not k.startswith("_")]
                lines.append(f"  Subscribed sources: {len(real_sources)} ({', '.join(real_sources[:6])})")
        except (json.JSONDecodeError, OSError):
            pass
    return "\n".join(lines)


def _location_snapshot() -> str:
    """Latest position with an explicit freshness verdict.

    The phone tracker (OwnTracks/Overland) can silently stop posting for hours,
    so the presence of a 'latest' ping is NOT proof of current whereabouts. We
    label anything older than the staleness window as stale and tell the model in
    plain words not to treat a stale fix as the operator's current location.
    """
    from datetime import datetime, timezone

    STALE_AFTER_MIN = 30  # matches commute LOCATION_STALE_AFTER_S and the UI

    # Read the canonical, ownership-aware store — NOT a hardcoded path. (The old
    # /home/.../.runtime/location-latest.json was moved to per-user runtime by the
    # ownership migration; reading it directly returned "no pings" forever.)
    try:
        from services import location as location_svc  # local import: avoid cycle
        d = location_svc.latest()
    except Exception:  # noqa: BLE001 — no owner pinned / unreadable => unknown
        d = None
    if not d:
        return ("LOCATION: unknown — no recent location ping available. Do NOT "
                "guess or imply where the operator is.")

    ts = d.get("ts")
    age_min: float | None = None
    if ts:
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age_min = (datetime.now(timezone.utc) - t).total_seconds() / 60
        except (ValueError, TypeError):
            age_min = None

    if age_min is None:
        return (f"LOCATION: last ping at {ts}, age unknown — treat as possibly "
                f"stale; do not assume it is the operator's current location.")

    if age_min < 60:
        age = f"{age_min:.0f} min ago"
    elif age_min < 1440:
        age = f"{age_min / 60:.1f} h ago"
    else:
        age = f"{age_min / 1440:.1f} d ago"

    coords = f"lat {d.get('lat')}, lng {d.get('lng')}, src={d.get('source','?')}"
    if age_min > STALE_AFTER_MIN:
        return (
            f"LOCATION (STALE — last KNOWN position from {age}, NOT necessarily "
            f"where the operator is now): {coords}. The phone tracker may have "
            f"stopped updating. Do NOT state or imply this is his current "
            f"location — call it the last known fix from {age} and say live "
            f"location is unavailable."
        )
    return f"LOCATION (live, {age}): {coords}."


# ============================================================================
# Path resolution for plan-patch (used by BudgetContextProvider.apply_actions)
# ============================================================================


def _set_path(plan: dict, path: str, value: Any) -> tuple[bool, str]:
    """Resolve a dotted path (list items keyed by `id`) and set the leaf.
    Returns (ok, human_label)."""
    parts = [p for p in path.split(".") if p]
    if not parts:
        return False, f"{path} (empty path)"
    node: Any = plan
    trail: list[str] = []
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if isinstance(node, dict):
            if last:
                if seg not in node:
                    return False, f"{path} (no field '{seg}')"
                node[seg] = value
                return True, f"{'.'.join(trail + [seg])} = {value}"
            if seg not in node:
                return False, f"{path} (no field '{seg}')"
            node = node[seg]
            trail.append(seg)
        elif isinstance(node, list):
            match = next((it for it in node if isinstance(it, dict) and it.get("id") == seg), None)
            if match is None:
                return False, f"{path} (no item id '{seg}')"
            if last:
                # setting a whole list item is unusual; require a sub-field
                return False, f"{path} (point at a field of '{seg}')"
            node = match
            trail.append(seg)
        else:
            return False, f"{path} (cannot descend into '{seg}')"
    return False, f"{path} (unresolved)"


def build_budget_context() -> str:
    """A compact, current snapshot of the editable plan. Plan-only fallback for
    when the engine isn't reachable; main path goes through _budget_full_context."""
    from services import budget_store

    plan = budget_store.load_plan()
    lines = ["CURRENT PLAN (editable):"]
    inc = plan.get("income", {})
    lines.append(
        f"- Income: {_money(inc.get('monthly'))}/mo "
        f"({_money(inc.get('per_paycheck'))} x {inc.get('paychecks_per_month')} paychecks)"
    )
    nec = [n for n in plan.get("necessities", []) if n.get("monthly")]
    if nec:
        lines.append("- Necessities: " + ", ".join(
            f"{n['name']} {_money(n['monthly'])} (id:{n['id']})" for n in nec
        ))
    else:
        lines.append("- Necessities: none set (rent/utilities/phone/insurance all $0)")
    for d in plan.get("debts", []):
        if d.get("balance") or d.get("planned_monthly"):
            extra = f", promo until {d['promo_until']}" if d.get("promo_until") else ""
            lines.append(
                f"- Debt {d['name']} (id:{d['id']}): balance {_money(d.get('balance'))}, "
                f"paying {_money(d.get('planned_monthly'))}/mo{extra}"
            )
    for s in plan.get("savings", []):
        if s.get("target") or s.get("current") or s.get("planned_monthly"):
            lines.append(
                f"- Savings {s['name']} (id:{s['id']}): {_money(s.get('current'))} / "
                f"{_money(s.get('target'))} target, saving {_money(s.get('planned_monthly'))}/mo"
            )
    var = [v for v in plan.get("variable", []) if v.get("target")]
    if var:
        lines.append("- Variable targets: " + ", ".join(
            f"{v['name']} {_money(v['target'])} (id:{v['id']})" for v in var
        ))
    else:
        lines.append("- Variable spend targets: none set yet (ids: "
                     + ", ".join(v["id"] for v in plan.get("variable", [])) + ")")
    return "\n".join(lines)


# ---------------------------------------------------------------- project provider
# Build-mode chat scoped to a Projects-hub project. The provider is parameterized
# by the project slug; context_key looks like "project:<slug>". Reads PROJECT.md,
# AGENTS.md, and TODO.md as the system context every turn so the agent always has
# the brief, the rules, and the live checklist in front of it.

import os as _os
from pathlib import Path as _Path


def _projects_root() -> _Path:
    return _Path(_os.environ.get(
        "WORKSHOP_PROJECTS_ROOT_NEW",
        str(_Path.home() / "workshop" / "projects"),
    ))


class ProjectContextProvider(ContextProvider):
    """Build-mode provider for a single Project. Routes call_cloud with cwd
    set to the project directory and the full default agentic toolset enabled
    (Read/Write/Edit/Bash/Grep/MultiEdit/Glob) so the chat behaves like a
    terminal-resident `claude` session inside the project."""

    label = "Build · project"
    # Build mode runs the default agentic path in call_cloud (NOT the
    # tools_disabled or allowed_tools narrow paths). Leaving these empty
    # signals routes/chat.py to switch into the project-build branch.
    allowed_tools = []
    mcp_servers = []
    # Vibe-coding wants real headroom; tasks routinely span 20+ tool calls.
    max_turns = 60
    role = "builder"
    # Builders default to a strong model for codegen quality; opus is selectable.
    default_model = _llm.TIER_SONNET

    def __init__(self, slug: str):
        self.slug = slug
        self.key = f"project:{slug}"
        # Honor the registry's `dir` (the self/platform project points at the
        # monorepo root, not projects/<slug>); fall back to the conventional path.
        self.dir = _projects_root() / slug
        self.is_self = False
        try:
            reg = _projects_root().parent / "projects.json"
            rec = (json.loads(reg.read_text()).get("projects") or {}).get(slug) if reg.exists() else None
            if rec:
                if rec.get("dir"):
                    self.dir = _Path(rec["dir"])
                self.is_self = bool(rec.get("self"))
        except Exception:
            pass

    def _read(self, name: str, default: str = "") -> str:
        p = self.dir / name
        try:
            return p.read_text() if p.exists() else default
        except Exception:
            return default

    def persona(self) -> str:
        rules = self._read("AGENTS.md")
        return (
            "You are the build agent for the operator's Workshop Project "
            f"'{self.slug}'. You have full Claude Code tool access inside the "
            f"project directory ({self.dir}). The operator is benmiriello — a "
            "senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current "
            "goal and constraints, then act. When you finish a task, update "
            "TODO.md (check items off, add follow-ups). When the operator gives "
            "a non-trivial direction, append a one-line decision note to "
            "PROJECT.md.\n\n"
            f"Project-local agent rules:\n\n{rules or '(none)'}"
        )

    def live_context(self) -> str:
        brief = self._read("PROJECT.md")
        todo = self._read("TODO.md")
        parts = []
        if brief:
            parts.append("=== PROJECT.md (brief) ===\n" + brief)
        if todo:
            parts.append("=== TODO.md ===\n" + todo)
        return "\n\n".join(parts)


# ---------------------------------------------------------------- build-agent provider
# Build agents are Claude Code coding sessions surfaced under the Agents tab,
# distinct from Projects. context_key looks like "build:<slug>". Same Build-mode
# behavior as a Project chat: full agentic toolset, cwd pinned to the agent's
# directory (which is either a Project dir for project-linked agents or a
# freestanding scratch dir).


class BuildAgentContextProvider(ContextProvider):
    label = "Build · agent"
    allowed_tools = []
    mcp_servers = []
    max_turns = 60
    role = "builder"
    default_model = _llm.TIER_SONNET

    def __init__(self, slug: str):
        # Defer build_agents import to avoid circulars at module load.
        from services import build_agents as _ba
        try:
            record = _ba.get_agent(slug)
        except KeyError:
            record = {"name": slug, "purpose": "", "cwd": str(_Path.home() / "workshop"), "project_slug": None}
        self.slug = slug
        self.key = f"build:{slug}"
        self.name = record.get("name") or slug
        self.purpose = record.get("purpose") or ""
        self.dir = _Path(record["cwd"])
        self.project_slug = record.get("project_slug")

    def _read(self, name: str, default: str = "") -> str:
        p = self.dir / name
        try:
            return p.read_text() if p.exists() else default
        except Exception:
            return default

    def persona(self) -> str:
        rules = self._read("AGENTS.md")
        return (
            f"You are the build agent '{self.name}' inside the Workshop. You have full "
            f"Claude Code tool access inside the directory {self.dir}. The operator is "
            "benmiriello — a senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current goal and "
            "constraints, then act. When you finish a task, update TODO.md (check items off, "
            "add follow-ups). When the operator gives a non-trivial direction, append a "
            "one-line decision note to PROJECT.md.\n\n"
            f"Build-agent rules:\n\n{rules or '(none)'}"
        )

    def live_context(self) -> str:
        parts = []
        brief = self._read("PROJECT.md")
        todo = self._read("TODO.md")
        if brief:
            parts.append("=== PROJECT.md (brief) ===\n" + brief)
        if todo:
            parts.append("=== TODO.md ===\n" + todo)
        return "\n\n".join(parts)


# ---------------------------------------------------------------- registry

PROVIDERS: dict[str, ContextProvider] = {
    "general": ContextProvider(),
    "budget": BudgetContextProvider(),
    "finance": FinanceContextProvider(),
    "commute": CommuteContextProvider(),
    "location": LocationContextProvider(),
    "new-project": NewProjectContextProvider(),
}


def _project_slugs() -> list[str]:
    """Read project slugs from the projects.json registry, if present."""
    reg = _projects_root().parent / "projects.json"
    if not reg.exists():
        return []
    try:
        data = json.loads(reg.read_text())
        return sorted((data.get("projects") or {}).keys())
    except Exception:
        return []


def _build_agent_slugs() -> list[str]:
    try:
        from services import build_agents as _ba
        return sorted(_ba.slugs())
    except Exception:
        return []


def is_build_provider(provider: ContextProvider) -> bool:
    """A build provider runs the full agentic Claude Code path."""
    return isinstance(provider, (ProjectContextProvider, BuildAgentContextProvider))


def capability_for(
    provider: ContextProvider, *, enable_chat_tools: bool
) -> tuple[bool, str]:
    """SINGLE SOURCE OF TRUTH for "does this provider get tools in the actual
    run?" — used both to build the role preamble (so it can't drift) and to
    report `tools_active` from /providers.

    Mirrors the branch order in routes/chat.send_message exactly:
      - build providers always run the agentic toolset -> True
      - data providers get narrow MCP tools ONLY when ENABLE_CHAT_TOOLS is on
        AND they declare a non-empty allowed_tools list -> else tools-off.

    Returns (tools_active, tool_summary). tool_summary is human-readable text
    for the preamble's layer-1 tool list.
    """
    if is_build_provider(provider):
        return True, (
            "full Claude Code tool access (Read, Write, Edit, Bash, Grep, Glob, "
            "MultiEdit, and the rest)"
        )
    if enable_chat_tools and provider.allowed_tools:
        return True, (
            "a narrow read-only tool set: " + ", ".join(provider.allowed_tools)
        )
    return False, ""


def get_provider(context_key: str | None) -> ContextProvider:
    if context_key and context_key.startswith("project:"):
        slug = context_key.split(":", 1)[1]
        return ProjectContextProvider(slug)
    if context_key and context_key.startswith("build:"):
        slug = context_key.split(":", 1)[1]
        return BuildAgentContextProvider(slug)
    return PROVIDERS.get(context_key or "general", PROVIDERS["general"])


def _provider_entry(provider: ContextProvider, label: str | None = None) -> dict:
    """Rich /providers entry the composer relies on: key, label, role, the
    pickable models, the default model, and whether this provider actually gets
    tools in the current run config."""
    from routes.chat import ENABLE_CHAT_TOOLS

    tools_active, _summary = capability_for(provider, enable_chat_tools=ENABLE_CHAT_TOOLS)
    return {
        "key": provider.key,
        "label": label if label is not None else provider.label,
        "role": provider.role,
        "models": list(PICKABLE_MODELS),
        "default_model": provider.default_model,
        "tools_active": tools_active,
    }


def list_providers(scope: str | None = None) -> list[dict]:
    """Provider directory for the composer/picker.

    scope=None  -> the FULL list (static data providers + every discovered
                   project + build-agent provider). The Agents page needs this.
    scope=<key> -> ONLY the single matching provider entry (an in-app dock
                   passes its own contextKey and must not see unrelated
                   projects/build agents). Returns [] if the key resolves to
                   nothing valid.

    Each entry: {key, label, role, models, default_model, tools_active}.
    """
    if scope:
        provider = get_provider(scope)
        # get_provider falls back to the `general` provider for unknown keys;
        # only return an entry if the resolved key actually matches the request
        # (so a bogus scope doesn't silently expose `general`).
        if provider.key != scope and scope not in PROVIDERS:
            return []
        # Preserve the nicer dynamic labels for project:/build: keys.
        if scope.startswith("project:"):
            label = f"Build · project · {scope.split(':', 1)[1]}"
        elif scope.startswith("build:"):
            label = f"Build · agent · {scope.split(':', 1)[1]}"
        else:
            label = None
        return [_provider_entry(provider, label)]

    out = [_provider_entry(p) for p in PROVIDERS.values()]
    for slug in _project_slugs():
        out.append(_provider_entry(
            ProjectContextProvider(slug), f"Build · project · {slug}"
        ))
    for slug in _build_agent_slugs():
        out.append(_provider_entry(
            BuildAgentContextProvider(slug), f"Build · agent · {slug}"
        ))
    return out
