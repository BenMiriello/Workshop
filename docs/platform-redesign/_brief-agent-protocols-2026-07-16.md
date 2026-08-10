# Shared brief: agent interaction protocols + notes contracts (2026-07-16)

> **STATUS: HISTORICAL.** This was the stage-setting brief handed to the research
> agents on 2026-07-16. Superseded for reading purposes by
> `synthesis-agent-protocols-2026-07-16.md` and `concept-schema-2026-07-16.md`.
> All resulting work is PROPOSED, NOT BUILT.

INTERNAL STAGE-SETTING DOC for the research/design agents working this thread.
Every file:line below was read by the orchestrator on 2026-07-16, not assumed.
Verify before you extend, but do NOT re-derive from scratch.

**READ FIRST**: `docs/platform-redesign/operator-asks-2026-07-16.md` holds the operator's
ask VERBATIM plus a stable index of ask IDs (A1-A12, B1-B4, C1-C4, D1-D6, E1-E5, F1-F6)
and a section on ideas-that-are-not-requirements. Where that doc and this paraphrase
disagree about what was asked, that doc wins. Cite ask IDs in your report so the
coverage audit in its Part 4 can be completed.

## What the operator asked for (verbatim intent, paraphrased tightly)

1. **Notes app gets an interface contract with outside agents.** A mix of *hard gate
   API scoping* and *AI interface scoping*. Different folders carry different
   permission and access protocols and rules.
2. **0 or multiple agents live within Notes** — on-call or autonomous, write-only,
   read-only, other.
3. **Other apps/agents request notes** by search, name, or other. In some configs a
   note or folder has **mixed permission**: no access unless an LLM reviews the
   request and compares it against a strict allowlist of topics/scope. **Must be a
   smart LLM. If none available, no access** (fail-closed).
4. **Response modes** on a mediated grant: verbatim, filter to only relevant info,
   redact, or other.
5. **Generalize.** Notes is a custom app we do not want to require anyone to use, so
   these protocols must be workshop-general primitives, not notes-specific. Decide
   explicitly which parts are general protocol and which stay in the app.
6. **Blocked-by-permission escalation.** An agent or job must be able to flag that it
   is blocked (by permission or anything), and the operator reviews and changes
   access / approves the request / updates rules. Rules may be **literal programmatic
   rules OR textual rules**.
7. **Task/ticket scopedness is a setting, not a constant.** Some work you specify
   requirements+goals and let it figure things out; other times you are hands-on.
   Skills and rules exist to make work well-scoped. Settings at **project level**, and
   agent/job level **inheriting from project**.
8. **Notes get picked up.** Leave a note in general or in a project and it is acted on.
   Simple general worker autonomous job-template agents, configurable/editable per
   project, with a **default set + templates** including different groups of these.
   One such worker reviews new/updated notes, ensures all notes are addressed, makes
   tickets, or escalates. "Like ants having little jobs."
9. **What are the other primitive roles/agents?** Research it, distill to a core set
   and a system for interaction.
10. **Conductor/overseer agents**, differing scopes, and integration with the broader
    design.

Deliverable: a few reports. Propose not just what the operator proposed but what it
implies and what the rest of the system needs, including full functionality, UI, and
condition handling.

## Verified current state (read on 2026-07-16)

### The Notes substrate is ALREADY the scoped knowledge store — and already a Store

- ADR `docs/decisions/0002-notes-substrate-is-knowledge.md` (status: accepted) locks
  this: `services/knowledge.py` IS the canonical Notes substrate. Do not build a new
  note store. Notes are scoped and retrieved, never concatenated.
- Scopes and roots (`backend/services/knowledge.py:92-111`): `workshop` ->
  `shared/knowledge/`, `project:<slug>` -> `projects/<slug>/knowledge/`,
  `app:<name>` -> `apps/<name>/knowledge/`, `agent:<id>` ->
  `.runtime/agents/<id>/knowledge/`. Per-note `<id>.md` + a `MEMORY.md` index.
