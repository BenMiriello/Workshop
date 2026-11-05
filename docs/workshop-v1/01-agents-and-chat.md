# 01 — Agents and Chat

Status: **draft for operator review** · Date: 2026-06-14

Companion to [00-overview.md](00-overview.md). Covers the two agent roles, their
permissions, and the single shared chat component that both use. This is Phase 1,
the operator's top-priority area ("the chat is the most disappointing part").

---

## 1. Two roles, inferred from context

| | Assistant (was "data agent") | Builder (was "build agent") |
|---|---|---|
| Scope | An app or a data domain | A Project (and a branch within it) |
| Tools | None, or a narrow allowlist; data mutations gated | Full Claude Code toolset |
| Memory | Letta persona + memory blocks | Claude Code session, project context each turn |
| Where it lives | In-app ChatDock | Workbench builder page |
| Created by | Attaching to an app (persona + settings) | Starting work on a project |

The user never picks "data vs build" as an abstract type. The **context picks the
role**: opening chat inside an app gives an Assistant scoped to that app; opening
chat in a project gives a Builder for that project. The creation flows reflect this
(see §6).

## 2. Permissions and gated mutation (Assistants)

Assistants must be safe to let touch data. The model, reusing the capability grants
from app-platform-plan.md rather than inventing new machinery:

- An assistant's reachable surface = its app's capabilities + whatever `grants.json`
  allows. It can never reach another app's data without an admin-configured grant.
- Each mutating action has a policy: **read-only** (default), **confirm** (assistant
  proposes, user confirms in chat with a ConfirmDialog-style inline action), or
  **allow** (executes directly). Policy is admin-configured per app/per capability.
- The budget assistant's existing "plan-patch" propose-then-apply flow is the
  prototype for "confirm": generalize it into a typed **proposed-action** the chat
  renders as an inline confirm card, not an app-specific hack.

This is a correctness boundary on the sharing path, not single-user cruft: for the
operator's own private apps the default can be "allow" so nothing nags.

## 3. Capability-aware system prompting (fixes the "promised tools it did not have" bug)

Today an assistant with tools disabled still tells the user it will go do things,
because its prompt does not state its own capabilities. Every agent prompt is built
from three layers:

1. **Role preamble (platform-owned).** States the role, the exact tools available
   right now, and what it must not promise. For a tools-off assistant: "You cannot
   take actions or edit data in this session. Do not say you will do something;
   instead tell the user what they would need to do, or offer to draft it." For a
   builder: the real toolset and the working directory, plus the environment's
   constraints (see §4).
2. **App/project context (platform-owned).** Live snapshot (today's PROJECT.md /
   TODO.md / app state) injected each turn.
3. **Custom system prompt (user-owned).** Free text the operator sets per
   assistant/builder. Appended last, never overrides the safety facts in layer 1.

The role preamble is generated from the same capability data that actually
configures the run, so it can never drift from reality.

## 4. Environment constraints surfaced to builders (fixes the egress confusion)

Builders run in a box with a network-egress filter: external `git clone` /
`npm install` to hosts like github.com and registry.npmjs.org are blocked at the
box level (the the bridge agent egress-guard hook plus box network policy), while loopback and
the tailnet are allowed. A builder that does not know this promises a clone and then
fails opaquely (what happened to `<app>-builder-1`).

Fix: the builder role preamble includes the egress facts ("external network is
blocked for git/npm/curl; loopback and tailnet are allowed; if a task needs an
external fetch, surface it and ask the operator to run it in their terminal"). The
builder page also shows a one-line environment banner when a command fails on
egress, rather than a raw stderr dump. Whether to instead allow git/npm to an
external host allowlist is decision #3 in the overview.

## 5. The shared chat component (the rebuild)

One component, `Chat`, used by ChatDock (in-app) and the focused/builder views.
Concrete requirements, each tied to a stated complaint:

### 5.1 Model picker
Add an AI **model selector** (Opus 4.8 / Sonnet 4.6 / Haiku 4.5; ids in the API
reference). Backend `llm_router` already routes by model id; expose the choice in the
composer as a small inline control, default per role (builders default to a strong
model, assistants to a fast one), remembered per thread.

### 5.2 Mobile
- Inputs use `font-size: 16px` minimum to disable iOS auto-zoom (verify the textarea,
  not just the send button, meets this).
- Composer is a single aligned row: textarea grows, send/stop/expand are equal-height
  icon buttons sharing one control height token. No 1px padding drift.
- The dock as a bottom sheet must keep the composer above the keyboard and within the
  safe area; test at 360px width.

### 5.3 Floating open button
Replace the labeled pill (icon + agent name) with a **small circular FAB**: just a
message-bubble icon, smaller than today. It must stay on-screen on mobile (respect
`env(safe-area-inset-bottom)`; it currently sits too low and clips).

### 5.4 Conversation auto-naming
Stop using the first N characters. After the first exchange, generate a short title
from the conversation (a cheap model call, like Claude/ChatGPT do). Title is editable.

### 5.5 Delete confirmation
Deleting a thread uses `ConfirmDialog` (danger), never an immediate destructive click.

### 5.6 Agent / thread picker scoping
The picker shows only agents **relevant to the current context**: an app's chat shows
that app's assistant(s), not every unrelated builder. The full cross-context list
lives in the Agents page, not in an app dock.

### 5.7 Task list in chat
Each agent has its **own task list**, shown in the chat (a collapsible panel in the
chat, not only a free-floating widget). Tasks can be created manually or managed by
the agent. A separate, optional free-floating general task list is fine and is the
project roll-up (see [02-workbench-and-launcher.md](02-workbench-and-launcher.md) §3).

### 5.8 Expand, not pop
Keep the existing "expand chat" affordance. Remove "pop chat" (duplicate, confusing).

## 6. Creation flows (stop asking for names/slugs/purpose)

### New builder
"New builder" does not ask for a name, slug, or purpose. It asks **which project**
(or "new project"), then starts a builder in that project. The agent is auto-named
(`<project>-builder-N`, or an AI-generated descriptive name once it has a task).
Purpose is optional and can be derived from the first task.

### New assistant
Assistants keep a light config (persona, custom prompt, tool policy) because that is
their substance, but they are created **attached to an app**, not free-floating with
a hand-typed slug. Name auto-suggested from the app + persona, editable.

## 7. The Agents page status row (redesign)

Today: "6 data agents idle / Background loop off / [Enable] / 2 build sessions live /
4 idle" with a green dot that contradicts "idle" and an orphan Pause button.

Redesign into a clear at-a-glance strip with three labeled segments:

- **Builders**: "2 live · 4 idle". Dot is `running` only when at least one is live.
- **Assistants**: a count (assistants are request/response; "idle" is not meaningful,
  so show count, not a live/idle split).
- **Autonomy**: the background loop state with one labeled toggle ("Autonomy: Off")
  and, only while running, a Pause control folded into the same segment. The bare
  Enable/Pause buttons go away; there is one clearly-labeled autonomy control.

Each segment links to the relevant detail. No status without a clear referent; no
control without a clear effect.
