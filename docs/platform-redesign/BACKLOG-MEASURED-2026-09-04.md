# The measured backlog: what is designed, what is built, which blockers are stale

- Date: **2026-09-04.** Every verdict below was produced by running a command
  against this tree today. The command is printed. Nothing here is quoted from
  a document.
- Method: a doc claiming something is planned is not evidence it is unbuilt, and
  a doc claiming something is done is not evidence it is built. Where no command
  could decide an item it is marked **UNMEASURED** and says why.
- Sources read: `EXECUTION-PLAN-2026-08-27.md`, both 2026-08-26 indexes,
  `WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md`, `REVIEW-plan-vs-reality-2026-08-29.md`,
  `AUDIT-2026-08-31.md`, the four most recent handoffs, and the live ticket store
  (`python3 scripts/project_tasks.py list-json .` → **211 live**: 183 todo,
  14 in_review, 9 parked, 4 blocked, 1 draft).

**Three items are owned by other agents right now and are not audited here:**
git worktrees for chats/agents (incl. arming `WORKSHOP_TREE_LEASE_MOUNT` and
`reader_staleness.py`), removing remaining app-specific names from platform
source (`check_no_app_names.py`, `d7a308f7`, `1596d691`, `403b3b88`), and the
chat client-state / 502 / disappearing-history frontend bug. They appear in the
ranking with verdict **OWNED** so the order is honest, and nothing else.

**One caveat that bounds this audit.** Live table row counts could not be taken.
A direct DSN connection from this sandbox reaches a scratch Postgres
(`chat_thread` = 0, which is false), exactly the failure CLAUDE.md warns about,
and every `/api/*` read needs auth. So `lease`/`worktree`/`change`/`run`
occupancy is **UNMEASURED** here rather than reported as zero.

---

## The ranked table