- **Load-bearing**: `knowledge.register_knowledge_stores()` already registers each
  knowledge scope as a platform Store of **type "Note"** (`services/stores.py:1-25`
  docstring). So "a folder of notes" is ALREADY an addressable, grantable resource.
- Notes plan phases N0-N3 are SHIPPED (commits `3d7d405`, `4b25956`, `d0ccf8a`):
  `/notes` surface (`frontend/src/pages/Notes.tsx`), cross-scope
  `backend/routes/notes_all.py`, project/agent Notes repointed to knowledge, docs
  bridge at `/documents`, note-capture in the finalize path,
  `shared/skills/notes-and-docs/SKILL.md`. Plan:
  `docs/platform-redesign/notes-system-plan-2026-07-15.md`.
- A SEPARATE out-of-tree vault exists at `~/notes` (`services/notes_vault.py`, 411
  lines, routes prefix `/notes-app`, frontend `/apps/notes`). Per ADR 0002 it stays
  separate and is only linked from the Notes UI. Decide deliberately whether the
  interface contract covers it, the knowledge scopes, or both.

### The capability/grant primitive ALREADY EXISTS (this is the spine)

`backend/services/permissions.py` (733 lines) — "Grants + permissions + allowed-actions
framework (the authz source of truth)".

- `Store` = "a named, typed handle onto data an app owns", stable URI
  `workshop://store/<app>/<name>`, optional broker `type`, mandatory portable
  `export`. It is a REGISTRATION over existing on-disk data, never a relocation
  (`services/stores.py:1-55`, 240 lines).
- Resource-scoped grants (Phase 4) live in `resource-grants.json`, separate from the
  app-level `grants.json`. Verbs are `_VERBS = ("read", "write", "append")`
  (`permissions.py:56`).
- API: `authorize()` (`:323`), `mint_grant()` (`:616`), `revoke()` (`:680`), and a
  per-invocation `permissions.check(token, uri, verb)` that a provider calls.
- Routes (`backend/routes/grants.py`, 122 lines): `GET/PUT /grants`,
  `POST /grants/mint`, `POST /grants/revoke`, `GET /stores?type=` — "the Store
  catalog the broker picks from". **A broker is already named in the design.**
- Identity model: `Identity.kind` in {`operator`, `guest`}. Guest auth is an
  intentional unbuilt seam (`resolve_identity` defaults to operator).

**CRITICAL DESIGN POSTURE (read the module docstring, it is explicit):**
default-permissive for the operator. `authorize(operator, ...)` is ALWAYS "allow",
never "deny"; the sole exception is an opt-in "confirm" policy for UX. Gating exists
ONLY for the external/shared boundary (guests, `shareable`/`public` apps). This is the
operator hard rule "no defensive cruft for single-user setups" encoded in code.

=> The operator's ask is NOT a request to gate themselves. It is about the
**agent-to-agent / app-to-app boundary**. Any proposal that makes the operator ask
permission to read their own notes is WRONG and will be rejected. Frame mediated
access as a boundary for *non-operator callers* (agents acting autonomously, other
apps, future guests). Resolve this tension explicitly and early in your report:
is an autonomous agent an `operator` identity, a `guest`, or a THIRD identity kind?
That question is arguably the central design decision of this whole thread.

### Tasks already model blocked + scoping knobs

- `backend/services/tasks.py` (903 lines). `STATUSES = {"todo", "doing", "in_review",
  "blocked", "parked", "done", "cancelled"}` (`:61`), with a `blocked_reason` field
  (`:22`, `:252`, `:629-636`, `:737`). Markdown box mapping `!` -> blocked (`:338`).
  So "flag that I am blocked" has a substrate; what is missing is the *escalation
  loop and the operator review surface* on top of it.
- Autonomy PRESET model exists (`services/triggers.py:214`): modes include
  `off`/`manual`/`continuous` (`:26`), plus `event-driven` and `scheduled`
  (`services/event_dispatch.py:147-149`). `continuous` translates to a BOUNDED loop
  (`triggers.py:190-207`: bounded by max_iterations + budget + reviewer gate).
