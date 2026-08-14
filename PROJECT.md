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
- 2026-08-12 — Operator: the Tickets surface is over-built, and past passes added
  rather than refined. Direction: make it a normal ticket manager and delete the
  leftovers, not just hide them. Shipped: four tabs collapsed to one list (Runs
  moved to /runs, Review and Inbox deleted as surfaces), controls cut 9 to 4,
  ticket page cut from ten sections to the standard main-column-plus-rail, and
  every field with no data in the live store removed along with its backend
  route. Deletions were chosen by measuring field occupancy across all 179
  tickets, not by judgement. Net -2431 lines.
- 2026-08-13 — Operator: enact the tickets/autonomy plan in full. Built phases 0
  to 4 of `docs/platform-redesign/autonomy-implementation-2026-08-13.md`. Two
  schema fields carry it: `execution` (code|judgement|external|null, fail-closed)
  and `triage` (a content hash over the spec fields only, NOT `updated`, which is
  what lets a queue triager terminate). `next_ready(unattended=True)` adds
  execution+readiness for the sweep and for nothing else, so readiness still
  never vetoes a human. Measured after: the human head of `development` is still
  the root-privileged SEC-C3, the unattended head is real code work.
  Phase 0 deleted the dead controls and split the global brake from the runner
  (the Start button used to clear the brake and start nothing). Phase 1
  classified all 25 open tickets, re-filed five platform tickets out of the
  `workshop` scope, put readiness on the page and on list rows, and gave the
  worker and agents the search tool. Phase 4 made a supervised sweep land
  `in_review`, a stuck run park instead of fail, and set the per-scope budget
  ceiling. NOT turned on: the crontab runner line stays commented (operator's
  call) and the queue triager is manual, dry-run by default, as the plan asks.
- 2026-08-13 — Operator: applying a change (restart backend / frontend / rebuild
  the public bundle) is a re-derived ordeal every time and agents often decide it
  is unsafe and skip it. Investigated; plan at
  `docs/applying-changes-plan-2026-08-13.md`. Root cause is not timidity: the
  deferred backend restart re-invokes itself WITHOUT `--force`
  (`restart_backend.sh:129`), hits its own live-turn guard, and exits 2 after
  reporting success (measured 61 restarts, 14 silent refusals). Separately the
  runtime briefing at `chat_context.py:934-948` tells every in-app agent frontend
  changes need no build, contradicting the settled 2026-08-11 public-origin
  decision. Instructions are spread over ~30 files with six live contradictions.
  Plan: fix the restart lie, add one drift-based idempotent `scripts/apply.sh`,
  collapse instructions to one canonical doc, surface an apply receipt. NOT
  implemented; awaiting operator approval.
- 2026-08-13 (2) — Operator: allow agents to restart, and make a restart only
  ever pause or delay rather than break; decide the open questions yourself.
  Built and tested. `bash scripts/apply.sh` is now the ONE command an agent runs
  when it finishes: drift-based (compares what is running to what is on disk,
  per surface), no arguments, idempotent, safe to run concurrently. The backend
  and the bundle each report their own source id (`GET /healthz`,
  `frontend/dist/build-info.json`) so "did my change land" is answerable instead
  of assumed. Resilience: a restart no longer kills the detached agent process
  (the lifespan sets `build_streaming.SHUTTING_DOWN` before asyncio cancels the
  drive loop, which used to SIGTERM the child and write "this turn ended early"),
  no longer duplicates a reply committed in the restart window
  (`_already_committed` reads the thread, not the journal), and no longer hangs
  90s on an idle browser tab (SSE generators release on shutdown, plus
  `--timeout-graceful-shutdown 10`). Instructions collapsed from ~30 files with
  six contradictions to `docs/applying-changes.md` plus three pointers; the
  gengui never-restart rule is reversed; the stale-bundle warning is now a hard
  gate when the diff touches `frontend/` or `packages/ui/`. Also fixed:
  `ui_source_id.sh` never hashed `packages/ui`, so kit edits were invisible to
  every staleness check despite compiling into the bundle.
