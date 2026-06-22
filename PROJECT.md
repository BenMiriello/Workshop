# Project — Agents & Chat overhaul

Goal: make the Agents tab and the Chat interface correct, consistent, and complete.
Driven by operator review (2026-06-22). Work the plan in `TODO.md` under
"## Agents & Chat overhaul".

## Constraints
- Single-operator system. No multi-tenant guardrails (see CLAUDE.md).
- All `frontend/` work obeys `frontend/CLAUDE.md` (Workshop Design System): `src/ui`
  primitives, tokens only (no inline hex), lucide icons, four list states.
- No committed stubs. A task too big for one tick → `[~] BLOCKED` + propose sub-tasks.

## Decisions (operator review 2026-06-22)
- "Data" agents are renamed "Assistant" everywhere user-facing. Internal `kind:"data"`
  token stays (Letta wiring) — UI-label rename only, low blast radius.
- Autonomy concept is KEPT — it gates the Wave-1 cron Ralph loop via a flag file
  (`backend/routes/autonomy.py`). The bug was the frontend hitting a dead
  `/api/autonomy`; fixed to `/api/agents/autonomy/{enable,disable}`. SHIPPED as a
  single On/Off `Toggle` (not tri-state). The separate supervisor pause/resume
  control was removed — it conflated two mechanisms and muddied the switch.
- Agent cards: one categorical hue per KIND (assistant vs build), not per-id hashing.
- Build-agent chat (`AgentBuild`) keeps ONE expand control (the RegionHead full-width
  toggle). The in-chat pop-out is suppressed when the host provides its own expand.
- Focused full chat (`/chat/:key`) drops the global SectionNav and slims the topnav —
  the chat owns its chrome (Back + compact toolbar).

## Phase 2 — chat features
Shipped this tick:
- **Timeout handling.** Build turns hit a 900s wall-clock limit; on timeout the
  proc is killed and the bubble used to collapse to the opaque
  "(model exited without text)". Now `build_streaming` distinguishes timeout vs
  clean-no-text vs error and writes a plain message ("stopped at the N-minute
  limit — files saved, send a follow-up to continue"), tags `meta.timeout`, and
  the frontend renders it as a calm warn note (Clock tag, `--warn` tint) instead
  of a red error. Limit unchanged — only the handling.
- **Overlap handling (minimal, no overhaul).** The real overlap surface was the
  Letta TODO/PROJECT tools (`letta_tools/workshop.py`) doing unlocked
  read-modify-write with no dedupe — that's why TODO.md filled with near-identical
  overseer lines. Added an inline `flock` advisory lock (single-flight via
  `.runtime/TODO.lock`) around each writer, and normalized dedupe in
  `wkshp_propose_task` so identical proposals are skipped. Cleaned the existing
  vacuous-duplicate pile out of TODO.md.
- **Slash commands.** Composer command registry + "/"-triggered autocomplete
  popover (arrow/enter/tab/esc) + a slash button. Wired to actions that work
  today: `/new`, `/delete`, `/tasks`, `/expand`, `/help`. `/compact` + `/rollback`
  land with their backends below (not stubbed).

Still to build (each needs more backend; no stubs):
- Per-chat to-do/done list surfaced from the task store and prompted into the agent.
- Context view + compaction (summarize older turns; show context size) → `/compact`.
- Conversation rollback (message ids + truncate endpoint + "revert to here") → `/rollback`.

## Overlap / concurrency note
Two writers touched shared docs at once earlier (the overseer planner + a builder),
not two coders. The fix is scoped to the high-frequency automated writers (flock +
dedupe on the Letta doc tools); a builder's freehand Edit is rare and operator-driven,
so no broad orchestration lock was added (avoids cruft per CLAUDE.md).

## Log
- 2026-06-22 — Phase 1 SHIPPED (Workshop builder 2): Agents tab — autonomy toggle
  (dead endpoint fixed), per-kind card colors, merged recency sort + activity
  timestamps (backend `last_active` added for data agents), uniform card heights,
  new-builder modal synced with the in-project spawn (editable name/slug/purpose),
  data→assistant UI rename. Chat — full-route topnav slimmed (toolbar height fix),
  dual-expand consolidated via `Chat hideExpand`. tsc clean, design clean, backend
  reloaded. Phase 2 (slash commands, per-chat tasks, context/compaction, rollback)
  not yet built.
- 2026-06-22 — Operator: handle interrupted/derailed turns gracefully (don't "fall flat").
  Captured as a TODO to design checkpoint/resume from PROJECT.md + in-progress notes.
