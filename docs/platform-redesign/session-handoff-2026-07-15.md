# Coherent-rebuild handoff (2026-07-15)

READ THIS FIRST, then continue the build. The canonical plan is
`docs/platform-redesign/coherent-rebuild-2026-07-14.md` (staged migration 0-9,
subsystem designs in section 2). This doc is the live status + how to keep going.

## The operator's standing directive (do not violate)

The operator has said this repeatedly and emphatically. Follow it literally:

- **DO ALL THE WORK. Do not stop, defer, check back in, or ask for confirmation
  until the whole thing is fully done and tested.** No "should I continue?", no
  "here is what remains, want me to?", no pausing between stages to report. Just
  build, integrate, test, and only surface when everything is complete.
- **Use Opus subagents, building in phases.** Task subagents (Agent tool,
  `model: opus`) with self-contained instructions; run independent ones in
  parallel, sequence ones that share files; YOU integrate and test each wave
  before the next.
- **Ollama: do NOT delete the service.** Other things use it (the local relay
  tier, possibly embeddings). Leave `ollama serve` running. Move workshop
  AUTONOMY off Ollama tiers if that is the design (Stage 9), but keep the service
  alive.
- Do not re-litigate decisions already made. Do not narrate options you will not
  take. Act.

## The goal (north star)

One coherent system: a chat spawns well-scoped tickets that are managed,
synchronized, and orchestrated in the background; every chat can see ongoing
tasks/context (scoped); agents dedup and self-maintain the work; nothing gets
dropped when a chat scrolls away; autonomous work is metered so it never burns
the operator's Claude usage. Concept model (plan section 1): Project / Agent /
Chat / Task / Run.

## What is DONE and shipped (verified live)

All committed on `main` in `/home/clawd-a/workshop`. The pipeline was tested
end-to-end with real sweeps.

- **Stage 3 - migration core.** Root `self` scope un-protected; structured
  `.tasks.json` is canonical everywhere; legacy prose `TODO.md` archived to
  `docs/archive/root-todo-legacy-2026-07-15.md`; the legacy grep/sed worker-loop
  in `cron_agent.sh` retired (Phase 0 + Phase 2 gone; dispatcher + event-consumer
  kept). ONE task system, ONE loop. Commits `4b1c2ff`, `8daf027`, `fc350e4`,
  `6d814d8`.
- **Stage 5 - usage budget + enforcement.** `backend/services/usage_ledger.py`
  (rolling 5h window, `can_admit`, `record`); real token metering in `ralph.sh`
  (`--output-format json`, `--bill-project`); admission gates in
  `dispatch_triggers.sh` + `project_ralph.sh`. Config `.runtime/usage-budget.json`
  (`app_total` default 2,000,000 tokens/5h, tunable). Usage UI at `/usage` (route
  `GET /api/usage`). Verified: a real sweep bills ~250k tokens; an exhausted
  budget makes the dispatcher SKIP a ready scope. Commit `3de8b5d` + Stage 3
  commits.
- **Stage 4 - chats spawn tickets + naming.** `POST /api/chat/threads/{id}/
  spawn-task` distills a conversation (Haiku) into a self-contained task, files it
  into the scope queue (chat binding, else workshop root) with
  `source={kind:chat,ref:thread_id}`. "Turn into task" button in the chat toolbar.
  UI stopped saying "builder" (says agent / workbench). Commits `a7eb7ba`,
  `7a1163f`.
- **Stage 6 - self-healing.** Lease + fence token (`claim_token`) + reclaim on
  claim; `waiting`/`blocked`/`in_review` release the claim; `parked` dead-letter
  status (poison-pill tasks stop being re-picked); `scripts/liveness_watchdog.sh`
  (fails `running` Jobs with dead pids, reclaims stranded tasks); circuit breaker
  in the dispatcher (skip a scope after 3 consecutive failed sweeps, exponential
  cool-off). Commit `4f60f99`. Suggested cron `*/5 * * * * liveness_watchdog.sh`
  is a comment, NOT installed.
- **Stage 8 - learning + skills (backend).** `backend/services/learnings.py`
  (structured global + per-agent learnings, dedup by hash + Jaccard, feedback
  loop, skill distillation via `services.skills.put_skill`). `chat_context.
  learnings_context(provider)` injects global guardrails + scope memory into the
  chat prompt (cache-stable prefix). `scripts/log_learning.sh` now delegates to
  the store. Storage `.runtime/learnings/<scope>.jsonl`. Commit `41d726c`.

## What is IN FLIGHT right now

- **Stage 7 (cross-chat context + dedup)** is running as a background Opus
  subagent (launched ~05:44 PT 2026-07-15). It builds: ambient scoped digest of
  ongoing work injected into chat context; dedup on task creation (canonical hash
  + local birds `/embed` cosine: >=0.90 merge, 0.80-0.90 link related, <0.80
  distinct); a safe scoped read-only workshop-query MCP tool for general chat (NOT
  a raw filesystem grant). **Check `git log` for its commit** (message will start
  "Stage 7"); read the agent's result in the run journal if needed. If it did not
  finish or has gaps, re-task or finish it.