- Agent `role` is a free-form tag today (`services/agents_registry.py:65,160,374-460`),
  alongside `depth` and `fanout`. The role taxonomy is therefore UNCOMMITTED and is
  exactly where a distilled primitive-role core set can land.
- Per-task effort tiers, TaskPanel/AutonomyPanel/Job-hierarchy UI all shipped
  (`ae02a74`, `827ab59`, `ee2d937`, `1577e6c`).

### Prior art in-repo you MUST reconcile with, not duplicate

- `docs/platform-redesign/orchestration-and-autonomy.md` — the O1-O7 design
  (task substrate, emission, dispatcher, worker, memory-keeper, autonomy presets,
  root-queue cron). O1-O7 are all DONE.
- `docs/platform-redesign/unified-agent-model-2026-07-15.md` — the unified Agent
  primitive design, plus a deferred clawd/the bridge agent naming decision.
- `docs/platform-redesign/chat-tickets-orchestrator-plan-2026-07-15.md` — Task=ticket,
  Job=attempt (deliberately NOT merged).
- `docs/platform-redesign/notes-system-plan-2026-07-15.md` — the notes plan (shipped).
- `docs/platform-redesign/research-findings-2026-07-15.md` and
  `review-findings-2026-07-15.md` — existing research + a 20-finding adversarial
  review. **Read these before doing new research so you do not re-run it.**
- `docs/decisions/0001-task-store-is-canonical.md`, `0002-notes-substrate...`,
  `0003-github-is-per-project-binding.md` — accepted ADRs. Contradicting an accepted
  ADR requires proposing a superseding ADR, explicitly, with reasons.
- `docs/app-platform-plan.md` section 3 and `docs/workshop-v1/01-agents-and-chat.md`
  section 2 — the authz model permissions.py implements.

### Hard constraints (operator rules; violating these wastes the report)

- **No defensive cruft for single-user setups.** No allowlists/audit journals/
  multi-user permissions/rate-limiting/confirmation prompts *for the operator*. The
  operator has corrected this repeatedly. The topic-allowlist the operator DID ask
  for is scoped to the agent/app boundary; keep it there and say so.
- **No em-dashes** anywhere (docs, UI copy, code strings). Use comma/period/colon/parens.
- **No emoji in UI.** Lucide SVG icons only.
- Reusable primitives over app-specific ones (`src/ui`); one shared header + one
  section list per app nav.
- Stack: `claude -p` + bash + python + json. **No agent framework.** Ollama is kept as
  a service (relay/embeddings/redaction) and is the natural "is a smart LLM
  available?" fallback question. Note the operator's fail-closed requirement: a small
  local model is explicitly NOT sufficient to authorize mediated access.
- Backend restart only via `scripts/restart_backend.sh`. `agents/` is gitignored.
- Autonomy is LIVE (the AUTONOMY_DISABLED flag is gone); workshop scope is manual.

## Output rules for your report

- Write to the exact path assigned in your prompt, under
  `docs/platform-redesign/`. Markdown. No em-dashes. No emoji.
- Cite `file.py:line` for every claim about current code you make. If you did not
  read it, say "unverified" rather than asserting it.
- Distinguish sharply: **what exists** / **what we adopt from research** / **what we
  reject and why** / **what we propose to build**.
- Prefer a small core set of primitives over an exhaustive taxonomy. The operator
  asked to "distill to a core set", and the anti-cruft rule means every primitive you
  propose must earn its place. Name what you deliberately leave out.
- Propose, do not implement. No code edits outside your report file.
- Give sequenced phases with file-level touch points, mirroring the shape of
  `notes-system-plan-2026-07-15.md` (audit -> research adopt/reject -> design ->
  phases). That doc is the house style. Match it.
- Flag open questions for the operator explicitly at the end. Recommend a default for
  each rather than leaving it open.