| # | Item | What it is | Spec | Verdict | Deciding command |
|---|---|---|---|---|---|
| 1 | **The ticket store disagrees with the tree on 13 tickets** | The whole `threads-branches-and-pages` §7 chain plus the most-repeated UI complaint are BUILT and still read `todo` | `threads-branches-and-pages-2026-08-29.md` §7; `HANDOFF-2026-09-01.md` §5 | **BUILT (tickets not closed)** | `python3 scripts/check_plan_landed.py` → `6/6 steps landed`; `project_tasks.py list-json .` → all six still `todo` |
| 2 | Autonomy off; queue runner idling | `.runtime/autonomy-override.json` engaged since 2026-08-24; runner has logged nothing else since | `WHY…-2026-09-01.md` §2 | **UNBUILT (operator decision + unproven runner)** | `cat .runtime/autonomy-override.json` → `engaged: true`; `wc -l .runtime/queue-runner.log` → **81453**; `tail -2` → `autonomy disabled … idling` |
| 3 | A view of what the operator asked for | Ticket provenance is now honest; nothing renders it | `WHY…` §1, §8.2 item 1 | **PARTIAL** | `grep -n source backend/services/tasks.py` → `SOURCE_KINDS = (operator, chat, agent, autogen, unknown)`, `source_kind()` "never guesses"; API filter at `schema.gen.ts:7766`; `grep -n source frontend/src/pages/Tickets.tsx` → **0 hits** |
| 4 | Executable acceptance on the backlog | The gate is built and refuses new tickets; 202 old ones are pinned debt | `WHY…` §8.2 item 2 | **PARTIAL** | `python3 scripts/check_ticket_acceptance.py` → `with an executable finish line: 19 / without one: 202 (pinned at 202)` |
| 5 | `thread.armed` / `thread.role` mean nothing yet | Column, check constraint and PATCH validation exist; no dispatcher reads them | `entity-schema-2026-08-27.md`; tickets `2f1b6a66` `5d004289` `e35a808a` | **PARTIAL** | `grep -rn THREAD_ARMED backend/` → model + `routes/chat.py:1484` only; `grep -rln armed backend/services/` → `work_driver.py` reads `scope_execution.armed_roots()`, never a thread; `grep '\.role' backend/services/chat_context.py` → **0** |
| 6 | Token accounting chain | `token_usage.py` is the declared central seam and nothing imports it; the ledger row is still one integer; no price table | `counting-tokens-2026-08-25.md`; tickets `c0bac5ad` `ea6f0c1a` `16625867` | **UNBUILT** | `grep -rn token_usage backend/ --include=*.py \| grep -v tests \| grep -v services/token_usage.py` → **0**; `tail -1 .runtime/usage.jsonl` → `{ts, project, tokens, run, role, chat}`; `ls backend/services/model_prices.py` → No such file |
| 7 | Pause a run at the budget cap (`70860d58`, P100) | Stop at the line, resume without redoing work | ticket `70860d58`; `limits-plan-audit-2026-08-24.md` §2.1 | **UNBUILT** | `grep -rn "budget_cap\|pause_at_cap" backend/services/work_driver.py` → 0; `jobs.py` carries `budget` as an opaque dict only |
| 8 | Prompt-budget chain | Nothing declares, measures or bounds a turn's prefix | `agent-context-plan-2026-08-24.md`; tickets `0e810bf9` `4614bfee` `c971b8f9` `f8fced4a` `1f0d6dcd` | **UNBUILT** | `grep -rn PROMPT_BLOCKS backend/ scripts/` → **0**; `ls scripts/check_prompt_size.py` → No such file; `grep -n "PROJECT.md" backend/services/semantic_index.py` → **0** |
| 9 | Hosting / artifact / release chain | No artifact store, no release or deployment record | `hosting-apps-design-2026-08-24.md`, `app-artifact-spec-2026-08-24.md`; tickets `fb93c4d2` `9ed7a34b` `80709722` `0cae7ab2` | **UNBUILT** | `grep -rn "class Artifact\|class Release\|class Deployment" backend/ --include=*.py` → 0 (only sqlalchemy internals); `ls .runtime/artifacts` → No such directory |
| 10 | Stage-5 runtime app loader (`86564637`) | Install an app without rebuilding the shell | `stage-5-app-loader-plan-2026-08-24.md` | **UNBUILT** | `grep -rn "importMap\|import-map" frontend/ backend/` → one comment, `frontend/src/apps/registry.tsx:54`, naming it as not built |
| 11 | Delete the deploy subsystem (`726d75a9`) | It has never run once and still boots | `REVIEW-plan-vs-reality-2026-08-29.md` §5 Tier 1 row 7 | **UNBUILT** | `grep -n deploy backend/main.py` → `:25` imports `routes.deploys`, `:434` mounts it |
| 12 | Delete `services/latitude.py` | Ordered deleted 2026-08-25; null on 100% of tickets | `REVIEW…` §5.1; `AUDIT-2026-08-31.md` | **UNBUILT** | `grep -rnE "from services(\.|.*import).*latitude" backend/ \| grep -v tests \| grep -v migrations` → **6 live import sites** (`db/ticket_sync.py`, `db/models.py`, `db/backfill.py`, `services/agents_registry.py`, `services/tasks.py`, `routes/projects.py`) |
| 13 | `services/triager.py` (1,004 lines) never executed | Built, entrypoint script exists, zero importers | `WHY…` §5.1 | **UNBUILT (dead)** | `grep -rnE "from services(\.|.*import).*triager" backend/ \| grep -v tests \| grep -v migrations` → **0**; `scripts/triager_run.sh` exists and nothing calls it |
| 14 | Zero declared command tools | Complete mechanism, empty registry | `tools-are-declared-2026-08-23.md`; `WHY…` §6 item 4 | **UNBUILT (unused)** | `cat .runtime/tools.json` → `{"tools": []}` |
| 15 | Run the bare ratchets on a schedule (`0f2f3fa6`) | Two silent upward drifts in one day motivated it | ticket `0f2f3fa6` | **UNBUILT** | `crontab -l` → no crontab; `systemctl --user list-timers` → `0 timers listed`; `ls /etc/systemd/system/*.timer` → only `backup.timer` |
| 16 | Images do not survive compaction | The real diagnosis, unfiled | `WHY…` §6 item 8 (retracting the `build_streaming.py:2666` claim) | **UNBUILT, UNFILED** | ticket store search for `image\|attachment\|upload` in titles → **0 of 211** |
| 17 | Messages out of order during a running turn (`ae9a6c08`, P100) | Two sends during one turn render in the wrong sequence | ticket `ae9a6c08` | **UNMEASURED** | No static decider exists. It needs a live two-send-during-one-turn repro against the built origin; grep cannot distinguish "fixed" from "not reproduced". The adjacent read-state bug WAS fixed (`efb38e6a`) and is a different defect |
| 18 | `main` is not published | 812 commits on `main` have never reached `origin/main` | `HANDOFF-2026-09-04` §4 | **UNBUILT** | `git rev-list --count origin/main..main` → **812**; `git rev-list --count main..origin/main` → **0** |
| 19 | Response-type debt | Named as ~528 instances; the ratchet says otherwise | `HANDOFF-2026-09-01.md` §5 | **PARTIAL** | `python3 scripts/check_response_types.py` → handwritten **25** (was 92), untyped reads **97**, handwritten list reads **56** |
| 20 | Backend suite known failures | Pinned ratchet, 15 left | `AUDIT-2026-08-31.md` §6 | **PARTIAL** | `scripts/backend_tests_baseline.json` → 15 entries; `python3 scripts/check_dead_surface.py` → nothing new vs baseline |
| — | Arming `WORKSHOP_TREE_LEASE_MOUNT`, `reader_staleness.py` (B8), worktree row occupancy | | `EXECUTION-PLAN` C-3, `workspace-ownership-and-leases-2026-08-22.md` | **OWNED** | not audited here |
| — | App names in platform source (`d7a308f7`, `1596d691`, `403b3b88`) | | `app-is-the-entity-2026-08-20.md` §9 | **OWNED** | `python3 scripts/check_no_app_names.py` → content 0, imports 15, literals 275; `bash scripts/check_fresh_clone.sh` → 4 PASS, 1 FAIL (`no_hardcoded_identity`) |
| — | Chat client-state / 502 / disappearing history | | `client-state-implementation-2026-08-26.md` | **OWNED** | not audited here |

