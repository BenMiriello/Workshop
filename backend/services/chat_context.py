"""Context providers — the pluggable bridge between the generalized chat core
and a specific app.

A provider answers three questions for a given chat thread:
  1. persona()        — who the assistant is + how it should behave / its powers
  2. live_context()   — a fresh snapshot of the app's state, injected each turn
  3. apply_actions()  — parse any structured directives the model emitted in its
                        reply, mutate app state, and return human-readable notes

This module is the PLATFORM chat plumbing: the ContextProvider base, the general
+ project/build providers, the role preamble, and dispatch. It
hardcodes NO app. A built-in app's own persona lives in `services/app_personas`
and is registered through the app's AppSpec (`app_registry`: `chat_provider` /
`chat_snapshot`), so a persona appears only when its app is installed and adding
a new app's assistant never edits this file. Adding chat to a fresh app = write
one provider (in the app) + register it + drop the <ChatDock contextKey="..."> in.

Per-provider context is bounded — Plaid balances + last-30d-summary + computed
budget go in for budget chat; latest commute decision for commute chat; etc.
We never dump raw transaction lists (too large + redundant); the engine has
already categorized them into actuals per bucket.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

log = logging.getLogger("workshop.chat_context")


def operator_name() -> str:
    """Display name for the operator in assistant personas. Per-instance and
    env-driven: WORKSHOP_OPERATOR_NAME, else a generic label. Deliberately does
    NOT read the operator auth-email env — that is an auth-config value and must
    never be consulted at request-time code (see tests/test_ownership.py); a
    persona label is request-time text."""
    return os.environ.get("WORKSHOP_OPERATOR_NAME", "").strip() or "the operator"

# The three pickable models surfaced in the composer's model selector, full ids.
# Sourced from llm_router so they never drift from the actual call tiers.
from services import llm_router as _llm  # noqa: E402

PICKABLE_MODELS: list[str] = [
    _llm.TIER_OPUS,    # claude-opus-4-8
    _llm.TIER_FABLE,   # claude-fable-5
    _llm.TIER_SONNET,  # claude-sonnet-4-6
    _llm.TIER_HAIKU,   # claude-haiku-4-5-20251001
]

# Short tier-name aliases accepted from the API alongside the full ids.
MODEL_ALIASES: dict[str, str] = {
    "opus": _llm.TIER_OPUS,
    "fable": _llm.TIER_FABLE,
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

# Skill loader protocol (Phase 5). Data-mode assistants see the available skill
# names + descriptions ambiently (in the cache-stable prefix); to pull a skill's
# FULL instructions they emit this fenced block and the backend feeds the body
# back for one more turn. Builders never see this — they discover skills natively
# from their cwd `.claude/skills/`.
USE_SKILL_PROTOCOL = (
    "To load a skill's full instructions, emit a fenced block tagged `use_skill` "
    "whose only content is the skill name:\n\n"
    "```use_skill\n"
    "<skill-name>\n"
    "```\n"
    "The skill's instructions will be provided and you continue the same reply. "
    "Request a skill ONLY when the current task matches its description; otherwise "
    "just reply normally. Emit at most one use_skill block per reply."
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


# ------------------------------------------------------------ learned memory
# Stage 8 (2f): learnings ride into a chat's cache-stable prefix. The workspace
# `global` guardrails go into EVERY agent's context; a scoped agent/project also
# carries its OWN accumulated memory. Kept append-ordered (oldest first) so a new
# learning extends the tail and leaves the cached prefix bytes stable. Best-effort
# and self-contained (mirrors runtime_briefing's shape) so a missing store never
# breaks the chat path.


def learnings_context(provider: "ContextProvider") -> str:
    """The learned-memory block for a provider's chat: workspace guardrails plus,
    for a scoped agent/project/app, that scope's own accumulated memory. Empty
    string when there is nothing to inject."""
    try:
        from services import learnings
    except Exception:
        return ""
    parts: list[str] = []
    try:
        g = learnings.memory_block(learnings.GLOBAL)
    except Exception:
        g = ""
    if g:
        parts.append("=== LEARNINGS (workspace guardrails) ===\n" + g)
    try:
        scope = provider.knowledge_scope()
    except Exception:
        scope = "workshop"
    if scope not in (learnings.GLOBAL, "workshop"):
        try:
            m = learnings.memory_block(scope)
        except Exception:
            m = ""
        if m:
            parts.append(f"=== AGENT MEMORY (scope: {scope}) ===\n" + m)
    return "\n\n".join(parts)


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

    def knowledge_scope(self) -> str:
        """The knowledge scope_key this provider reads/writes (workshop /
        project:<slug> / app:<name> / agent:<id>). Derived from the provider key;
        project/build providers override to point at their project scope."""
        from services import knowledge
        return knowledge.scope_for_context(self.key)

    def persona(self) -> str:
        return (
            "You are Clawd, the operator's personal assistant inside the Workshop app. "
            f"The operator is {operator_name()} — a senior engineer who dislikes padding. Be direct "
            "and brief. Use short paragraphs or tight bullet lists. No emojis.\n\n"
            "Below each turn you may be given live snapshots from the operator's installed apps "
            "(accounts, budget, location, commute, etc.) — read what's given to you before "
            "answering, and DON'T make up numbers when real ones are right there."
        )

    def live_context(self) -> str:
        # General assistant gets a broad system snapshot so it can answer
        # cross-cutting questions ("how much in savings", "next train", etc.)
        # without the operator having to switch tabs. The snapshots come from the
        # INSTALLED apps that register one (app_registry) — no app is hardcoded
        # here, so an instance that doesn't install finance/commute/… simply omits
        # that section.
        from services import app_registry

        parts: list[str] = []
        for snap in app_registry.installed_snapshots():
            try:
                parts.append(snap())
            except Exception as e:
                log.warning("general live_context: a snapshot failed: %s", e)
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
            f"project directory ({self.dir}). The operator is {operator_name()} — a "
            "senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current "
            "goal and constraints, then act. Task state lives in the structured "
            "store, NOT in TODO.md (which is a generated export, never edit it "
            "directly). To mark a task done, add a follow-up, or block one, run "
            "`python3 ~/workshop/scripts/project_tasks.py <verb> .` via Bash "
            "(verbs: set-status <id> done, add \"text\", block <id> \"reason\", "
            "note <id> \"text\", next-ready). When the operator gives a non-trivial "
            "direction, append a one-line decision note to PROJECT.md.\n\n"
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
            f"{operator_name()} — a senior engineer who dislikes padding. Be direct and brief.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current goal and "
            "constraints, then act. Task state lives in the structured store, NOT in "
            "TODO.md (a generated export, never edit it directly). Mark tasks done / add "
            "follow-ups / block via `python3 ~/workshop/scripts/project_tasks.py <verb> .` "
            "over Bash (set-status <id> done, add \"text\", block <id> \"reason\", "
            "note <id> \"text\", next-ready). When the operator gives a non-trivial "
            "direction, append a one-line decision note to PROJECT.md.\n\n"
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


class AssistantAgentContextProvider(ContextProvider):
    """A registry assistant agent (`agent:<slug>`) as a chat provider: a data-tier
    assistant whose persona comes from its markdown definition body, scoped to its
    own knowledge namespace. This is what makes registry assistants reachable from
    a chat surface (before this they silently fell back to `general`). Data-tier
    tools (web + anything it declares), no project cwd, no build."""
    role = "assistant"
    max_turns = 8
    default_model = _llm.TIER_HAIKU

    def __init__(self, slug: str, record: dict):
        self.slug = slug
        self.key = f"agent:{slug}"
        self.name = record.get("name") or slug
        self.label = self.name
        self.purpose = record.get("purpose") or ""
        self._body = (record.get("definition") or "").strip()

    def persona(self) -> str:
        base = (
            f"You are {self.name}, an assistant agent inside the Workshop app. The "
            f"operator is {operator_name()} — a senior engineer who dislikes padding. "
            "Be direct and brief. Use short paragraphs or tight bullet lists. No emojis."
        )
        if self.purpose:
            base += f"\n\nPurpose: {self.purpose}"
        if self._body:
            base += f"\n\n{self._body}"
        base += (
            "\n\nYou can maintain your own task list. To add, update, or note tasks, "
            "emit ONE fenced block (it is applied to your task scope and hidden from "
            "your reply):\n```task-patch\n{\"add\": [{\"text\": \"...\", \"priority\": 0}], "
            "\"update\": [{\"id\": \"...\", \"status\": \"done\"}]}\n```"
        )
        return base


# ---------------------------------------------------------------- registry

# EVERY data/assistant provider gets web access — the route + capability_for add
# WebSearch/WebFetch centrally via effective_allowed_tools(), so providers no
# longer need to opt in per-instance. (budget/finance keep their Plaid tools and
# gain web; commute/location gain web; build/project agents run the full toolset.)
# The only per-instance tweak left is a roomier turn budget for the open-ended
# general assistant so a research answer isn't cut short.
_general = ContextProvider()
_general.max_turns = 8

# Platform providers — always present, no app required: the general assistant.
# App personas are NOT listed here; they come from the installed apps' AppSpecs
# (see provider_map()). Chat and Project are loosely coupled: a general chat can
# be rescoped to project:<slug> / app:<name> / agent:<slug> as a binding change,
# there is no separate "new project" chat pipeline.
_PLATFORM_PROVIDERS: dict[str, ContextProvider] = {
    "general": _general,
}


def provider_map() -> dict[str, ContextProvider]:
    """The dispatchable provider map: platform providers + one per INSTALLED app
    that registers a chat provider (app_registry). Built per call so installing/
    removing an app changes the available personas with no edit here — a persona
    for a not-installed (or not-shipped) app is simply absent."""
    from services import app_registry

    providers: dict[str, ContextProvider] = dict(_PLATFORM_PROVIDERS)
    for factory in app_registry.installed_chat_providers():
        try:
            p = factory()
            providers[p.key] = p
        except Exception as e:  # a broken persona must not sink the whole map
            log.warning("chat provider build failed: %s", e)
    return providers


def _project_slugs() -> list[str]:
    """Read project slugs from the projects registry (per-instance, .runtime/)."""
    from routes.projects import REGISTRY as _reg
    if not _reg.exists():
        return []
    try:
        data = json.loads(_reg.read_text())
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


# Built-in web tools every data/assistant chat gets (no MCP server needed).
# Operator rule: ALL agents can search + fetch the web, on top of whatever MCP
# tools the provider declares. Build/project agents already run the full toolset.
WEB_TOOLS = ["WebSearch", "WebFetch"]


def effective_allowed_tools(provider: ContextProvider) -> list[str]:
    """A data provider's declared MCP tools + the shared web tools, de-duped and
    order-stable. This is the SINGLE place that grants web, so the route and
    capability_for can't drift. Not used for build providers (full toolset)."""
    return list(dict.fromkeys([*provider.allowed_tools, *WEB_TOOLS]))


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
    if enable_chat_tools:
        # Every data/assistant agent is tools-active: web search + fetch, plus any
        # MCP tools it declares (e.g. Plaid for budget/finance).
        return True, ("a read-only tool set: " + ", ".join(effective_allowed_tools(provider)))
    return False, ""


