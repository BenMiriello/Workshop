# Handoff: target-architecture build, second pass (2026-08-17)

- Status: **IN PROGRESS. Three subagents were still running when this was written
  and 88 files were uncommitted in the shared tree.** Read §3 before touching
  anything or running any build command.
- Reads on top of `target-architecture-2026-08-17.md` (the plan) and its build
  log §6. This doc covers only the second pass.

---

## 1. The one thing to know

**Before renaming X to Y, grep for Y.**

The plan called `job -> run` "clean and mechanical, no standard-library or
framework collision." True, and irrelevant. `run_id` is already taken twice
inside this codebase, and the collision is worse than the `asyncio` one that
blocked `task -> ticket`, because both meanings are "a unit of execution" and a
reader cannot disambiguate from context.

The same failure shape then turned up twice more in the same session, in two
different gates (§2.2, §2.3). Each time, the SOURCE of a centralisation was
measured and its DESTINATION was not.

---

## 2. What was found

### 2.1 `job -> run` is blocked, and the unblocker is an unfinished rename

`run_id` means three things:

1. a chat thread's claim for one streaming turn (185 refs: `routes/chat.py` 49,
   `build_streaming.py` 32, `chat_store.py` 23, `db/chat_backfill.py` 19,
   `db/chat_repo.py` 10, `chat_context.py` 8, `chat_log.py` 7)
2. a deploy run (`services/deploy_runner.py`, `DeploysPanel.tsx`, `DeployDetail.tsx`)
3. the Job rename the plan proposes

Meaning 1 is already modelled as a Turn everywhere it counts: the table is
`chat_turn`, the dataclass is `Turn`, the frontend says `turn_id` 60 times
against `run_id` 31, and `db/chat_repo.py:114-124` literally does
`run_id = m.pop("run_id")` then `meta["turn_id"] = run_id`. One value, three
names, one round trip.

So item 1 is **three** renames: chat `run_id -> turn_id` first (frees the word),
then `job -> run`, then `task -> ticket` riding item 3. Recorded in the plan §5.
An agent was mid-way through the first when this was written.

### 2.2 The `response_model` ratchet was measuring the keyword, not the contract

`scripts/check_response_model.py` asked only whether `response_model=` was
present and never looked at its value, so `response_model=dict` satisfied it,
moved the number the right way, and still produced `unknown` in the generated
client, which is the entire thing ADR 0023 clause 6 exists to prevent.

Fixed in `a8ad8c5`. It now judges the value, with the carve-out that
`response_model=None` on a 204/205/304 is correct rather than weak. Proven
against 16 cases.

**A metric that can be satisfied without doing the work is worse than no metric,
because it reports success.**

### 2.3 The raw-storage gate asserted a clean it had not verified

It printed "storage.ts is the only caller of localStorage/sessionStorage" while
eight raw calls sat outside its scan roots: four in `packages/ui/src/theme.ts`
and four in `frontend/index.html`. `packages/ui` is a live frontend dependency
that compiles into the bundle.

Fixed in `de4d628`: scan widened, exemptions listed with a reason each (the kit
cannot import `frontend/src/lib/storage.ts`, and an inline bootstrap script
cannot import at all, so the list has to live in the checker), and the clean
message now states what it scanned instead of making a claim. Proven to fire on
a raw call in `packages/ui/src`, which was previously invisible.

### 2.4 Two of the gates pull in opposite directions

`check_one_definition` wants one declaration per closed enum, which is why
`db/models.py:54,62-63` imports `LATITUDES`, `CHAT_MODES`, `EFFORTS` and
`STATE_TYPES` from `services/*`. `check_module_layout` wants no upward edges,
and those same three imports are 3 of the 42 upward violations. Two of the three
were added deliberately in the previous build, on the precedent of the first.

All four symbols are plain string tuples. The resolution dissolves the tension
rather than trading: put them in `wsplatform/vocabulary.py`, both layers import
DOWN, one declaration survives, both gates go green. **ADR 0024 already
describes this as done. It is not: `wsplatform/vocabulary.py` does not exist.**