---

## Item 1 — the ticket store disagrees with the tree (13 tickets)

This is the single largest source of false backlog. `check_plan_landed.py`
reports `6/6 steps landed`, and each of these was independently confirmed:

| ticket | status in store | measured in tree |
|---|---|---|
| `d5a051c8` thread `armed`+`role`, roster retired | todo | `backend/db/models.py:1512` declares `armed`, `:1548` its check constraint. Step 1 `ok` |
| `9fc4ce67` Roles page | todo | `frontend/src/pages/Roles.tsx` exists, routed at `App.tsx:190`, `/agents` redirects to it |
| `130ef15e` Work view; delete Runs/Changes/Deploys | todo | `pages/Runs.tsx`, `pages/Changes.tsx`, `pages/Deploys.tsx` all absent; `AppWorkTab.tsx` exists |
| `427a86fe` Branches lens | todo | step 2 `ok`. But `grep -rn queryKeys.leases frontend/src` → **0**, so the leases read is still uncalled |
| `d12a0332` delete `/projects` and `ProjectDetail` | todo | `ProjectDetail.tsx` and `Projects.tsx` absent; `App.tsx:233` redirects `/projects` → `/apps` |
| `85a51a32` Settings 12 rows → 9 | todo | `places.ts:258-289` lists exactly **9** rows |
| `5b41eb9d` retarget 17 links, delete the three pages | todo | pages deleted; `App.tsx:207-209` redirect to `/`; 4 non-App.tsx links remain |
| `7d8d733a` an app opens to something it chose | todo | `App.tsx:109 AppHomeRedirect` uses `app.landing`; `appTabState.ts` `tabsForApp`/`canHaveContent` drops tabs an app cannot hold |
| `65cb8c6f` the chats page is a list of chats (P100) | todo | `grep -n worktree frontend/src/pages/Chats.tsx` → **0 hits**; grouping is gone (`0aba1743`) |
| `c8c49cca` publish compaction state (P100) | in_review | `thread_ops.py:87,120` call `chat_events.bump`; exposed on GET at `routes/chat.py:535,751`; 409 guard at `:884` |
| `49e5ea37` `d6696cd3` `02933169` `17f0e21d` `eb4649e2` | in_review | filed 2026-09-01, never adjudicated |

**Missing:** nothing in the code. The store's status field.
**Smallest next step:** one pass over these 13 with the reviewer gate, closing
what passes and re-scoping `427a86fe` to the one measurable remainder (a browser
caller for `GET /leases`). **~2h.**

**This is `4868a051`'s premise generalised.** That ticket asks for stale
*blockers* to be corrected; the more expensive error is a stale *status*, and it
is what makes the backlog read as 211 items when it is not.

---

## Item 3 — the operator's queue has a backend and no front

**Missing:** a `/tickets` view filtered to `source.kind in (operator, chat)`,
ordered, with state. The provenance defect `WHY…` §1 describes is **fixed**:
`SOURCE_KINDS` now contains `unknown` and `source_kind()` documents that it
"never guesses". The API accepts a `source` filter. `Tickets.tsx` references
`source` zero times.
**Smallest next step:** a source filter control on `/tickets` bound to the
existing query param. **~3h.**

## Item 4 — acceptance criteria