def get_provider(context_key: str | None) -> ContextProvider:
    if context_key and context_key.startswith("project:"):
        slug = context_key.split(":", 1)[1]
        return ProjectContextProvider(slug)
    if context_key and context_key.startswith("build:"):
        slug = context_key.split(":", 1)[1]
        return BuildAgentContextProvider(slug)
    if context_key and context_key.startswith("agent:"):
        # A registry agent bound to this chat. Builders resolve to the build
        # provider (their runtime); assistants resolve to the assistant provider
        # (persona from the definition body). An unknown slug falls back to
        # general so a stale binding never 500s the chat.
        slug = context_key.split(":", 1)[1]
        try:
            from services import agents_registry
            rec = agents_registry.get(slug)
        except Exception:
            rec = None
        if rec and not rec.get("retired"):
            if rec.get("runtime") == "builder":
                return BuildAgentContextProvider(slug)
            return AssistantAgentContextProvider(slug, rec)
    providers = provider_map()
    return providers.get(context_key or "general", providers["general"])


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
        if provider.key != scope and scope not in provider_map():
            return []
        # Preserve the nicer dynamic labels for project:/build: keys.
        if scope.startswith("project:"):
            label = f"Build · project · {scope.split(':', 1)[1]}"
        elif scope.startswith("build:"):
            label = f"Build · agent · {scope.split(':', 1)[1]}"
        else:
            label = None
        return [_provider_entry(provider, label)]

    out = [_provider_entry(p) for p in provider_map().values()]
    for slug in _project_slugs():
        out.append(_provider_entry(
            ProjectContextProvider(slug), f"Build · project · {slug}"
        ))
    for slug in _build_agent_slugs():
        out.append(_provider_entry(
            BuildAgentContextProvider(slug), f"Build · agent · {slug}"
        ))
    return out
