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