Validated before proposing: `check_one_definition` matches by VALUE, not name,
so a shared module plus imports leaves exactly one declaration.

### 2.5 62% of the layering violations are filing, not coupling

| Count | Share | Root cause |
|---|---|---|
| 16 | 38% | genuine coupling |
| 13 | 30% | one-shot CLI misfiled in the data layer |
| 10 | 23% | composition root misfiled as a service |
| 3 | 7% | enum vocabulary with no home below both layers |

- The 23% is one module. `services/app_registry.py` declares `mounts`, the
  FastAPI routers plus their auth, so it references ten routers. That is what a
  composition root does. Filed beside `main.py` those ten edges are legal by
  definition. It imports all ten function-locally, and `llm_router.py:456` says
  outright that the lazy import breaks an `app_registry <-> llm_router` cycle.
- The 30% is seven `db/` modules that have a `__main__` and **zero importers**:
  `repair_branch_heads`, `repair_chain_order`, `chat_list_parity`,
  `prune_synthetic_users`, `backfill`, `chat_verify`, `prune_test_threads`.
  One-shot tools in the data layer's package.
- Four of the 16 genuine ones are **one misplaced function**.
  `routes/tasks_all.py:229` publishes `scope_roots` "because other routers need
  it"; its four consumers are all SERVICES (`chat_work`, `inbox`, `refs`,
  `semantic_index`). `chat_work.py:160` also reaches in for `_with_latitude` and
  `_with_triage`, both underscore-private. `semantic_index.py:408` defends the
  import with "routes -> services is the normal direction" while doing the
  reverse.

### 2.6 T3 removed the concept and left the substrate

`chat_thread.scope`, `chat_thread.scope_locked` and `ix_chat_thread_scope` are
all still on the live schema. The service layer deleted the concept and now
carries four separate comments explaining why the surviving storage must not be
used, including `db/chat_repo.py:813`: "selecting the column would only offer a
second answer that can be stale."

That is worse than keeping it or dropping it. Fix is one migration dropping two
columns and one index, and it should not ride another migration.

**General lesson: retiring a concept has a schema half, and finishing the code
half feels like finishing.**

### 2.7 An independent ADR review found more than this doc can carry

A read-only agent checked ADRs 0023-0026 against each other, against the code,
and against ADRs 0001-0022. The three worst are fixed (`8ccae3f`): 0023 named
the forbidden `platform/` package, 0024's rename amendment reached the tree
diagram and stopped (its layer table would have made a checker report the whole
platform layer as unlayered), and 0026 opened with an emergency that had already
been fixed the same day.

**Still open from that review, in priority order:**

1. **ADR 0021 is silently superseded.** It is accepted, and it argues
   explicitly that the thread-operation registry is in-process memory on
   purpose: "Persisting the flag would recreate the stale-flag class this
   deletes." ADR 0026 clause 6 reverses that without naming 0021, and does not
   list `thread_ops.py` among the globals to move. A superseded ADR that does
   not know it is superseded is a landmine.
2. **ADR 0016 is silently reversed.** It is accepted and says "The code nouns
   stay `task` and `job`." The whole of item 1 is the opposite. None of the four
   new ADRs cites it.
3. **ADR 0025 clause 2 is unsatisfiable as written.** It gates on "no `fetch(`
   in it, only the generated client", and there is no generated *client*, only
   generated *types*. 364 `fetch(` sites, one file importing the schema. The ADR
   gives no interim rule, unlike its own migration posture for the other five
   clauses.
4. **ADR 0026 and 0025 disagree about where the integer user id survives.** 0026
   says one place; 0025's namespacing makes every browser key `ws:<int>:`. The
   storage migration is one-shot and guarded, so retiring the integer would
   orphan every existing bucket with no second run.
5. `check_module_layout` **inverts** `unlayered_module`: unmapped top-level
   directories silently pass, so `main.py`, `deps_auth.py`, `mcp/`,
   `middleware/`, `workshop_public_app.py` are invisible. The ADR requires the
   opposite.
