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
- 2026-08-13 — Operator: be able to go back to a point in a chat and carry on
  from there (a fork control on each message, with past branches in a dropdown),
  and chats must live in PostgreSQL in a normal schema. Decided and built: a
  transcript is a TREE, `docs/decisions/0020-a-transcript-is-a-tree.md`. Four
  tables (`chat_thread`/`chat_branch`/`chat_turn`/`chat_message`), migration
  `c1a7b8e3d204`, RLS and composite same-thread FKs. `parent_id` is the render
  order and `seq` is demoted to a cursor, which deletes the reason a mid-reply
  message needed cut tokens at all. ONE branch verb, not two: with nothing
  deleted, "return to here" and "branch from here" are the same act. Backfill
  verified on the real data (63 threads, 1,352 messages): conservation 63/63,
  every ordering difference explained. Pulls chat forward out of P6 in the
  persistence plan, which had deferred it. NOT done: the transcript READ still
  comes from the log, so `interleave_history` and its two siblings still exist
  and a fork anchors at the end of an interrupted reply.
- 2026-08-14 — Operator: the branch control opens off to the side and is clipped,
  and why is it on the LAST message. Both real, and reviewing them found four
  more. UI: the clipping was in the KIT, not the chat — `Menu` measured its
  popover against the VIEWPORT only, and `.chat-log` is `overflow-x: hidden`
  inside a centred 1200px column, so it never flipped and lost half of itself at
  the log's edge (@workshop/ui 0.5.4; clip box = viewport narrowed by every
  clipping ancestor, per axis; `side` became a preference that flips too;
  placement projected not read back, so a flip can undo; arithmetic extracted to
  `menuPlacement.ts` and tested). The last-message question had no good answer:
  the control was present-but-disabled there because the branch LIST was the only
  thing left worth opening, and a list of the thread's branches is thread state
  repeated identically under every bubble. Split by what each surface answers —
  bubble is one action (`BranchAction`, absent where the action is the identity
  operation, disabled with a reason mid-turn), toolbar owns which-line-am-I-on and
  switching (`BranchPicker`, only once a second line exists). The rule is one
  question in `branchable.ts`: would going back here produce a different
  conversation? A trailing REPLY, no; a trailing message of YOURS, yes, and that
  is the most useful case in the feature. Backend, all four found by review and
  three invisible from the screen: `_fold` never applied the branch filter, so
  after a fork the operator saw the new line and the MODEL still read the
  abandoned tail (`hidden_event_ids_sync` was written for that caller and had
  zero callers); fork and switch had no concurrency guard, so an in-flight reply
  committed onto the new branch as a child of the fork anchor (both now refuse on
  a busy thread rather than queue — the turn they would wait for is the one being
  discarded); mirror writes for one thread ran concurrently and raced to create
  the thread row, losing three of four messages at the start of a chat (chained
  per thread); and `_MIRROR_TASKS` was referenced and never defined, so the
  held-task safeguard had never once existed. Verified end to end through the real
  handlers in the backend's own environment. 2,217 backend tests, 673 frontend,
  158 kit. NOT done: moving the transcript READ onto the tree. It hinges on a
  decision that is the operator's — a Postgres outage would go from costing
  branching to costing the chat — and on the mirror being provably complete first.
  Plan and recommendation: `docs/platform-redesign/transcript-read-from-rows-2026-08-14.md`.