**Missing:** 202 pre-gate tickets have no executable finish line. The gate,
the ratchet and the PATCH-door fix all exist.
**Smallest next step:** this is not one task; it is 202. Convert only the
priority-100 and priority-80 set (75 tickets) and re-pin. **~6h**, parallelisable.

## Item 5 — armed and role

**Missing:** the three behaviours the fields exist for. Nothing schedules a turn
for `armed = schedule`, nothing wakes a thread on `on-ready-ticket`, nothing
narrows a turn by `role`. `wakeups.py` is the durable alarm and is already built
and proven, so `5d004289` is a wiring job, not a construction one.
**Smallest next step:** `5d004289` — point the existing wakeup drain at threads
whose `armed = schedule`. **~4h.** The other two follow it.

## Item 6 — token accounting

**Missing:** (a) the four usage writers still write `{tokens: int}`;
`token_usage.py` has zero importers, so the "one central seam" is a file nobody
calls. (b) No `model_prices.py`, so no money answer. (c) The ledger row cannot
be re-evaluated under a different weight because components were never stored.
**Smallest next step:** `c0bac5ad` — store components on the row. Everything
else in the theme depends on it and it is one writer plus a version tag. **~4h.**

## Item 7 — pause at the budget cap

**Missing:** everything. `jobs.py` carries `budget` as an opaque dict it passes
through; nothing compares spend to a cap and nothing can resume.
**Smallest next step:** blocked in practice on item 6 — a cap needs a meter whose
unit is recoverable. Re-order it behind `c0bac5ad` rather than leaving it P100
and unstartable. **~8h after item 6.**

## Item 8 — prompt budget

**Missing:** the registry, the gate script, and the corpus entry for
`PROJECT.md`. All four tickets are unstarted; `1f0d6dcd` is correctly `blocked`
on the other three.
**Smallest next step:** `0e810bf9`, the `PROMPT_BLOCKS` registry. It is
declarative and is the only one the other three need. **~5h.**

## Items 9 and 10 — hosting and the app loader

**Missing:** the whole chain. No artifact store, no release record, no runtime
loader. Stage 7 (extraction) landed on 2026-09-04 and explicitly did not touch
these; `registry.tsx:54` says so in a comment.
**Smallest next step:** `fb93c4d2`, the content-addressed artifact store under
`.runtime/artifacts`. It is the only one with no dependency. **~6h.**

## Items 11-13 — three deletions that were decided and not done

`726d75a9` (deploy subsystem, still imported and mounted in `main.py`),
`latitude.py` (6 live import sites, ordered deleted 2026-08-25), and
`triager.py` (1,004 lines, zero importers, an entrypoint script nothing calls).
**Smallest next step:** `latitude.py` first — it has the most import sites but
the field is null on 100% of tickets, so every site deletes rather than
migrates. **~3h for all three.**

## Items 14-16 — three small, fully specified gaps

- `.runtime/tools.json` is `{"tools": []}`. The wakeup, clock and notify tools
  the docs list as missing are all **built as built-ins** (`wake_me`,
  `list_wakeups`, `cancel_wakeup`, `notify_operator` in
  `backend/mcp/workshop_query_server.py`; the clock at `chat_context.py:1272`),
  so the remaining gap is the declared-tool path having no first user. **~2h**
  to declare one real tool and prove the path.
- `0f2f3fa6`: no cron, no systemd timer. **~1h.**
- Image survival across compaction: unfiled, zero tickets. **File it first**;
  the fix is scoped by `WHY…` §6 item 8 and is not a per-turn injection change.
  **~1h to file, size unknown until measured.**

## Item 19 — response types

**Missing:** 97 untyped decodes, 56 of them reading collections. The "~528"
figure is stale by a factor of five and the ratchet has improved three times.
**Smallest next step:** the 56 list reads, highest blast radius first — those
are the ones that fail as "empty" rather than as an error. **~6h.**

---

## Stale blockers found

Every line below is a sentence in a tracked doc that claims something is blocked,
missing or unbuilt, which a command run today contradicts.