## What REMAINS (do these, in order, without stopping)

1. **Integrate + test Stage 7** once it lands (restart backend, run a demo-x
   `--force` sweep to confirm no regression, test dedup on the spawn-task path).
2. **Stage 9 - retire Letta, move autonomy off Ollama (KEEP the Ollama service).**
   Run a solo Opus subagent (it is sprawling and shares `chat_context.py` /
   `agents_registry` with Stage 7, so do it AFTER Stage 7 integrates). Remove: the
   Letta heartbeat supervisor, the Letta assistant-creation flow, Letta memory
   dependency (Stage 8's learnings store replaces it), and dead Letta routes/UI.
   Move workshop autonomy tiers off Ollama to `claude -p`. LEAVE `ollama serve`
   and the `/relay/local` path intact (other things use it). This was
   "needs operator confirm" in the plan; the operator has now confirmed the
   nuance (keep the service, move off it in the design).
3. **Small integration wiring still open:**
   - Inject learnings (`learnings.memory_block` / `chat_context.learnings_context`)
     into the AUTONOMOUS SWEEP prompt path (`ralph.sh` / `project_ralph.sh`), not
     just chats. Stage 8 flagged this as unwired.
   - Optionally route rejected-reviewer reasons to a per-agent learning scope
     (currently global).
4. **Final integration pass:** restart backend clean, `bash scripts/check_design.sh`
   + `cd frontend && npx tsc --noEmit` + `npm run build`, rebuild the public
   bundle, one real end-to-end sweep, confirm the Jobs/Usage/Agents UI is coherent.
5. **Re-enable autonomy:** remove `.runtime/AUTONOMY_DISABLED` (the pause flag was
   set for the multi-agent build). Confirm the budget cap is live first.
6. Update the task tracker and MEMORY; report ONCE when fully done.

## Operational state + gotchas

- **Autonomy is PAUSED** right now: `.runtime/AUTONOMY_DISABLED` exists (set for
  the safe multi-agent build). Live autonomy scripts can be edited safely while it
  is present. RE-ENABLE it (delete the flag) only at the very end.
- **Autonomy modes:** `demo-x` = continuous (throwaway canary, idles when its
  queue drains); `workshop` (the self project) = **manual**. Arming the workshop
  root to CONTINUOUS self-editing is **blocked by the safety classifier** (an
  unsupervised loop committing to the live, financial-data-adjacent platform).
  Do NOT try to work around that. Only the operator can flip it from the UI
  Autonomy panel. Manual mode still dispatches on demand and via `--force`.
- **Budget:** `.runtime/usage-budget.json`, `app_total` 2M tokens/5h. Chats are
  exempt from the ledger but DO consume the real shared Claude 5h quota, so keep
  `app_total` below the real limit. One autonomous task-run costs ~250k tokens.
- **You cannot drive the authed UI** (login wall; loopback trust removed). Verify
  backend via `py_compile`, direct python import tests (`from services import
  ownership; ownership.set_current_owner(1)`), and `curl` 401-vs-404. Verify
  frontend via `tsc` + `check_design.sh` + `npm run build`.
- **Concurrent WIP in the tree:** other agents' uncommitted work sits in the tree
  (voice files, Home.tsx, Projects.tsx, components.css, letta_tools, meta_reject,
  compact_todo, work_generator, heartbeat). NEVER `git add -A`. Stage ONLY your
  own files by explicit path. Commit messages end with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Hard rules (user + repo CLAUDE.md):** single-operator, NO defensive cruft
  (no allowlists/audit/rate-limiting/confirmation gates/multi-tenant guards). NO
  em-dashes anywhere (code strings, UI copy, comments). NO emoji in UI (lucide
  icons only). Frontend must follow `frontend/CLAUDE.md` (src/ui primitives,
  tokens not hex, five status kinds). Backend restart: `bash scripts/
  restart_backend.sh`. Python is `python3`; venv `backend/.venv`.
- **Subagent prompt recipe that worked this session:** point the agent at the
  plan section, name the exact seam files, give the shared-constraints block
  above, require a scratch (gitignored) verification test, and require it to
  commit only its own files. Opus, run in background, integrate its result
  yourself.

## Commits this session (newest first)

`4f60f99` Stage 6 self-healing; `41d726c` Stage 8 learnings; `3de8b5d` Stage 5
usage UI; `7a1163f` Stage 4 naming; `a7eb7ba` Stage 4 spawn-task; `6d814d8` +
`fc350e4` + `4b1c2ff` + `8daf027` Stage 3 migration + budget. Plus the earlier
Notes/Apple-import work (`c11d8be`, `8b4daf5`, `7a6b62a`, `13d406a`, `6ef37fa`).