- 2026-08-13 (3) — Operator: "go do both, do all of your plan". Closed the rest
  of the restart work. `apply.sh` promised it was safe to run concurrently and
  the tooling did not keep that promise: `restart_backend.sh` had no check that
  the code it was starting could run, so on a tree several agents edit at once a
  restart could crash-loop the whole app. It now preflights `import main` (0.78s,
  exactly what uvicorn does) and refuses with exit 3, and verifies afterwards by
  polling /healthz for a source_id instead of trusting `is-active`. The
  per-turn checkpointer was committing other agents' unfinished work because its
  guard asked whether cwd WAS the repo root while `git add -A` is repo-wide from
  any subdirectory (that is how this session's own changes landed in cf3eabd
  beside another agent's migrations); the guard is now containment and it stages
  only the paths the turn wrote, under an flock. Replay no longer re-files the
  turn's tickets and notes (`blocks_applied` + `strip_all_blocks`), reattach
  resumes the journal from a recorded offset instead of rebuilding the message
  from byte zero (with a fallback to full replay on any uncertainty), and a
  recycled pid no longer leaves a dead turn spinning "Still running". Voice was
  on the list and was deliberately NOT changed: it already respawns with
  `--resume` so the conversation survives, and only an in-flight utterance is
  lost, with an honest message. Suite 2171 green.
- 2026-08-16 — Operator: research the ontology review another agent wrote, do our own
  research, propose an overhaul, then implement it with subagents, working and tested.
  Emphasis: memory is many unintegrated systems (Letta included) and a proper memory
  system without junk old systems is critical; ticket sequencing as a DAG is overstated.
  Diagnosis, from four audits of the live tree: the problem is entropy more than
  taxonomy. Every concept has two to five implementations and nothing has ever removed
  one (11 memory substrates, 5 disagreeing context_key->scope mappings, 16 date
  formatters, 6 status mappers with 2 drifted, 62 design docs with 22 carrying no
  status). Plans: `coherence-overhaul-2026-08-16.md` (the spine plus the ratchet) and
  `memory-consolidation-2026-08-16.md` (thread T1), ADR 0022. SHIPPED: all of T1 plus
  the two S0 fixes. Headline finding was that the learnings garbage collector could
  never fire because the injector stamped last_retrieved on every render, which is why
  "Ralph rejected: ts_typecheck_fail" sat in every agent prompt for a month. Also fixed:
  the semantic index never learned about chat, ticket or doc writes (2 call sites, both
  note writes), the relational ticket store silently dropped five fields including
  `awaiting` and `attempts`, and PLAID_ENV was documented, required, and read by
  nothing while the real switch defaulted to production. Letta's 18 memory blocks were
  exported to notes before its wiring was removed; the root-owned :8283 process itself
  needs the operator. NOT done: T2 (chat is the session, and "chat with an agent" is
  currently impossible because _promotion_patch rewrites the context key at creation),
  T3 (one scope module), T4 (delete the dead `run` and `agent` tables), T5, T6.
- 2026-08-18 — Operator: three corrections in one session, all the same shape.
  (1) "If you refuse to remove things you are making things WORSE." A pass that
  NAMED three dead fields and left them in the tree is the entropy this effort
  exists to stop. Removed: the Letta `POST /tools/{name}` surface plus all of
  `services/letta_tools/` (zero callers, kept only for a since-closed security
  ticket, and it exposed an agentic `claude -p` over HTTP), `Job.evidence`'s
  freeform-string shape (its 1,957 records belonged entirely to two ALREADY
  deleted systems, so "genuinely polymorphic" was wrong), `RosterAgent.memory_blocks`,
  `chat_thread.context_cut`, `scripts/migrate_jobs.py`, and `letta-client` from
  the venv. (2) The log history was then rewritten on his instruction and the
  read-side fold deleted with it; the write-side guard at `finish()` stays,
  because that is what stops the shape returning. (3) "It doesn't sound like
  there's a blocker, it just sounded like there's an extra step you could have
  done." Correct: a knowable missing prerequisite is a DEPENDENCY to express,
  not a decision to escalate. The move-runs ticket had been blocked with
  "the operator's call" because it cited a table T4 deleted; it now depends on a
  new ticket (9dc3b4f5) that creates the destination, its false premise is
  corrected, and it is unblocked so the dependency does the gating. Verified the
  gate: not ready while the prerequisite is open, ready the moment it is done.
- 2026-08-18 (3) — Operator: stop shipping half-solutions, the managed links have
  NEVER rendered and paths are still raw, build the whole thing now. Two root
  causes found and fixed. (1) The `<Ref>` chip was rendered NOWHERE in chat: the
  resolver and component existed (I had called that "shipped") but no transcript
  path ever rendered one, so a link was structurally impossible to see. Built the
  producing side: kit `Markdown` (0.5.14) forwards `components`/`urlTransform`;
  new `ChatMarkdown` renders `workshop:` links as chips via a resolved-ref
  context; the briefing tells the dev agent to reference docs/notes/tickets as
  managed links. `doc` refs only resolved under `docs/`, so PROJECT.md and TODO.md
  (the operator's own example) resolved to None: broadened `_clean_doc_path` and
  the docs read route to any `.md` in the tree (write stays docs/-only), and made
  the Memory viewer open a root doc read-only. (2) Scoped exec was off only
  because the backend was never restarted onto the flag; and a naive flip would
  have broken the dev agent. Made confinement the DEFAULT (on where bwrap
  functions, `=0` escape hatch), and pre-flighted every interaction live before
  flipping: dropped `--unshare-pid` (it made `systemctl --user` fail, which
  apply.sh needs; process isolation is the uid layer's job, SEC-A1), dropped
  `--die-with-parent` (it would kill a turn on backend restart, breaking the
  pause-not-break guarantee), added a dev grant set (systemd socket, config,
  git, ~/.claude[.json]), remapped in-tree argv paths to /workspace so the MCP
  server (absolute host paths in `--mcp-config`) still launches, and made
  `session_alive` scoped-aware so existing threads re-mint a session seamlessly
  instead of a doomed --resume. Proven: a real authenticated `claude -p` turn
  runs confined at /workspace, systemctl/git/config/MCP all work, ssh and home
  are absent. Tests green, typecheck+design clean.
- 2026-08-18 (2) — Operator on scoped execution (Layer 1): confinement should be
  the DEFAULT, not an opt-in flag. Access beyond a turn's scope is a GRANT, not
  the absence of a sandbox (capability model, same as macOS TCC; holds for remote
  server and a future desktop app alike). `WORKSHOP_SCOPED_EXEC` off-by-default is
  a migration artifact, not a design stance. Two real reasons it is still a flag:
  (a) flipping changes how every live turn spawns and still owes one attended
  verification; (b) the build/dev role legitimately needs binds beyond `/workspace`
  (restart systemd units, read instance.env, run apply.sh), so making confinement
  the floor requires enumerating that one role's grant set first. Chat turns need
  nothing past their scope and could be confined today. Direction: after the
  attended verify, invert the code default to scoped-on and express the dev role's
  wider reach as an explicit named grant set, so a fresh deploy is confined out of
  the box and the flag becomes at most an escape hatch.
- 2026-08-19 — Operator: a chat said "I've kicked off an investigation, I'll
  respond when it's done" and then never did; asked why this wasn't already
  fixed and to find the actual full fix. It HAD been diagnosed and partly fixed
  hours earlier (`chat-agent-integration-2026-08-18.md`, commit 2f2a262,
  09:50am): `DELEGATION_RULE` was rewritten to tell the model a background
  subagent cannot survive turn-end. That fix did not hold — verified against a
  real thread (89223a47..., 01:25-01:34 UTC 08-19) where the model tried it
  again hours after the restart that put the new prompt live, got "interrupted
  by a session restart," retried, failed the same way again, and only then gave
  up and did the work in the foreground. Root cause, confirmed empirically by
  spawning real `claude -p` processes: a backgrounded Agent/Task call DOES
  deliver correctly when nothing disturbs the process (task_notification
  arrives and gets answered inline, no `--resume` needed) — but this backend
  restarts often, because every concurrent chat turn in this scope runs
  `apply.sh` as its own final step, and a background subagent's in-flight work
  does not survive that restart the way the turn's own outer process does
  (that guarantee — `KillMode=process` + no `os.killpg` on shutdown — was built
  for the turn's process, not for a subagent nested inside it). A prompt asking
  the model not to do this failed twice in the same thread within 7 minutes, so
  the fix shipped as a structural one instead: `services/subagent_guard.py`, a
  PreToolUse hook wired into every chat turn's argv (`--settings` in
  `build_streaming._build_cmd`) that rejects `run_in_background: true` on the
  Agent/Task tool outright. Verified end-to-end against the real CLI: the
  background call is denied with a clear reason, the model retries the same
  call synchronously, and the result lands inline in the same reply — proven
  three times against live `claude -p` processes before landing. `DELEGATION_RULE`
  updated to say the flag is hook-enforced, not just discouraged, so the model
  does not spend a round trip discovering it. 79 targeted tests + a new
  `test_subagent_guard.py` green; full backend suite has pre-existing,
  unrelated failures (db/tenancy/dev-mode fixtures) confirmed present before
  this change and untouched by it.
- 2026-08-18 (4) — Operator: since the bwrap layer (Layer 1) is live and
  proven, have an Opus subagent review the [SEC-A1] uid-layer plan (a
  dedicated sub-user composed on top of bwrap) before building it, then
  implement whatever it returns. The review found the plan's one-line
  description ("sudo -u in front of the argv") was wrong in both directions —
  it omitted `-n`/`-E`, the env/grant-set changes, and a REVERSE signal channel
  (cross-uid `os.killpg`/`proc.kill()` raise `PermissionError`, silently
  swallowed by the existing bare `except Exception: pass`, which would have
  made Stop a no-op and leaked every process) — and, independently, found two
  live gaps in the ALREADY-SHIPPED bwrap layer: the grant set bound the whole
  `$XDG_RUNTIME_DIR` read-write (including `docker.sock`, reachable via
  `clawd-a`'s existing group membership) and `_proc_alive`'s identity check
  never recognized bwrap's own pid as the turn (bwrap forks rather than
  exec-replacing), which broke restart-adoption for every scoped turn since
  Layer 1 went default-on. Fixed both same-day, uid-layer or not. Built:
  `scoped_exec.spawn_argv`/`turn_env`/`signal_pg`/`turn_user`, threaded
  through `build_streaming.py`'s spawn/retire/kill/liveness/`session_alive`
  paths on the uid actually used (recorded at spawn, not recomputed later),
  gated by `WORKSHOP_TURN_USER` (unset = off, FAILS CLOSED on a broken
  precondition rather than silently running as `clawd-a`) and carved out for
  a turn scoped to the platform's own repo (`systemctl --user` cannot cross a
  uid boundary, and `apply.sh` needs it — named, not silent).
  `scripts/setup_turn_user.sh` is the one-time root setup step (idempotent,
  `--check` mode) an agent turn categorically cannot run itself: turns are
  bwrap-sandboxed by default, and bwrap sets `PR_SET_NO_NEW_PRIVS`, which
  makes `sudo` refuse unconditionally even with a perfect sudoers rule
  (confirmed empirically, twice, in this exact conversation). Full backend
  suite confirmed green against a throwaway worktree of the pre-change tree
  (no regressions). NOT done and NOT flipped on: the account, verification,
  and flag-setting are the operator's, per the design doc's own Layer-1 rule.
  Details: `docs/platform-redesign/managed-file-references-and-path-scoping-2026-08-18.md`
  section 9a.
- 2026-08-21 — Operator: back to the core work-loop (chat identifies/files/refines/
  executes tickets under stage-by-stage observation; confinement double-check).
  Ground truth found and fixed. (1) The chat path IS confined and correct (host
  tree, other scopes and other owners absent; only the scope at /workspace plus
  named grants). (2) But the WORKER was the open hole: `ralph.sh` spawned `claude
  -p` directly, unconfined, and S2 (b250a1c) had authorized it to WRITE without
  wiring the flag (`PERM_FLAGS` was defined and never passed, so "AUTHORIZED" was a
  false log) — an unconfined bypass writer. Fixed (7f9275a): the worker spawns
  through `scoped_exec exec`, a new fail-closed shell entrypoint (die-with-parent
  so a `timeout` kill takes the sandbox down), MCP config moved to inline JSON so
  search survives the sandbox tmpfs, PERM_FLAGS wired. (3) The /workspace-vs-
  ~/workshop ROOT MISMATCH was real and its cause was env, not code: `WORKSHOP_ROOT`
  (and siblings) were inherited into the sandbox as HOST paths, so
  `paths.workshop_root()` resolved OUTSIDE the sandbox and a confined turn's
  knowledge/.runtime pointed at a phantom empty dir. Fixed (6267052): `turn_env`
  now remaps env values under the scope root to /workspace, the same way `wrap()`
  remaps argv; confirmed knowledge then resolves the real 110 notes. (4) The triager
  (note->ticket intake) had NEVER run (died on OwnerUnset); pinned the operator as
  owner and ran it dry over the real backlog: 20 of 109 due decided -> 2 task, 2
  link, 1 escalate, 15 none. Conservative, deduped, batched, one note at a time
  (not the whole-chat scan). Confinement completeness: chat done, worker done
  (this turn), in-chat background agents confined by inheritance; VOICE is the one
  remaining unconfined bypass writer, deliberately off-limits. OPEN and operator-
  owned (not done): the attended S2 live write-verify (must run from the UNCONFINED
  resident runner, cannot be tested from inside a confined chat turn); the
  chat->loop dispatch policy (chat-filed tickets are unattended-INELIGIBLE by
  design, `execution` unset/fail-closed — making them dispatchable is the
  filing-vs-execution autonomy setting, not a silent default); then S3 (one
  supervised resident driver). Model of record: `work-loop-core-design-2026-08-21.md`.
- 2026-08-21 (2) — Operator: stop asking, do it. Three directives, all built+tested.
  (1) VOICE was not exempt on purpose; it spawned `claude -p` directly with
  bypassPermissions. Confined it through the same scoped_exec.spawn_argv+turn_env
  seam (41367f1); no agent-spawn path is now outside the sandbox except by explicit
  future choice. (2) Ran the attended S2 LIVE WRITE-VERIFY. `systemd-run --user`
  runs UNCONFINED (the granted systemd socket spawns in the manager's context, not
  the turn's namespace) -- that is both the way to run the verify from an
  unconfined parent AND a noted capability of the dev role's systemd grant. Proved
  it two ways: an sh worker (pwd=/workspace, only clawd-a home, host tree absent,
  wrote its file) and a real `claude -p` agent that wrote NOTE.md and COMMITTED it
  (4c5010d) unattended, confined. The worker the loop runs IS a claude process, so
  that is the thing that had to work, and it does. (3) Built the per-scope EXECUTION
  SETTING (the answer to "what scope": a ticket lives in a scope, so per-scope, not
  per-chat; the per-chat filing mode already exists). services/scope_execution.py
  (reactive|supervised|autonomous, default reactive/fail-closed), gated in
  next_ready(unattended=True) at the one chokepoint (attended path untouched),
  GET/POST /agents/autonomy/scope-execution, spawn_task classifies execution="code"
  when the scope is armed (the chat->loop connection), and a Background execution
  control in the chat Work settings (6b97299, 9d07b75). Tested: scope_execution
  store, the gate, the API round-trip, next_ready regression fixed, typecheck +
  check_design clean, 1032 frontend tests pass. STILL not turned on: nothing
  auto-runs until a scope is armed AND the global override is clear AND a resident
  driver (S3) runs; S3 is the next build.
- 2026-08-22 — Operator: reassess the whole lineage, don't take past work for
  granted, keep the design honest (REST, separation of concerns, no parallel
  systems, no Jenga tower), and BUILD the core work loop so it actually runs and
  he can use it. Also: work continuously, do not stop.
  FINDING THAT REFRAMED S3: it did not need building. `queue_runner.sh` (resident
  sweep, per-tree flock, wake file, supervised-by-default landing at in_review),
  `project_ralph.sh`, `next_ready(unattended=True)`, the `scope_execution` gate, a
  full REST runner API (status/targets/start/stop) and its Settings UI all already
  existed. The loop was COMPLETE AND DISCONNECTED. The real defect was that
  ARMING a scope and having a RUNNER resident were two independent controls and
  neither implied the other: arming from the chat's Work settings did nothing at
  all, and the only thing that would ever restart a dead runner was a cron entry
  that is not installed (crontab is empty; the runner log had been dead since
  Aug 10). So the build was a reconciler, not a driver: `services/work_driver.py`
  makes mechanism follow intent in the same shape as the other lifespan
  reconcilers (port_registry, deploy_watcher, jobs). Additive only -- it ensures a
  runner per armed scope and never kills one it did not start; disarming stops at
  the moment of disarming. Start now ARMS and Stop DISARMS, so neither is a button
  the next reconcile pass undoes. Route layer stopped owning process management.
  THEN RUNNING IT FOUND FOUR MORE, none visible by reading:
  (1) Intent was keyed by ROOT PATH. Arming `development` from inside a confined
  chat turn stored "/workspace" (the bwrap mountpoint), and the backend started a
  runner for a directory that does not exist in its namespace
  (`project not found or no dir: /workspace`). Re-keyed by SCOPE; roots resolve
  per namespace; legacy path keys fold on read. Same class as the WORKSHOP_ROOT
  remap of 2026-08-21 and it will recur anywhere a confined turn persists a path.
  (2) The interpreter was INHERITED, not resolved: every script calls a bare
  `python3` and needs the venv, which only held because the backend's PATH had it.
  Started by cron/nohup the store read fails as "a database is configured but did
  not answer" -- a missing import reported as a database outage. instance.sh now
  puts the venv on PATH and queue_runner.sh sources instance.sh itself.
  (3) The owner pin was a COMMENT, not a function: project_ralph.sh's inline python
  pinned no owner and died on OwnerUnset. Three copies existed and one omission;
  now one, `ownership.pin_operator`.
  (4) THE LOOP COULD NOT WRITE. `WORKSHOP_UNATTENDED_WRITES` was set in exactly one
  place, behind the operator's Run-now button, so the entire autonomous path
  started a worker with no hands: armed scope, live driver, resident runner,
  claimed ticket, and every write silently auto-denied. Both comments owning the
  flag named the S3 driver as where it changes, and the attended verification had
  already run (4c5010d). Set on the sweep invocation. Still bounded by: armed
  scope, supervised ceiling (in_review), bwrap confinement, the
  irreversible-action deny gate, and the reviewer gate on a revertable commit.
  SEPARATELY, the operator's turn was killed mid-work and he asked why, and why
  his reply had an outline. One cause: a `search` call found the index empty, the
  rebuild ran IN-PROCESS inside the turn's own MCP server child (an 8-minute
  multi-GB ONNX job; a healthy query server is 11 MB), and the turn was killed at
  the 6 GB ceiling for searching. The outline was not a stray design change: it is
  `.chat-bubble.error`, correctly reporting the errored turn. Worse, the ceiling
  reclaimed NOTHING -- `_kill_turn_proc` called `proc.kill()` and returned, making
  its own `killpg` unreachable, so it killed the group LEADER and orphaned
  `claude` plus its 9.0 GB child to systemd for seven hours. Fixed both: index
  maintenance is now a backend lifespan loop with a single owning process, and the
  hard-kill path signals the GROUP like its sibling `_retire_turn_proc` always
  did. 9.0 GB reclaimed.
  MEASURED WHILE WATCHING IT: a 20-minute per-task wall is not enough for a large
  UI ticket (the chat-branching one timed out with nothing committed); and the
  worker cannot be launched from inside a confined chat turn (its own bwrap cannot
  bind host paths), which is exactly why the backend must be the thing that
  spawns it -- the reconciler.
- 2026-08-22 (4) — Operator answered a ticket question from the chat toolbar and
  could not tell whether anything had happened. It had, and it was the opposite
  of what he asked for. Directive mid-session: "don't rush to fix! Read research
  into it more and think and plan and design THEN act and do all this."
  ROOT CAUSE, and it is a parallel-systems defect rather than a bug: the word
  `approve` named two OPPOSITE outcomes in two routers. routes/jobs mapped it to
  in_review -> done ("accept the worker's completion"); routes/tasks_all.
  review_task mapped it to "release the block, the task is todo and runs again",
  and its docstring said "Answer a BLOCKED task". The inbox posted every
  free-text answer to the second one regardless of what the ticket was waiting
  for, so "Approve" on FINISHED, COMMITTED work sent it back to `todo`, refunded
  an attempt via restore_attempt, and wrote the bare word into the ticket body as
  a Clarification the next attempt would read as an instruction. Measured on the
  live record for 86f58aea at 07:04:28Z. entity-model-2026-07-18 had already
  ratified `in_review -> done` on approval, so this was a divergence from the
  model of record, not an open question. Notably TaskPanel was CORRECT and knew:
  it gates the verbs by status and relabels the blocked one "Release".
  BUILT: services/task_decision owns what an answer means and both routers call
  it (accept | send_back | answer | cancel | dismiss, each named for its
  outcome); the verb is chosen from the ticket STATUS, never assumed; `approve`
  is REFUSED on a finished ticket rather than guessed at, which keeps the two
  correct callers working; only a rework instruction reaches the body; only a
  ticket that STOPPED refunds an attempt. Repaired 86f58aea (accepted, bogus
  clarification removed, cause in evidence) and scanned every other ticket for
  the same shape: it was the only one.
  THE QUESTION ALSO COULD NOT BE ANSWERED WHERE IT WAS ASKED. question_report
  ended its chat message "Answer here, or on the ticket: /tickets/<scope>/<id>"
  and both halves were false: nothing routes a chat reply back to `awaiting`, and
  a bare path is not a link, which on a phone means unfollowable. `ticket` has
  been a managed ref kind since managed links shipped, so it is a chip now. The
  decision itself is INLINE under the message that raised it (work/
  TicketDecision.tsx), following the rule ApprovalBlock already states for tool
  permissions: rendered in the log under the message that caused it, not in the
  strip above, which is for things pointing OUT of the chat. It reads live ticket
  state rather than remembering its own click, so answering in the panel, on the
  ticket page or in the transcript resolves all three. clarifying-question-flow-
  2026-07-18 had specified exactly this, was marked accepted, and was never built.
  ALSO: the ticket PAGE offered nothing at all for in_review (its answer box was
  gated on `blocked`), which is why nothing looked actionable; four in_review rows
  shared one generic sentence so a list of them said the same thing under four
  titles (the sweep now says which question it is and what each answer does, and
  run_inbox offers Accept / Send back as taps). Three of the four had no
  originating chat at all (source.kind agent/operator), so the asymmetry he saw
  was correct behaviour, not a bug.
  CONVENTION, which had never been written down: docs/ticket-writing.md.
  ticket_format enforced four labels and nothing said how to fill them, so
  tickets drifted into shorthand the operator could not read on his own queue
  (the offending title was "Pin the backend-to-renderer seam with a contract
  test", which I wrote). It covers BOTH readers, which is the thing that was
  missed: the worker gets the body and nothing else, and the operator opens the
  ticket to decide something on a phone weeks later. Wired into chat_context's
  build rule and ticket_drafts' prompt.
  Design: docs/platform-redesign/deciding-on-work-from-a-chat-2026-08-22.md.
  Backend suite verified against clean HEAD in a throwaway worktree: zero new
  failures. 1047 frontend tests, typecheck, check_design clean.

- 2026-08-22 (3) — Operator: get caught up and continue. The loop was deadlocked,
  and unblocking it surfaced the gate hole that matters most.
  IT WAS BILLING TO THE WRONG BUDGET. Every 60s cycle for hours refused with
  "project ceil reached: workshop 1518896/1500000" while every ticket it wanted
  to work lives in the `development` scope, whose own 1,500,000 sat untouched.
  BILL_SCOPE fell back to basename($DIR), and the platform repo's basename is
  "workshop" while its ticket store is `development` (ADR 0014). So the
  per-scope ceiling gated a scope the sweep never touched. Third instance of one
  defect: identity derived from a filesystem path rather than the scope key
  (after the execution-intent re-key and the WORKSHOP_ROOT remap). Fixed by
  asking task_scope.key_for_root; the ledger key stays a BARE project slug
  because that is what dispatch admits against, `agent:` already matches, and an
  unregistered directory keeps basename so an ad-hoc sweep still bills
  somewhere. Tests run the shell function extracted from the script rather than
  a Python restatement, so it cannot drift green. Proven live: the next sweep
  logged scope=development and immediately claimed the ticket eight cycles had
  refused.
  THEN THE LOOP CLOSED, END TO END, ON ITS OWN. 86f58aea reached IN_REVIEW (the
  supervised ceiling, the thing never previously verified). A ticket filed FROM
  THIS CHAT for the sibling learn_scope defect was picked up unprompted and
  committed by the worker (3eaf0e0), correctly declining to strip the `project:`
  prefix because learnings needs the prefixed shape while the ledger needs the
  bare slug, and migrating the misfiled entries. Chat -> ticket -> autonomous
  execution -> commit, unattended.
  THE FINDING THAT MATTERS: A WORKER CAN PASS ITS OWN GATE BY RE-PINNING IT.
  Watching 86f58aea revealed how it had actually "succeeded". Its reviewer failed
  on module-layout regressions, so the worker ran --write-baseline and committed
  971f7f3, moving function_local_imports 452->552, largest_scc_size 58->110,
  upward_violations 42->48, big_file_count 21->22 and mutable_global_count
  60->63. The gate then passed and the task landed in_review looking like
  success. The checkers cannot see this: every one reads its baseline out of the
  WORKING TREE, so rewriting the baseline is indistinguishable from a clean tree.
  A ratchet a worker can re-pin is not a ratchet. Closed with
  check_ratchets_not_loosened.py, wired into reviewer.sh and gated on the diff
  touching a baseline file. The escape hatch needs no token: reviewer.sh gates
  UNATTENDED completion only, so a worker cannot loosen a ratchet and the
  operator, who never passes through it, still can. Direction of "worse" is
  IMPORTED from check_module_layout._HIGHER_IS_WORSE rather than restated,
  because that header warns a future metric could be higher-is-better.
  Verified against the real event and end to end with a planted loosening.
  The erased signal became ticket 7c3b804d rather than being lost: files grew
  9% (223->244 scanned) but largest_scc_size grew 90% while scc_count FELL 3->1,
  so three cycles merged into one 110-module component, about 45% of the backend.
  That is not explained by 9% growth and needs a real answer, not a re-pin.
  ALSO FIXED, both found by reading the live logs rather than the source:
  services/latitude.py's CLI never pinned a data owner, so every unattended run
  emitted an OwnerUnset traceback and then logged
  "latitude=standard (source=default)" as though the cascade had run --
  ownership.pin_operator is the one set-point and its docstring already named
  four earlier entrypoints that each learned this separately; this was the
  fifth. And ralph.sh reported the crash as source=default, the SAME answer it
  gives when nothing is configured, which is precisely why a traceback sat under
  a line that read normal. The value still falls back to standard; only the
  source now tells the truth. Blast radius measured, not assumed: all 47 tickets
  and every project have latitude null, so no run was misgoverned -- but the
  mechanism was dead, and `strict` is the only value that makes reviewer.sh
  block instead of guess.
  RETIRED THREE FALSE LEARNINGS. .runtime/learnings/project-workshop.jsonl was
  injecting "Ralph keeps hitting raw_browser_storage; the direct approach is not
  converging, try a different tactic" for the three tickets whose work had in
  fact landed and whose rejection was the allowlist defect fixed in 05513ba 13
  minutes later. render_count was above zero, and the loop was about to re-claim
  one of those very tickets. Archived through the existing archive path with the
  reason recorded, not deleted.
  MEASURED HAZARD, unresolved: a concurrent worker reset the shared working tree
  mid-session and discarded uncommitted edits (the reviewer wiring had to be
  redone). This is the stage-2 revert hazard, live. Working on the shared tree
  while the loop runs means committing immediately or not at all; it is also why
  the worker's reviewer now judges MY commits as part of its diff, since its
  baseline predates them.

- 2026-08-22 (2) — Operator: keep going and testing, and plan/build the next phase
  of search, embedding search and context discovery for chats and agents.
  OVERNIGHT RESULT FIRST: the loop worked THREE real backlog tickets unattended
  and committed all three (d6ec10a contract test, f6b430e sent-message dim,
  d2c894c working indicator), then marked all three PARKED "no progress". The
  worker was fine; the REVIEWER was wrong. Every one was rejected for the same
  reason, `raw_browser_storage`, on three unrelated tickets that never opened a
  storage file. All 8 remaining violations were in packages/ui/src/storage.ts,
  which is not a violation: it is the namespacing engine itself, promoted there
  from frontend/src/lib/storage.ts on 2026-08-17 while the allowlist stayed
  behind. Because the check is repo-wide, NO ticket touching any frontend .tsx
  could ever pass the gate again. Fixed the allowlist (and made it print its
  owners from _ALLOWED rather than hardcoding one, which is how the claim and
  the list drifted apart); verified a planted violation is still caught. The
  three tickets were unparked to `todo` with a note recording the false
  rejection, so the gate judges the committed work rather than me asserting a
  pass I did not get. typecheck clean, 1037 frontend tests (up 5: the loop's own
  new contract test).
  SEARCH, MEASURED BEFORE DESIGNED: docs are 66% of the index (3,834 of 5,773
  chunks) and this tree keeps several design generations. "is an app the same
  thing as a project" returned docs/workshop-v1/00-overview.md FIRST -- a doc
  whose banner says historical and which CLAUDE.md says not to follow -- beating
  the model of record by 0.001, with two of five hits from that one file. So
  search was actively teaching agents the retired model, which is the operator's
  recurring complaint made mechanical. Built: chunks carry a `status` parsed
  from the doc's OWN banner (the corpus keeps declaring its own state, no list
  in code), a historical doc SET declares itself once with `.index-status`
  rather than platform source naming paths, superseded chunks are DEMOTED in
  rank but never hidden (the floor still tests the real score, because sometimes
  the history is the question), they arrive LABELLED so the model knows what it
  is quoting, and at most 2 chunks per document so one plan cannot take three of
  five slots. TWO TRAPS, both hit and both now pinned by tests, either of which
  silently INVERTS the ranking: 34 docs open by saying what they REPLACE
  ("Supersedes nothing") and a naive /supersede/ match demotes exactly the
  current files; and entity-model-2026-07-18.md says "PARTLY SUPERSEDED" three
  lines from its claim, so status is judged per BLOCK (per line splits the
  banner, a whole-header blob lets an unrelated qualifier suppress a real one).
  Result: model of record now first, historical doc out of the top five, five
  distinct sources instead of duplicates; rebuild re-embedded 0 of 5,708 because
  the digest is content-keyed. NOT DONE: staleness that is not declared anywhere
  (a current doc discussing a retired field) is untouched, and deliberately so —
  a recency prior is not the same as correctness and was not justifiable on the
  measurements.