| Doc | The claim | Measured today |
|---|---|---|
| `HANDOFF-2026-09-01.md` §5 ("Unbuilt in dependency order: `d5a051c8` … `85a51a32`") | six items unbuilt | `python3 scripts/check_plan_landed.py` → **6/6 steps landed** |
| `AUDIT-2026-08-31.md` §6 "Known and unbuilt" (same six) | same | same command |
| `REVIEW-plan-vs-reality-2026-08-29.md` §5 Tier 1 rows 1-6 | all "Todo", with line counts for `ProjectDetail.tsx` (1801) and `Projects.tsx` (224) | both files **do not exist**; `ls frontend/src/pages/ProjectDetail.tsx` → No such file |
| `REVIEW-plan-vs-reality-2026-08-29.md` §5 row 6 | "Measured 12" settings rows | `places.ts:258-289` → **9** |
| `REVIEW-plan-vs-reality-2026-08-29.md` §5 row 14a "Nothing runs the backend suite. `reviewer.sh:277-296` runs mypy … never `pytest`" | pytest ungated | `grep check_ backend scripts/reviewer.sh` → `check_backend_tests.py` is in the gate list |
| `REVIEW-plan-vs-reality-2026-08-29.md` §10 item 1 "`bash scripts/apply.sh`. Nothing else matters until…" | drift | superseded; `apply.sh` is now run per-change and reports schema drift itself |
| `HANDOFF-2026-09-01.md` §5 and `AUDIT-2026-08-31.md` §6, "Uploaded images survive exactly one turn (`build_streaming.py:2666`)" | wrong mechanism | **Retracted in `WHY…` §6 item 8** and still stated as fact in both docs. Attachments are content-addressed and durable; compaction is what destroys them |
| `HANDOFF-2026-09-01.md` §5, "**~528** hand-written response types" | 528 | `python3 scripts/check_response_types.py` → **25** handwritten, 97 untyped |
| `HANDOFF-2026-09-04-app-separation-done.md` §4, "`main` is 1130 commits behind `origin/main`" | direction inverted | `git rev-list --count main..origin/main` → **0**; `origin/main..main` → **812**. `main` is 812 **ahead**, unpushed |
| `WHY-THE-WORK-DOES-NOT-LAND-2026-09-01.md` §1, "`operator` doubles as the value for 'nobody recorded anything' — there is no `unknown` in `SOURCE_KINDS`" | fixed | `backend/services/tasks.py:770` → `SOURCE_KINDS = ("operator", "chat", "agent", "autogen", "unknown")` |
| `WHY…` §2, "The runner is invoking bare `python3` with no venv … the fix was applied to **7 of the 77** call sites" | premise moved | `scripts/ralph.sh:53` and `scripts/project_ralph.sh:38` now state that the launcher prepends `backend/.venv/bin` to PATH, making every bare `python3` the venv. **Not fully verifiable here**: the launcher those comments name (`scripts/instance.sh`) does not exist in the tree, so this is downgraded to *unresolved*, not *stale* — someone must find the real launcher or the comment is a second stale blocker |
| `WHY…` §3, "`check_fresh_clone.sh` … FAIL `no_app_source` 106 tracked files"; "`build_with_zero_apps` ???? never run" | fixed 2026-09-04 | `bash scripts/check_fresh_clone.sh` → PASS `no_app_source`, PASS `build_with_zero_apps`, PASS `shell_boots_with_empty_catalog`. One FAIL remains, `no_hardcoded_identity`: 115 home paths, 101 identity refs, 1 email |
| `WHY…` §6 items 1-3, "a durable wakeup / a clock / a way to notify the operator" listed as missing | all built | `grep -n "WAKE_TOOL_NAME\|NOTIFY_TOOL_NAME" backend/mcp/workshop_query_server.py` → `:227`, `:301`; clock at `backend/services/chat_context.py:1272-1279` |
| `EXECUTION-PLAN-2026-08-27.md` §1 C-3, "`session_defaults.resolve()` and `.choose()` have zero non-test callers" | already annotated stale in the doc itself, 2026-09-04 | left here because the correction sits *below* the original text and the original is what a skimmer reads first |
| `INDEX-open-work-2026-08-26.md` header, "311 tickets total, 159 live" | stale count | `project_tasks.py list-json .` → **211 live** |
| `EXECUTION-PLAN-2026-08-27.md` §2, "19 workstreams, 175 live tickets" | stale count | same command → 211 |

**The pattern in this table is one-directional and it is the opposite of the one
`EXECUTION-PLAN` §4 recorded.** That document found ten docs saying "unbuilt"
where code existed. Ten days later the same error is here in larger numbers, and
in every case the doc is *pessimistic*: it names work as blocked or missing that
has since landed. A pessimistic status doc is more expensive than an optimistic
one, because it makes inaction look like diligence — an agent reads it, agrees,
summarises it, and does nothing, which is `WHY…` §3 exactly.

**The cheapest thing on this whole page is item 1**, and it is not code.