6. `wsplatform/__init__.py` has no `__all__` and every consumer deep-imports
   `wsplatform.http`, violating ADR 0024's own package rule.
7. Roughly 17 stated rules across the four ADRs have no mechanical check. The
   review lists them in priority order. `check_tenant_columns.py` and
   `check_frontend_state.sh` do not exist at all.

### 2.8a Deduplication found a real user-visible bug (agent D, landed `c6be0bd`)

17 declarations to 5. `fmtTs` x3 now call `lib/time.ts` `timeAbsolute`, and
three new pure modules hold the rest (`lib/bytes.ts`, `lib/sha.ts`,
`pages/apps/<slug>/specColor.ts`).

**Two same-named helpers were not the same function, and one was a bug.**
`Memory.tsx`'s `fmtBytes` had no GB tier, so a 2 GiB chat attachment rendered as
`2048.0 MB`. Verified against the pre-change source. The birds copy had the tier;
the `AutonomyControls` copy was a nullable VRAM formatter in mebibytes and was
deliberately kept separate. Separately `fmtAge` took SECONDS in one caller and
MILLISECONDS in the other, and was correctly not folded into `timeAgo` because
one rounds where the other floors.

`scripts/check_duplicate_helpers.py` hashes normalised bodies with the signature
excluded, which is what lets `ts?: string` and `ts?: string | null` match. 10
groups flagged, 10 genuine, zero false positives. **Its stated limit matters:
it catches copy-paste, not independent reimplementation.** None of the three
`fmtTs` copies would have matched `timeAbsolute`, because one used `isNaN` and
the other `Number.isNaN`. A green result is not evidence that no equivalent
exists.

Left in the frozen chat area for later: `useNow` x2, `firstLine` x2, and a bonus
finding, `done` in `components/chat/CopyMessage.tsx:18` is a byte-copy of the
kit's own `packages/ui/src/feedback.tsx:120`, so the chat reimplements a kit
hook. All three are recorded in the baseline as pre-existing.

### 2.8 Smaller, verified

- `jobs.py`, `usage_ledger.py` and `permissions.py` carry **zero** owner
  references, so token spend cannot be attributed to a person. Item 4's premise
  holds.
- Frontend: 364 `fetch(` sites, no server-cache library, 54 pure-module tests
  under `components/chat` + `lib` against **zero** under `pages/`.
- 17 duplicate helper declarations that should be about 5 (`fmtTs` x3 byte
  identical, `fmtBytes` x3, `short(sha)` x3, `specColor` x2, `fmtAge` x2, plus
  `useNow` x2 and `firstLine` x2 inside `components/chat`). `lib/time.ts`
  already exports the canonical `timeAbsolute` and `timeAgo` and is bypassed.
  An agent was mid-way through this when this was written.
- One live ticket sent a worker to `backend/services/chat_repo.py`, which does
  not exist (it is `backend/db/chat_repo.py`). Fixed in the store.
- `.runtime/users/<id>` leak from the previous build is holding: `user_account`
  is 1 row, 0 created today, across many full suite runs.

---

## 3. STATE OF THE TREE, read before doing anything

**Three agents were running when this was written.** They commit their own work,
so their commits may already be present.

- **A: chat `run_id -> turn_id`.** Owns `routes/chat.py`, `services/chat_*.py`,
  `build_streaming.py`, `voice_worker.py`, `thread_ops.py`, `db/chat_repo.py`,
  `db/chat_backfill.py`, `db/models.py`, a new migration, ~14 chat test files,
  `frontend/src/lib/chat.ts`. Was asked to decide the wire-field question itself
  and report the reasoning.
- **B: `response_model` on four routers.** Owns `routes/tasks_all.py`,
  `projects.py`, `agents.py`, `jobs.py`, and a new `routes/api_models.py`. Told
  not to re-pin the baseline. Two open questions were put to it: the
  `autonomy_mode` two-vocabulary defect, and whether its bare-array collection
  routes (`list[Project]`, `list[RosterAgent]`, `list[BuildAgent]`) are
  deliberately grandfathered against ADR 0023 clause 1.
