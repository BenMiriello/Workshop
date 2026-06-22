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

## Phase 2 (planned, not yet built — net-new chat subsystems)
Slash commands (registry + autocomplete + slash button), per-chat to-do/done list
surfaced from the task store and prompted into the agent, context/compaction UI,
conversation rollback (needs message ids + a truncate endpoint). Designed in the
TODO before building; no stubs land.

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
