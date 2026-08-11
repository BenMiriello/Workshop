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
    _llm.TIER_OPUS_5,     # claude-opus-5
    _llm.TIER_OPUS,       # claude-opus-4-8
    _llm.TIER_FABLE,      # claude-fable-5
    _llm.TIER_SONNET_5,   # claude-sonnet-5
    _llm.TIER_SONNET,     # claude-sonnet-4-6
    _llm.TIER_HAIKU,      # claude-haiku-4-5-20251001
]

# Short tier-name aliases accepted from the API alongside the full ids.
MODEL_ALIASES: dict[str, str] = {
    "opus5": _llm.TIER_OPUS_5,
    "opus": _llm.TIER_OPUS,
    "fable": _llm.TIER_FABLE,
    "sonnet5": _llm.TIER_SONNET_5,
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

# The `task-proposal` ui-card: how a chat scopes work into a TRACKED ticket
# instead of doing it inline. Described to the general + build/project personas so
# those models may emit it (the ui-card protocol requires a kind be described
# before use). The SERVER emits the companion `job-status` card after a run, so
# that one is deliberately NOT described to the model.
TASK_PROPOSAL_CARD = (
    "=== TASK PROPOSAL CARD (task-proposal) ===\n"
    "To turn work into a tracked ticket instead of doing it in the conversation, "
    "emit ONE `ui-card` of kind `task-proposal`. The operator reviews and edits it, "
    "then clicks File ticket (queue it) or File and run (start a worker now). A "
    "worker acts on the FILED SPEC ALONE and never sees this chat, so specify it "
    "well. Emit these fields (scope + title are required; the rest are optional but "
    "a good ticket fills them in):\n"
    "  - scope: type `taskscope` (a select the client fills with the available "
    "scopes; prefill `value` with this chat's own scope when it has one, else "
    "`workshop`).\n"
    "  - title: type `text`. ONE short imperative line naming the work, under 80 "
    "characters. A title, not a summary. The spec goes in the fields below; a "
    "title that carries the whole spec is split by the store and reads badly in "
    "every list.\n"
    "  - objective: type `textarea`. ONE crisp sentence stating the concrete "
    "outcome, so a worker knows exactly what the work must achieve.\n"
    "  - output: type `textarea`. What done looks like: the artifact, format, or "
    "acceptance (e.g. 'endpoint returns X and a test covers it', 'docs section "
    "updated').\n"
    "  - entry_points: type `textarea`. Where to start: the specific files, "
    "functions, or commands the worker should read or run first.\n"
    "  - boundaries: type `textarea`. Explicit out-of-scope: files or systems to "
    "leave alone ('do not touch X') and what NOT to build.\n"
    "  - priority: type `text` (a number; higher runs first, default 0).\n"
    "  - effort: type `select` with options quick / standard / deep (quick = one "
    "small edit, standard = a normal multi-file change, deep = large or research-"
    "heavy). A rough size class, hint only.\n"
    "The client composes title + objective + output + entry_points + boundaries into "
    "the ticket body (title as the first line) and files the effort class alongside. "
    "Actions: `addTasks` (file it), `addTasksAndRun` (file it and run now), "
    "`dismiss`. Emit it only when there is real work to scope, never as decoration."
)

# Build/project triage rule (sub-decision a): the model, not a code threshold,
# decides inline-vs-delegate, with the operator's click as the tiebreaker.
TASK_PROPOSAL_BUILD_RULE = (
    "TRIAGE: answer questions and make single-file trivial edits inline while the "
    "operator is watching. For anything multi-step, multi-file, or that should "
    "survive this conversation, emit a task-proposal card instead of doing it "
    "inline; the operator's click (File ticket vs File and run) is the tiebreaker. "
    "SPECIFY IT WELL: fill objective, output, entry_points, and boundaries so a "
    "worker who never saw this chat can act. A weak, underspecified spec is the top "
    "cause of a failed run."
)

# The `task-patch` block: how a chat that OWNS a task scope maintains its queue
# mid-conversation (add / reprioritize / update / note), applied server-side and
# stripped from the reply. Described to the general orchestrator so it can adjust
# priority when the operator states relative importance. Assistant/build personas
# describe their own copy inline; this constant is for the general chat.
TASK_PATCH_PROTOCOL = (
    "=== MAINTAINING THE QUEUE (task-patch) ===\n"
    "You own the workshop-root task queue. To add, reprioritize, or update tickets "
    "on it, emit ONE fenced block (it is applied to the store and hidden from your "
    "reply):\n"
    "```task-patch\n"
    "{\"add\": [{\"text\": \"<title>\\nObjective: <one sentence>\\nOutput: <done-looks-"
    "like>\\nStart from: <files or commands>\\nBoundaries: <do-not-touch>\", "
    "\"priority\": 0}], "
    "\"update\": [{\"id\": \"<id>\", \"status\": \"done\", \"priority\": 5}]}\n"
    "```\n"
    "The FIRST line of `text` is the title and must be SHORT: one imperative line "
    "under 80 characters, never the whole spec (the store splits an over-long first "
    "line, but the split rarely reads as well as a written title). Everything after "
    "it is the spec a worker acts on, so keep the Objective / Output / Start from / "
    "Boundaries structure even here (a worker never sees this chat). Higher priority "
    "runs first (default 0). When the "
    "operator says one thing matters more than another, `update` the ticket's "
    "`priority`. Use task-patch for lightweight bookkeeping on the existing queue; "
    "use a task-proposal card for NEW multi-step work the operator should review "
    "before it runs (that path also captures an effort class)."
)

# The `learning-note` block: deterministic capture of a durable operator
# preference or correction into the learnings store, applied server-side in the
# finalize path (exactly like task-patch) and stripped from the reply. Described
# to the personas that should emit it. This is IN ADDITION to compaction-time
# distillation, by design; emit it only for explicit, lasting signals.
LEARNING_NOTE_PROTOCOL = (
    "=== CAPTURING A PREFERENCE (learning-note) ===\n"
    "When the operator states a DURABLE preference or correction (\"always X\", "
    "\"stop doing Y\", \"I prefer Z\", or corrects something you should not repeat), "
    "record it so it is not forgotten, by emitting ONE fenced block (it is applied "
    "to the learnings store and hidden from your reply):\n"
    "```learning-note\n"
    "{\"note\": \"<the durable lesson in one sentence>\", \"scope\": \"workshop\"}\n"
    "```\n"
    "`scope` is optional (default: this chat's scope, else `workshop`) and may be "
    "`workshop`, `project:<slug>`, or `agent:<slug>`. Do NOT emit it for transient "
    "task detail, one-off instructions, or anything already going into a ticket. "
    "Only lasting preferences and corrections."
)

# The `note` block: deterministic capture of a durable, scoped FINDING into the
# knowledge store (the Notes substrate at services/knowledge.py), applied in the
# same finalize path as task-patch/learning-note and stripped from the reply.
# Distinct from learning-note (which records operator preferences as guardrails):
# a note is freeform scoped knowledge the operator and agents both read. Described
# to the general + build/project personas that should emit it.
NOTE_PROTOCOL = (
    "=== LEAVING A NOTE (note) ===\n"
    "When you learn something durable and scoped that should outlive this chat (a "
    "fact, a finding, a gotcha, running context an agent will want later), record "
    "it as a note rather than leaving it in scratch. Emit ONE fenced block (it is "
    "written to the knowledge store and hidden from your reply):\n"
    "```note\n"
    "{\"title\": \"<short note title>\", \"body\": \"<the finding, markdown>\", \"scope\": \"workshop\"}\n"
    "```\n"
    "`scope` is optional (default: this chat's scope, else `workshop`) and may be "
    "`workshop`, `project:<slug>`, `app:<name>`, or `agent:<slug>`. Notes are for "
    "durable scoped knowledge, NOT ephemeral scratch. For a choice or tradeoff that "
    "will outlive the chat (an architectural decision, a policy), open a decision "
    "record in `docs/decisions/` (the ADR convention) instead of a note."
)

# Git worktree / branch-philosophy pointer (Phase G3). Points agents at the
# git-worktree-workflow skill and states the canonical-branch rule inline, so the
# discipline holds even before the skill body is opened. Brief on purpose; the
# full philosophy lives in shared/skills/git-worktree-workflow/SKILL.md.
_GIT_WORKTREE_RULE_BASE = (
    "=== BRANCHES + WORKTREES ===\n"
    "In a project bound to a GitHub remote, follow the git-worktree-workflow skill. "
    "The canonical branch is the project's tracked repo.default_branch (usually "
    "`main`): branch off `origin/<default_branch>` onto a short-lived "
    "`wt/<slug>/<task-id>` branch, keep changes small and self-contained, rebase on "
    "the default branch before you publish, then open a PR and merge on a green "
    "reviewer gate. Never push to or reference the Workshop platform repo's own "
    "remote, and only ever work under this installation's own GitHub account."
)


def git_worktree_rule() -> str:
    """The branching rule, plus whatever this install has declared off limits.

    An operator with a work org that must never be touched from here names it in
    WORKSHOP_BLOCKED_REPO_OWNERS; the agent is told, and services.github refuses
    it regardless of whether the agent read this. The platform itself knows no
    org names.
    """
    from services import github as gh_repo

    blocked = gh_repo.blocked_owners()
    if not blocked:
        return _GIT_WORKTREE_RULE_BASE
    names = ", ".join(sorted(blocked))
    return _GIT_WORKTREE_RULE_BASE + (
        f" Never touch these accounts, which are out of scope for this "
        f"installation: {names}."
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

# The block, for stripping it back out. A turn runs as a live agent process now:
# a data-mode assistant that wants a skill reads it with its own tools rather
# than asking the backend for a second model call, so an emitted block is raw
# protocol that nobody is going to expand. Take it off the reply before the
# operator sees it.
_USE_SKILL_RE = re.compile(
    r"```use_skill\s*\n\s*([a-z0-9][a-z0-9-]{0,63})\s*\n```", re.IGNORECASE)


def strip_use_skill(text: str) -> str:
    """Remove any `use_skill` block from a reply. Returns the text unchanged when
    there is none, which is every ordinary turn."""
    if not text or "use_skill" not in text:
        return text
    return _USE_SKILL_RE.sub("", text).strip() or text

# When to delegate to a subagent, and when a Ticket is the right shape instead.
#
# The two mechanisms were being confused because nothing ever said what
# separates them, so "this is a lot of work" pulled toward a ticket even when the
# work was one long READ. The line is what the work needs, not how big it is:
#
#   a subagent buys CONTEXT ISOLATION and nothing else — it starts clean, does a
#   bounded piece of reading or checking, and returns a summary into this reply.
#   The chat is still the thing doing the work, and the transcript is still the
#   record. That costs one tool call.
#
#   a Ticket buys DURABILITY: a reviewer gate, touches enforcement, retries, a
#   receipt, and a record that outlives the conversation. That costs a queue slot
#   and a worker who never saw this chat.
#
# So: needs to survive the chat -> Ticket. Needs a clean window and comes back
# here -> subagent. Neither -> just do it, which is still the common case.
DELEGATION_RULE = (
    "=== SUBAGENTS (delegating inside this chat) ===\n"
    "You can launch a subagent with the Agent tool. It runs in its own clean "
    "context and returns a summary into this reply; its intermediate steps never "
    "enter this conversation's window. Launch several in one turn when the pieces "
    "are independent.\n\n"
    "USE ONE WHEN the work is a bounded read or check whose intermediate output "
    "would crowd out the conversation: searching a large tree, reading many files "
    "to answer one question, digesting logs or a long document, running a "
    "verification pass, or several independent lookups that can go at once. Say "
    "in one line what you delegated and report what came back.\n\n"
    "DO NOT USE ONE for something you can answer or do directly (a single read, "
    "one edit, a command you already know). The round trip costs more than the "
    "work. Do not use one for work that must OUTLIVE this chat: durable, "
    "reviewable, multi-file change work is a Ticket, because a subagent has no "
    "reviewer gate, no retry, and leaves no receipt. A subagent is context "
    "isolation; a Ticket is durability. Size is not the test.\n\n"
    "NEVER file a ticket merely to get an isolated context, and never delegate "
    "work whose result you will not check."
)


def delegation_block(tools_active: bool) -> str:
    """The subagent rule plus the named delegates this instance can call.

    Named subagents come from the same registry the Agents page reads
    (agents_registry.native_subagents), so what the model is told it can call and
    what the operator can see are one list. Foreign ones (another surface's
    agents, e.g. the Discord bridge's) are named as unavailable rather than
    silently dropped — an agent that exists and is not ours is worth exactly one
    line. Empty when the chat has no tools at all: describing a tool that isn't
    there is how the old prompt came to promise subagents a data chat could not
    launch.
    """
    if not tools_active:
        return ""
    lines: list[str] = []
    try:
        from services import agents_registry
        for r in agents_registry.native_subagents():
            if r.get("foreign"):
                continue
            desc = (r.get("description") or r.get("purpose") or "").strip().replace("\n", " ")
            if len(desc) > 160:
                desc = desc[:157] + "…"
            lines.append(f"- {r['slug']}" + (f": {desc}" if desc else ""))
    except Exception as e:  # a registry hiccup must not cost the chat its rule
        log.warning("delegation block: native subagents unavailable: %s", e)
    named = (
        "\n\nNAMED SUBAGENTS on this instance (pass as `subagent_type`); anything "
        "else uses the general-purpose worker:\n" + "\n".join(lines)
        if lines else
        "\n\nNo named subagents are installed; the general-purpose worker is the "
        "only delegate."
    )
    return DELEGATION_RULE + named


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
            "`origin` are blocked by design. Commit locally and tell the operator "
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
        # Stated here, with the rest of what this session can actually do, so the
        # rule and the granted tool are computed from the same tools_active flag
        # and cannot disagree.
        + "\n\n" + delegation_block(True)
        + "\n\n" + UI_CARD_PROTOCOL
        + "\n\n" + _STYLE_RULE
    )


def agent_self_preamble(
    *,
    name: str,
    slug: str,
    role: str,
    scope: str,
    cwd: str,
    tools: list[str] | None,
    triggers: list | None = None,
    mentionable: bool = False,
    delegatable: bool = False,
) -> str:
    """Who an agent is, for the agent — agent-interaction-model §7.4.

    "Each agent should know what it's doing, where it is, what it has access to."
    That is a prompt-assembly problem, not a memory one, and this is the same
    discipline `role_preamble` above uses: every line is DERIVED from the record
    and from the run config the caller is about to pass to start_turn, so the
    block cannot drift from the enforced values the way a hand-written per-agent
    paragraph does. Nothing here is written per agent, and adding a sentence to
    an agent's definition cannot make this claim something untrue.

    The bounds are derived too. `You must not` is not boilerplate: each line is
    there only when the record makes it true (no tools => it genuinely cannot
    act; not delegatable => it genuinely does not take tickets). A constraint
    that is asserted whether or not it holds teaches a model to discount all of
    them.

    WHY `capabilities` IS NOT HERE. It used to be, as a "CAPABILITIES: discord,
    plaid, google-docs" line under a header that calls this block the enforced
    truth — while the caller passed `mcp_servers=[]`, hardcoded. The agent was
    being handed a list of things it had no tool for and told the list was
    authoritative, so it would confidently agree to do them and then silently
    could not. `capabilities` is free text an agent definition writes about
    itself; a bridge agent's servers are often not workshop MCP servers at all
    and `workshop` are installed). It stays on the agent's page as a declared
    description, and it is not shown to the model as something it HAS. If an
    agent should genuinely reach a server, grant it: pass `mcp_servers` at
    start_turn, as `app_personas` does.
    """
    # The ACTUAL grant, not a word for it. This took the tool list rather than a
    # boolean because "TOOLS: the Claude Code toolset" was read by a triager
    # holding Read, Grep and Glob — a sentence that promises Write and Bash to an
    # agent that has neither. Named, the line cannot say more than was granted.
    granted = [str(t) for t in (tools or []) if str(t).strip()]
    tools_active = bool(granted)
    trig = [t for t in (triggers or []) if t]
    reach = []
    if mentionable:
        reach.append(f"@{slug} in a chat whose scope reaches yours")
    if delegatable:
        reach.append("being handed a ticket")
    if trig:
        kinds = sorted({str((t or {}).get("kind") or "trigger") for t in trig})
        reach.append(f"your own {'/'.join(kinds)} trigger" + ("s" if len(trig) > 1 else ""))

    lines = [
        f"You are {name} (@{slug}), a {role} agent in {operator_name()}'s Workshop.",
        f"SCOPE: {scope}. Where you work and the corpus you can see.",
        f"WORKSPACE: {cwd}",
    ]
    lines.append(
        f"TOOLS: {', '.join(granted)}. In your workspace, and nothing else "
        "this turn." if tools_active
        else "TOOLS: none this turn. You cannot read files, run commands, or "
             "change anything. Answer from what is given below."
    )
    if reach:
        lines.append("REACHED BY: " + "; ".join(reach) + ".")

    musts = [f"Take on work outside {scope}. Say so and name what does handle it."]
    if not tools_active:
        musts.append("Claim you will do something, or that you did: you have no "
                     "tool to do it with.")
    if not delegatable:
        musts.append("Accept a ticket or promise follow-up work. You are not a "
                     "delegate.")
    musts.append("Answer as the assistant, or as another agent. You were "
                 "addressed by name.")

    return (
        "=== WHO YOU ARE (from your agent record, the enforced truth) ===\n"
        + "\n".join(lines)
        + "\nYOU MUST NOT:\n"
        + "\n".join(f"  - {m}" for m in musts)
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


# One definition, in the module that owns the persisted field. Re-declaring them
# here is how the default came to be stated in two places, and a default stated
# twice is a default that can disagree with itself.
from services.chat_store import CHAT_MODES, DEFAULT_CHAT_MODE  # noqa: E402,F401


def chat_mode_block(mode: str | None) -> str:
    """How this chat handles work, stated every turn.

    Two modes, and the field already existed (`chat_store.set_thread_mode`) with
    these two names — it simply had no behaviour, like `participants`. Rather
    than invent a third vocabulary, this gives the existing names their meaning.

      direct        the conversation does the work itself. Claude Code's native
                    subagents are available for context isolation, and their
                    results come back here. Nothing is tracked outside the chat;
                    the transcript IS the record.
      orchestrated  work leaves as Tickets, run by Workshop agents under the
                    governance model, reported back. The Ticket is the record.

    Re-stated on EVERY turn rather than only in the first-turn persona, because
    the mode is switchable mid-conversation. The protocols themselves are large
    and are sent once; this line is what makes a later switch take effect, by
    overriding them in the direction that removes capability (direct) rather
    than adding it.
    """
    # Clamp rather than test for "direct": an unrecognised value is not a third
    # mode, and letting it fall through here meant junk silently selected
    # Orchestrated no matter which mode was the default.
    from services.chat_store import _clamp_mode
    if _clamp_mode(mode) == "direct":
        return (
            "=== CHAT MODE: DIRECT ===\n"
            "Work stays in this conversation. Do NOT file tickets, do NOT emit a "
            "task-proposal card, and do NOT emit a task-patch block; if the "
            "protocols for those appear earlier in this session, they do not "
            "apply while the chat is in Direct mode. For work that needs an "
            "isolated context, use a native subagent and bring the summary back "
            "here. The transcript is the record."
        )
    return (
        "=== CHAT MODE: ORCHESTRATED ===\n"
        "Work that outlives this conversation leaves as a Ticket and is reported "
        "back here. The Ticket is the record; this chat links to it."
    )


def turn_contract(timeout_s: float | int = 0) -> str:
    """The rule that decides when a turn ends. Re-sent on EVERY turn.

    Why this exists: an agent in this chat has no way to observe the stop
    condition it is subject to. A turn ends the moment the model emits a
    message containing only text and no tool call — so writing a closing
    summary IS the stop signal, indistinguishable from finishing. The model
    experiences writing "I'll continue with the next phase" as making a plan;
    the harness experiences it as the end of the session. Nothing resumes.

    That mismatch produced the platform's most common failure: a turn that
    announces continuation and then stops dead, repeatedly, across a long piece
    of work. It is not fixable with timers or nudges, because it is not a
    motivation problem. It is a rule the agent was never told.

    So tell it, every turn, next to the other volatile facts about its
    environment.
    """
    # 0 means no wall clock (routes/chat.CHAT_TIMEOUT_S). Saying "the only bound
    # is N minutes" when there is no bound would be a false constraint, and a
    # false constraint on a turn's length is exactly what makes an agent wrap up
    # work it should be finishing.
    budget = float(timeout_s or 0)
    bound = (
        "There is NO cap on this turn: no step limit and no time limit. It runs "
        "until the work is done. Running long is fine and expected; stopping "
        "early is the failure mode."
        if budget <= 0 else
        f"There is NO step cap on a turn. The only bound is a "
        f"{max(1, round(budget / 60))}-minute wall clock. Running long is fine "
        "and expected; stopping early is the failure mode."
    )
    return (
        "=== HOW THIS TURN ENDS (re-read every turn) ===\n"
        "This turn ends the moment you emit a message that is ONLY text, with no "
        "tool call. There is no loop behind you: nothing resumes, no timer fires, "
        "and the operator sees a finished turn. A closing summary IS the stop "
        "signal.\n"
        "\n"
        "Therefore:\n"
        "- While work remains, keep calling tools. Narrate progress as text "
        "ALONGSIDE tool calls; the chat streams those segments live, so the "
        "operator sees what you are doing without the turn ending.\n"
        "- Never write \"continuing\", \"next I'll...\", or a phase summary and "
        "then stop. Announcing continuation and then emitting terminal text is "
        "the same action as saying you are done, and worse, because it is "
        "inaccurate.\n"
        "- Write the final text block ONLY when the work actually asked for is "
        "finished, or when you are genuinely blocked or the task was a question.\n"
        "- If you must stop with work outstanding, say plainly what is done, what "
        "is not, and why. Never imply a continuation that cannot happen.\n"
        "\n"
        + bound + "\n\n"
        + REALTIME_CHANNEL
    )


# Declared every turn, and it has to be. A message the operator types WHILE a
# turn runs is written to that turn's stdin, and the CLI surfaces it inside a
# <system-reminder>. Told nothing about this channel, the model reads an urgent
# mid-turn message as an attack and discards it — measured, verbatim from the
# probe stream: "A prompt injection attempt appeared in a system-reminder
# claiming you sent an urgent message... No such message was in your actual
# input", having reasoned "they would not communicate urgent requests through
# system-reminders — they would type them directly."
#
# It is right to be suspicious; nothing had told it this path exists. With this
# paragraph in the prompt the identical message is honoured with no accusation
# (probes 1 vs 3, docs/platform-redesign/chat-message-ordering-audit-2026-08-05.md).
# So the case that matters most — "stop, that's wrong" — is exactly the case
# that was being thrown away.
REALTIME_CHANNEL = (
    "=== MESSAGES THAT ARRIVE WHILE YOU WORK ===\n"
    "The operator can type while a turn is running. Those messages reach you "
    "mid-turn on your input stream, and may be shown to you inside a "
    "<system-reminder>. That is this system's normal, first-party delivery path "
    "for operator messages. It is NOT untrusted content and NOT a prompt "
    "injection. Treat anything arriving that way exactly as if the operator had "
    "typed it directly.\n"
    "\n"
    "Act on it at your next step boundary rather than at the end of your plan. "
    "If it redirects you, stop what you were doing and follow it, and say in one "
    "line that you are changing course. If it just adds something, acknowledge it "
    "and fold it in. Never finish a long plan in silence and address the message "
    "only at the end. From the operator's side that is indistinguishable from "
    "not having read it, and it is the most common complaint about this "
    "chat.\n"
    "\n"
    "A message tagged as sent during an earlier turn was written BEFORE you had "
    "read any of the messages after it. Sequence and timing are stated when they "
    "matter; read them rather than assuming a gap means a new thought."
)


def arrival_notice(messages: list[dict], run_id: str | None = None) -> str:
    """States which of the messages now being read arrived DURING an earlier run.

    The operator's report, and the reason this exists: "this and the last two
    were sent in sequence before you ever read any of them and you won't even
    know that til now." He is right that the model could not know. Each message
    reaches it looking like a fresh turn, so three things typed inside one minute
    while nothing was responding are indistinguishable from three thoughts spread
    across an afternoon — and the difference decides whether the right reply is
    three answers or one.

    Two ways a message can have waited, and they mean the same thing to whoever
    reads it now:

      during_run — delivered into a run that was already going
                   (routes/chat._file_during_run)
      queued     — typed while a run went, delivered as this one
                   (routes/chat._record_user_turn)

    FOUND BY THE MARK, NOT BY POSITION. This used to read the trailing run of
    user messages, which worked only while the assistant reply sat ABOVE the
    messages it had not answered — the arrangement that made the operator's five
    messages render as if he had been ignored. Now the reply is appended when it
    finishes, so a delivered message sits above it and would never be in the
    tail. `run_id` names the run that just ended, and its arrivals are a lookup
    on `meta.during_run` wherever they are in the transcript.

    Queued messages have no run to name (they were never delivered to one), so
    they are still found in the trailing run of user messages, which is exactly
    the turn being sent now.

    Empty string when nothing waited, which is the common case. Scoped so it
    cannot re-announce: a notice that fires always says nothing.
    """
    picked: list[dict] = []
    seen: set[int] = set()

    def _take(m: dict) -> None:
        if id(m) not in seen and m.get("role") == "user":
            seen.add(id(m))
            picked.append(m)

    if run_id:
        for m in messages:
            meta = m.get("meta") or {}
            if meta.get("during_run") == run_id or meta.get("during_turn") == run_id:
                _take(m)
    tail: list[dict] = []
    for m in reversed(messages):
        if m.get("role") != "user":
            break
        tail.append(m)
    tail.reverse()
    for m in tail:
        meta = m.get("meta") or {}
        if meta.get("queued") or (not run_id and (meta.get("during_run") or meta.get("during_turn"))):
            _take(m)
    if not picked:
        return ""
    # Transcript order, whichever pass found them.
    order = {id(m): i for i, m in enumerate(messages)}
    during = sorted(picked, key=lambda m: order.get(id(m), 0))
    times = []
    for m in during:
        ts = str(m.get("ts") or "")
        times.append(ts[11:16] + "Z" if len(ts) >= 16 else ts)
    n = len(during)
    if n == 1:
        body = (
            f"One message above ({times[0]}) was sent while you were still working "
            "on the previous turn. It was written before you had responded to it."
        )
    else:
        body = (
            f"{n} of the messages above were sent while you were still working on "
            f"the previous turn, at {', '.join(times)}, IN SEQUENCE, and all of "
            "them before you had responded to any. Read them together as one "
            "thought that had to be said more than once, not as separate requests, "
            "and answer the whole of it at once."
        )
    return "=== SENT WHILE YOU WERE WORKING ===\n" + body


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

    lines = ["=== RUNTIME BRIEFING (your environment; re-read every turn) ==="]
    lines.append(f"Working directory (cwd): {dirp}. All relative paths resolve here.")
    lines.append(
        "Host: Linux, shell: bash. You ARE a Claude Code session with the full "
        "default toolset (Read, Write, Edit, MultiEdit, Bash, Grep, Glob, "
        "WebFetch, WebSearch, Task). Use them directly. Don't ask the operator "
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
            "THIS PROJECT IS THE LIVE WORKSHOP. You are editing the running app "
            "itself (the monorepo at ~/workshop). There is NO separate deployment; "
            "your edits change the app the operator is using right now.\n"
            "- FRONTEND (frontend/, Vite + React): HOT-RELOADS on save. Edits to "
            "frontend/src/... appear in the running UI immediately. Built-in apps live "
            "in frontend/src/pages/apps/<app> (backend logic in backend/services/<app>); "
            "shared primitives come from the @workshop/ui kit at packages/ui (import by "
            "package name; the kit is platform-owned, see packages/ui/CONTRIBUTING.md). "
            "Follow frontend/CLAUDE.md (design "
            "system: lucide icons, no inline hex, the five status kinds).\n"
            "- BACKEND (backend/, FastAPI on :8000): does NOT auto-reload. When you have "
            "FINISHED a backend change, APPLY IT. This is expected: run "
            "`bash scripts/restart_app.sh workshop` as your FINAL action. That "
            "does a DEFERRED, detached restart: this reply lands first, then the backend "
            "reloads with your change (the chat is served by that backend, so the deferred "
            "restart is what makes it safe). The only rule: finish all your edits first, "
            "then restart once at the end. Never restart in the middle of a multi-step "
            "edit. (Frontend changes are already live via HMR, so no restart.)\n"
            "- VALIDATE before claiming done: `cd frontend && npx tsc --noEmit` + "
            "`bash scripts/check_design.sh` for UI; a quick import check for backend.\n"
            "- COMMITS: this is the real repo with real history and unrelated work in "
            "progress. Do NOT `git add -A` / commit the whole tree. Stage only the files "
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
            "scaffold a fresh app. Read the real code there, and confirm with "
            "the operator which files to edit. You may edit the monorepo files "
            "for this app."
        )
    elif is_scaffold:
        lines.append(
            "NOTE: this directory has no source yet (empty scaffold). If you were "
            "asked to change an existing app, its code is NOT here. Confirm where "
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
                line += f", start: `{cmd}`"
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
                tail = f". {r['how_to_obtain']}" if r.get("how_to_obtain") else ""
                env_lines.append(f"  - {r['key']} [{mark}{', required' if r.get('required') else ''}]"
                                 f" {r.get('description','')}{tail}")
            miss = ov.get("missing_required") or []
            if miss:
                env_lines.append("  Missing required (the app won't start until set): " + ", ".join(miss))
            env_lines.append(
                "To request a NEW key from the operator: add an entry to "
                "`env_requirements` in workshop.app.json (key, description, required, "
                "secret, how_to_obtain). It appears in the Environment panel for them "
                "to fill. NEVER hardcode, echo, or print secret VALUES; you can see "
                "which keys are set, never their values."
            )
            lines.append("\n".join(env_lines))
    except Exception:
        pass

    lines.append(
        "PLATFORM OWNS THE RUNTIME: do NOT pick ports or hand-start/stop the dev "
        "servers. The app runner allocates ports centrally, spawns each service, "
        "and auto-restarts crashes.\n"
        "APPLYING A CHANGE (do this when a change is FINISHED; the "
        "operator wants the running app updated): a Vite/dev-server service "
        "hot-reloads on save (nothing to do); for a backend service that does NOT "
        "hot-reload, make it live by restarting just that service: "
        f"`bash scripts/restart_app.sh {project_slug} <service>` (re-spawns it on "
        "your new code, in a separate process). Don't leave the running app on "
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


def ambient_digest(provider: "ContextProvider") -> str:
    """Stage 7 (2e): a compact "ongoing work across the workspace" block for a
    chat's cache-stable prefix. A project/build/agent chat sees ITS OWN scope; a
    general chat sees a workspace-wide roll-up. Scope-correct, not a permission
    wall (single-operator). Empty string when there is nothing to inject. Same
    best-effort, self-contained shape as learnings_context so a missing store
    never breaks the chat path."""
    try:
        from services import workspace_digest
        body = workspace_digest.digest_for_provider(provider)
    except Exception:
        return ""
    if not body:
        return ""
    return "=== ONGOING WORK (ambient, across chats) ===\n" + body


# ---------------------------------------------------------------- base / general


class ContextProvider:
    key = "general"
    # Named for its SCOPE, not as a character. This entry is the workshop-scope
    # chat, which is what the Chats page and the task scope labels have always
    # called it; the picker calling the same thing "Clawd" was the app
    # disagreeing with itself. Agents are participants in a chat (entity model
    # 2026-07-18), so the top-level chat is a place, not a persona.
    label = "Workshop"
    # Per-provider tool policy for the chat surface.
    #   allowed_tools : list of MCP tool names ("mcp__plaid__search_transactions")
    #                   the model may call. Empty = tools fully off (single-turn
    #                   text Q&A; the call_cloud path pins max_turns=1).
    #   mcp_servers   : which MCP server specs to spawn in the chat subprocess.
    #                   Must be a subset of llm_router._MCP_SERVER_SPECS keys.
    # There is no per-provider step budget: a chat turn runs until it is done.
    # Defaults below are deliberately empty — `general` is text-only because
    # cross-cutting chat without a clear data domain doesn't justify the surface.
    allowed_tools: list[str] = []
    mcp_servers: list[str] = []
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
            "You are the operator's personal assistant and work orchestrator "
            "inside the Workshop app. "
            f"The operator is {operator_name()}, a senior engineer who dislikes padding. Be direct "
            "and brief. Use short paragraphs or tight bullet lists. No emojis. Never use em-dashes.\n\n"
            "Work placement. Reason from these principles, not rules:\n\n"
            "1. Do small things now. The operator asked you, not a queue. A read, an answer, "
            "a single-file edit while they are watching: just do it. You have filesystem "
            "tools (Read, Edit, Write, Bash, Grep, Glob) and can use them.\n"
            "2. Hand off when the work needs what chat cannot give it. The queue provides the "
            "reviewer gate, touches enforcement, attempts/lease/retry, a receipt, and metering. "
            "Multi-file writes and anything needing verification you cannot do in one pass "
            "belong there, not because they are 'big' but because unreviewed multi-file "
            "writes are how a tree gets quietly broken. File a task-proposal card and specify "
            "it well: objective, what done looks like, start-from files, boundaries.\n"
            "3. Offload reading that would crowd out the conversation. A question needing a lot "
            "of context is a subagent task, not necessarily a ticket. The subagent reads; the "
            "answer comes back into chat.\n"
            "4. The operator's expressed wish outranks all of this. If they say do it inline, "
            "do it inline. When they have said what they want, stop optimizing and do it.\n"
            "5. Say what you did or handed off, in one line. "
            '"Filed as a ticket: touches four files and wants a review gate." Never hand off silently.\n\n'
            "When you scope a task for the queue, SPECIFY IT WELL: give the objective, what "
            "done looks like, the files or commands to start from, and explicit boundaries, "
            "so a worker who never saw this chat can act. A weak spec is the top cause of a "
            "failed run. Use `mcp__workshop__query` to answer questions about ongoing tasks, "
            "jobs, and notes, and to report and interpret how a job or ticket turned out when "
            "asked. When the operator states that one thing matters more than another, set or "
            "adjust ticket priority through the task-patch block below.\n\n"
            "Below each turn you may be given live snapshots from the operator's installed apps "
            "(accounts, budget, location, commute, etc.). Read what's given to you before "
            "answering, and DON'T make up numbers when real ones are right there.\n\n"
            + TASK_PROPOSAL_CARD + "\n\n" + TASK_PATCH_PROTOCOL
            + "\n\n" + LEARNING_NOTE_PROTOCOL
            + "\n\n" + NOTE_PROTOCOL
            + "\n\n" + git_worktree_rule()
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
    role = "builder"
    # Builders default to a strong model for codegen quality; opus is selectable.
    default_model = _llm.TIER_SONNET

    def __init__(self, slug: str):
        self.slug = slug
        self.key = f"project:{slug}"
        # Honor the registry's `dir` (the self/platform project points at the
        # monorepo root, not projects/<slug>); fall back to the conventional path.
        #
        # The registry is read through routes.projects.REGISTRY — the same
        # accessor _project_slugs() uses — because this looked it up at
        # `<projects_root>/../projects.json`, which is not where it lives
        # (`.runtime/projects.json`). Inside a bare except, that missing file
        # meant the branch NEVER fired: the self project silently resolved to
        # `projects/workshop`, a stub holding nothing but `knowledge/`, so a
        # chat in the workshop project scope ran its tools against the wrong
        # tree, and `is_self` was False everywhere. A path spelled twice is a
        # path that will be spelled wrong in one of them.
        self.dir = _projects_root() / slug
        self.is_self = False
        try:
            from routes.projects import REGISTRY as _reg
            rec = (json.loads(_reg.read_text()).get("projects") or {}).get(slug) if _reg.exists() else None
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
            f"project directory ({self.dir}). The operator is {operator_name()}, a "
            "senior engineer who dislikes padding. Be direct and brief. Never use em-dashes.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current "
            "goal and constraints, then act. Task state lives in the structured "
            "store, NOT in TODO.md (which is a generated export, never edit it "
            "directly). To mark a task done, add a follow-up, or block one, run "
            "`python3 ~/workshop/scripts/project_tasks.py <verb> .` via Bash "
            "(verbs: set-status <id> done, add \"text\", block <id> \"reason\", "
            "note <id> \"text\", next-ready). When the operator gives a non-trivial "
            "direction, append a one-line decision note to PROJECT.md.\n\n"
            + TASK_PROPOSAL_CARD + "\n\n" + TASK_PROPOSAL_BUILD_RULE + "\n\n"
            + LEARNING_NOTE_PROTOCOL + "\n\n" + NOTE_PROTOCOL + "\n\n"
            + git_worktree_rule() + "\n\n"
            + f"Project-local agent rules:\n\n{rules or '(none)'}"
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


# ---------------------------------------------------------------- development provider
class DevelopmentContextProvider(ProjectContextProvider):
    """The Workshop's own source, as a top-level chat scope. ADR 0014.

    Behaves exactly like a project build chat, because that is what working on
    the platform is: full agentic toolset, cwd pinned to this checkout. The one
    difference is the KEY. It is `development`, not `project:<slug>`, so the
    picker groups it above Workshop instead of burying the platform repo among
    the projects.

    That key is the whole reason this class exists. Phase 3 renamed the
    `self: true` registry row to `development` and deleted the four other
    aliases, but the chat layer still built it through ProjectContextProvider,
    which stamps `project:` on the front. So the scope resolved as
    `project:development`, `scopeKind` read it as an ordinary project, and the
    Development group in the picker had a heading and no rows: developer mode
    was on everywhere and still invisible.

    Present only when developer mode is on, so a normal deployment does not
    offer a chat whose cwd is the application's own source.
    """

    label = "Development"

    def __init__(self):
        super().__init__(_self_project_slug() or "development")
        self.key = "development"


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
            f"{operator_name()}, a senior engineer who dislikes padding. Be direct and brief. Never use em-dashes.\n\n"
            "Always read PROJECT.md FIRST to ground your work in the current goal and "
            "constraints, then act. Task state lives in the structured store, NOT in "
            "TODO.md (a generated export, never edit it directly). Mark tasks done / add "
            "follow-ups / block via `python3 ~/workshop/scripts/project_tasks.py <verb> .` "
            "over Bash (set-status <id> done, add \"text\", block <id> \"reason\", "
            "note <id> \"text\", next-ready). When the operator gives a non-trivial "
            "direction, append a one-line decision note to PROJECT.md.\n\n"
            + TASK_PROPOSAL_CARD + "\n\n" + TASK_PROPOSAL_BUILD_RULE + "\n\n"
            + LEARNING_NOTE_PROTOCOL + "\n\n" + NOTE_PROTOCOL + "\n\n"
            + git_worktree_rule() + "\n\n"
            + f"Build-agent rules:\n\n{rules or '(none)'}"
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
            f"operator is {operator_name()}, a senior engineer who dislikes padding. "
            "Be direct and brief. Use short paragraphs or tight bullet lists. No emojis. Never use em-dashes."
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
_general = ContextProvider()
# Stage 7 (2e): the general chat gets a scoped, read-only workshop-query tool
# (platform MCP server, always available) so it can answer "what is going on /
# what tasks and notes exist" on demand from the task + knowledge digest. It is
# NOT a filesystem grant and never exposes `.runtime` secrets.
_general.allowed_tools = ["mcp__workshop__query", "Read", "Grep", "Glob", "Edit", "Write", "Bash"]
_general.mcp_servers = ["workshop"]

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
    # The platform repo's own scope, developer mode only (ADR 0014). Added here
    # rather than in _PLATFORM_PROVIDERS because that map is a module-level
    # constant and the flag has to be read per call: a constant would freeze
    # whatever the environment said at import.
    from services import app_runner
    if app_runner.dev_mode_on():
        try:
            dev = DevelopmentContextProvider()
            providers[dev.key] = dev
        except Exception as e:
            log.warning("development chat provider build failed: %s", e)
    for factory in app_registry.installed_chat_providers():
        try:
            p = factory()
            providers[p.key] = p
        except Exception as e:  # a broken persona must not sink the whole map
            log.warning("chat provider build failed: %s", e)
    return providers


def _self_project_slug() -> str | None:
    """The registry row marked `self: true`, the platform repo's own row.

    Phase 3 of the dev-mode design renamed it to `development` and deleted the
    other four aliases, so exactly one row points at this checkout. Read by slug
    rather than assumed, because the name is data and this must not break if it
    is renamed again."""
    from routes.projects import REGISTRY as _reg
    if not _reg.exists():
        return None
    try:
        data = json.loads(_reg.read_text())
        for slug, rec in (data.get("projects") or {}).items():
            if rec.get("self"):
                return slug
    except Exception:
        pass
    return None


def _project_slugs() -> list[str]:
    """Project slugs from the registry (per-instance, .runtime/), WITHOUT the
    self row.

    The platform repo is not a project in the picker. It is the top-level
    `development` scope, and listing it here as well would put it under Projects
    too: two rows, two names, one tree. That is the multiplication ADR 0014
    exists to stop."""
    from routes.projects import REGISTRY as _reg
    if not _reg.exists():
        return []
    try:
        data = json.loads(_reg.read_text())
        self_slug = _self_project_slug()
        return sorted(s for s in (data.get("projects") or {}).keys() if s != self_slug)
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

# The CLI's subagent tool, granted on the same footing as web. Every chat is
# told (chat_mode_block, DELEGATION_RULE) that it can push a big read into an
# isolated context and bring back the summary — and until this was granted, a
# data chat had no such tool and the instruction was simply false. Verified
# against CLI 2.1.143: `--tools Agent` enables it, the emitted tool_use name is
# `Agent`, and `Task` is the older accepted spelling.
SUBAGENT_TOOL = "Agent"


def effective_allowed_tools(provider: ContextProvider) -> list[str]:
    """A data provider's declared MCP tools + the shared web tools + the subagent
    tool, de-duped and order-stable. This is the SINGLE place that grants the
    shared surface, so the route and capability_for can't drift. Not used for
    build providers (full toolset)."""
    return list(dict.fromkeys([*provider.allowed_tools, *WEB_TOOLS, SUBAGENT_TOOL]))


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
        return True, ("tools: " + ", ".join(effective_allowed_tools(provider)))
    return False, ""


def dev_scope_refused(context_key: str | None) -> bool:
    """Is this a developer-mode scope asked for while the flag is off?

    Exists because `get_provider` below CANNOT refuse. It is total by contract:
    24 call sites treat it as always returning something, and several are on
    paths where an exception would be a 500 rather than a refusal. So the
    honest split is that get_provider degrades and this predicate lets the
    operator-facing seams say so out loud."""
    if context_key != "development":
        return False
    from services import app_runner
    return not app_runner.dev_mode_on()


def get_provider(context_key: str | None) -> ContextProvider:
    if context_key == "development":
        from services import app_runner
        if app_runner.dev_mode_on():
            return DevelopmentContextProvider()
        # FALLS THROUGH TO `general` when the flag is off, and that is a
        # DOWNGRADE, not a refusal. This comment used to claim the opposite,
        # which was worse than the behaviour: a chat bound to the platform repo
        # quietly became a tool-less general chat and nothing said so.
        #
        # Degrading here is deliberate (see dev_scope_refused above for why it
        # cannot raise), so the refusal belongs at the seams the operator can
        # actually see. `routes/chat.py` checks dev_scope_refused before
        # starting a turn, which is the moment the downgrade would otherwise
        # become invisible.
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
    pickable models, the default model, whether this provider actually gets
    tools in the current run config, and each model's turn capabilities.

    `model_caps` rides along here rather than on its own endpoint because the
    composer already makes this one fetch to populate the model picker, and the
    effort ladder has to change in the same paint as the model that determines
    its length."""
    from routes.chat import ENABLE_CHAT_TOOLS
    from services import chat_store, model_caps

    tools_active, _summary = capability_for(provider, enable_chat_tools=ENABLE_CHAT_TOOLS)
    # The OPERATOR's defaults, folded into the declared defaults here. The
    # composer shows the level and mode that will actually run when nothing is
    # picked, and since the resolution chain consults these prefs below the
    # thread (routes.chat._resolve_effort), they are part of that answer. Reading
    # them here rather than in model_caps keeps the caps table a pure statement
    # about vendors and models.
    pref_effort = chat_store.get_default_effort()
    pref_mode = chat_store.get_default_permission_mode()
    return {
        "key": provider.key,
        "label": label if label is not None else provider.label,
        "role": provider.role,
        "models": list(PICKABLE_MODELS),
        "default_model": provider.default_model,
        "tools_active": tools_active,
        "model_caps": {m: model_caps.caps_for(m, pref_effort) for m in PICKABLE_MODELS},
        # Vendor-level, so it sits beside the models rather than inside each one:
        # a permission mode is a property of the CLI running the turn, not of the
        # model answering it.
        "modes": model_caps.modes_payload(pref_mode),
    }


def list_providers(scope: str | None = None) -> list[dict]:
    """Provider directory for the composer/picker.

    scope=None  -> the FULL list (static data providers + every discovered
                   project + build-agent provider). The Agents page needs this.
    scope=<key> -> ONLY the single matching provider entry (an in-app dock
                   passes its own contextKey and must not see unrelated
                   projects/build agents). Returns [] if the key resolves to
                   nothing valid.

    Each entry: {key, label, role, models, default_model, tools_active,
    model_caps}.
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