- **D: LANDED** (`c6be0bd`), and its check is now wired (`a05ba32`). 17 helper
  declarations to 5. Frontend tests 945 -> **966 in 64 files**; use 966 as the
  new baseline. Details in §2.8a.

### Do these when they land, in this order

1. **`npm run gen:api-types` and commit `frontend/src/api/schema.gen.ts`.** It
   is stale right now and correctly so: every `response_model` B adds and any
   response-shape change A makes moves the schema. The gate is wired and red
   until this runs. Both agents were explicitly told not to do it. **This is the
   single thing that must not be forgotten.**
2. `python3 scripts/check_response_model.py --write-baseline` (only after 1).
3. DONE: `check_duplicate_helpers.py` is wired (`a05ba32`) and proven to fire.
4. Full gates: backend pytest (**`set -a; . ~/.config/workshop/db-admin.env;
   set +a` first, or 85 tests error spuriously and look like a regression**),
   `npm run typecheck`, `cd frontend && npm test`, `check_design.sh`,
   `check_no_hardcoding.sh`, and all seven ratchets.
5. Baselines to compare against: backend **2,921**, frontend **966 in 64 files**.
6. Leak check: `user_account` must still be 1 row.
7. **`bash scripts/apply.sh` LAST.** Do not run it while agents hold the tree:
   it builds and restarts from disk, and a half-renamed chat subsystem would go
   live.

---

## 4. Next work, in dependency order

1. **`wsplatform/vocabulary.py`** (§2.4). Small, dissolves a live conflict
   between two gates, and ADR 0024 already claims it exists. Touches
   `db/models.py`, `services/latitude.py`, `services/tasks.py`,
   `services/chat_store.py`. Blocked while agent A holds the chat files.
2. **Move the seven one-shot `db/` CLIs out of the data layer** (§2.5). Clears
   ~13 violations by describing the tree honestly. Blocked while A holds
   `db/chat_repo.py` and the repair scripts.
3. **Extract `scope_roots`, `_with_latitude`, `_with_triage` out of
   `routes/tasks_all.py`** into a service (§2.5). Clears 4 violations for one
   move. Blocked while B holds that file.
4. **File `services/app_registry.py` beside `main.py`** (§2.5). Clears 10.
5. **Drop `chat_thread.scope`, `scope_locked`, `ix_chat_thread_scope`** (§2.6).
   Its own migration.
6. Then `job -> run` (now unblocked if A landed), then item 3 with
   `task -> ticket` riding it.
7. Reconcile ADRs 0016 and 0021 with 0023/0026 (§2.7 items 1-2). Cheap, and
   both are landmines for exactly the rename work in item 6.

---

## 5. Traps that cost time this session

- **`WORKSHOP_DATABASE_ADMIN_URL` must be exported** or 85 backend tests error
  and read as a regression. `set -a; . ~/.config/workshop/db-admin.env; set +a`.
- **`tasks.update()` needs a `Path`**; `tasks.list_tasks()` accepts a `str`.
  Passing a str raises `AttributeError: 'str' object has no attribute 'mkdir'`
  four frames down in `_locked`. And verify a ticket write by READING IT BACK:
  a sibling failure mode silently no-ops and returns `{}`.
- **`import main` writes live instance state.** It calls
  `ensure_identity_manifests()`, which rewrites `.runtime/installed-apps.json`
  and reorders the operator's launcher cards. It is gitignored, so nothing shows
  and `git checkout` cannot undo it. `scripts/gen_openapi.py` is the worked
  example of redirecting the instance env vars first.
- **`/tmp` is shared across users on this box.** A predictable name like
  `/tmp/t.txt` collides with another tenant's file. Use `mktemp`.
- **`import fastapi` is not a safe venv probe here** (a partial install in
  `~/.local` imports and then dies), and `sys.executable` compares equal from
  outside the venv because of a symlink. Compare `sys.prefix`.
