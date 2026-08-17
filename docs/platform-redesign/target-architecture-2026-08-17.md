# Target architecture: what to build, ignoring what is there

- Status: ACCEPTED 2026-08-17, in build. The operator approved implementation the
  same day it was written. Build log in §6; that section is the record of what has
  actually landed, and it also records where this document turned out to be wrong.
- Date: 2026-08-17
- Supersedes nothing. Sits above `coherence-overhaul-2026-08-16.md`, which was a
  cleanup; this is the target the cleanup should have been aiming at.
- Evidence: four independent measured audits run 2026-08-17 (backend layering,
  data layer, frontend architecture, HTTP surface). Every number below is measured,
  not estimated.

---

## 0. The finding, in one sentence

**This codebase has already solved every one of its structural problems correctly,
exactly once, and never generalised the solution.**

That is not a figure of speech. It is the same shape in all four audits, and the
numbers are not close:

| Layer | Where it was solved | Where it was not |
|---|---|---|
| Backend modules | `services/commute/` is a package: **0.76** cohesion (share of its coupling that stays internal) | `chat` is 17 flat files, 15,892 lines, **0.27**. `app` is **0.11** |
| Frontend state | `components/chat/` + `lib/`: a real reducer store, 58-98% pure modules, **57 test files** | `pages/` + `components/` (non-chat): **0% pure modules, 0 test files, 22,390 lines** |
| Data partitioning | `.runtime/users/<id>/`: **17** correctly per-owner stores, owner is a literal path component, fails closed | ~30 global singletons with no owner field at all |
| HTTP design | Every place an ADR exists, the API is coherent and the code carries the rationale | **22 ADRs, not one about the HTTP contract**, and that is precisely where 52 distinct envelope keys live |

The last row is the causal mechanism, and it is the most important sentence in this
document: **coherence exists exactly where a decision was written down.** The
incoherence is concentrated in the decisions nobody ever had to make explicitly, so
they got made 39 times independently, once per router.

That tells you what to build. Not a rewrite: the hard parts are already right and
were expensive to get right. What is missing is **a small number of written
contracts, each with a mechanical check**, and then the existing best pattern
applied uniformly.

---

## 1. What is already good, and must survive

Stated first because a proposal that does not say what to keep is a rewrite in
disguise, and a rewrite here would destroy things that took months and real
incidents to learn.

- **The Postgres half is genuinely well built** and is rare: RLS enabled AND forced
  on all 16 tables, an app role that is `NOSUPERUSER NOBYPASSRLS` with
  `assert_unprivileged()` refusing to start otherwise, **composite tenant-carrying
  foreign keys** (`REFERENCES project(id, org_id)`) because RI checks run with row
  security off, structural guards as triggers rather than app code, partial unique
  indexes that respect soft delete, and UUIDv7 keys minted app-side for PG13
  portability.
- **`services/paths.py` and `services/ownership.py`**: in-degree 49 and 44, both
  ~148 lines, near-zero fan-out. Textbook good hubs. `current_owner()` raises rather
  than defaulting to the operator.
- **`chat_log.py`'s event-loop-thread assertion**, which raises `OffLoopWrite` if a
  worker thread tries to write. The strongest concurrency engineering in the tree.
- **The fencing-token / lease protocol in `tasks.py`** (Kleppmann-correct).
- **`packages/ui`**: a real `exports` map, a 126-line API snapshot, and a design
  check that fails on a moved surface. The one boundary in the repo that is
  mechanically enforced, and it holds.
- **`components/chat/threadStore.ts`**, `usePoll.ts`, `streamWire.ts`,
  `transcriptBlocks.ts`, `httpError.ts`. Each is a correct, tested, documented
  solution to exactly the class of problem that is unsolved everywhere else.
- **The comment density.** 20-27% in the large modules, arguing tradeoffs rather
  than restating code. This is why four audits could reconstruct intent at all.

**Design principle for everything below: generalise these, do not replace them.**

---

## 2. The target, layer by layer

### 2.1 One vocabulary, all the way down

Today one concept has two names, and the split runs *inside single directories and
single files*:

```
Postgres table          ticket, ticket_event, ticket_label, ticket_link, ticket_type
Python services/        tasks.py, task_dedup.py, task_scope.py
                        ticket_drafts.py, ticket_format.py, ticket_readiness.py
HTTP                    /tasks
UI route                /tickets/:scope/:id
UI module               lib/tasks.ts, which exports ticketHref()
```

`entity-model-2026-07-18.md` declared the canonical names (Ticket, Run, Chat, Agent)
and then explicitly declined to rename the code "because renaming would break
imports". That was the wrong trade. The rename is a bounded one-time cost;
the translation tax is paid by every reader, every doc, and every agent prompt,
forever, and it compounds as the team grows.

**Target: the code says what the model says.** `ticket` and `run` everywhere:
module names, functions, fields, routes, types.

**Cost, measured:** 1,217 `task` + 334 `job` references in backend source (134 + 79
files), 382 + 234 in the frontend. ~2,167 references, ~200 files. It is mechanical
and it is a single reviewable commit per layer.

**Do this first.** Every other refactor below touches these identifiers, and doing it
after means doing it twice.

### 2.2 Backend: every domain gets a package

The flat `services/` namespace is the single largest structural defect. 116 files,
**22 obvious prefix clusters** (`chat_*` ×11, `ticket_*` ×4, `deploy*` ×4, `app_*` ×4,
`scope_*` ×3, ...), and no directories to match. The consequences are measured:

- **438 function-local imports** against 430 module-scope ones. A near 1:1 ratio.
  These are cycle workarounds: in 22 of the 24 mutual A↔B pairs, exactly one
  direction is a function-local import that someone pushed into a function body to
  escape an `ImportError`.
- **One strongly-connected component of 58 modules**, 27% of the backend. Removing
  every one of the 40 layering violations still leaves a **32-module SCC entirely
  inside `services/`**. So the layering violations are a symptom; the flat namespace
  is the disease.
- **11 modules over 1,200 lines carry 30% of all backend code.** `routes/chat.py` is
  4,096 lines holding **22 distinct concerns, 7 of which have no HTTP surface at
  all** (prompt construction, a compaction summariser with its own LLM call, a ticket
  distiller with a second LLM prompt, the context-window table, the send/claim state
  machine, plan-mode policy, cut-coordinate math).

**Target structure.** Each domain is a package with an `__init__.py` that is its
public API, and nothing outside imports past it:

```
backend/
  domains/
    chat/        # was 17 flat files + 7 concerns buried in routes/chat.py
    tickets/     # was 13 flat files
    agents/
    memory/      # knowledge, learnings, semantic index
    autonomy/
    deploy/      # collapses the TWO parallel deploy stacks
    apps/        # the app runner and registry
    commute/     # already correct, becomes the template
    voice/       # already partly correct
  platform/      # paths, ownership, tenancy, ids, config
  api/           # routers only, thin
  db/            # models, migrations, repositories
```

Rules that make it hold, each mechanically checkable:

1. **A route handler contains no business logic.** Measured today: `birds.py` is 8.9
   lines/route (thin, delegating, correct) while `tasks_all.py` is 102.8 and
   `chat.py` is 83.6. The thin routers are the ones whose logic already lives in
   services. Target every router at the thin shape.
2. **No function-local imports.** They are the visible symptom of a cycle. Target:
   zero SCCs. Achieving this is what the package split is *for*.
3. **Dependencies point one way**: `api → domains → platform → db`. Today there are
   40 upward violations, including `db/models.py` importing three service modules at
   module scope. (Note: two of those, `EFFORTS` and `STATE_TYPES`, I deliberately
   *introduced* on 2026-08-17 to kill a duplicate enum. That was right at the time
   and becomes wrong under this target: the vocabulary should move down into
   `platform/`, which both layers may import.)
4. **A god module is a reviewable event.** No new file over ~800 lines without an ADR
   saying why the concern is genuinely that large.

**Two specific extractions worth calling out.** `services/app_runner.py` is 1,426
lines and 10 modules import it; **only 2 use its actual purpose**. The other 8 import
a process supervisor to read a ~40-line developer-mode boolean. That boolean becomes
its own module in `platform/`. And `birds_analyze.py` is 3,443 lines containing its
own auth system with `password_hash` and `invites` tables for a separate app, two
independent storage-eviction engines, and 1 test file. It is a product, not a module.

### 2.3 Data: one store per entity, Postgres, files are exports

The relational half is good. **The file half is what makes this system single-user**,
and it is not a small residue: `.runtime/jobs.jsonl` (5,517 records, no owner field in
the record schema), `.runtime/usage.jsonl` (2,161 records, keyed by project slug
only), `grants.json`, `resource-grants.json`, `store-policies.json`, `apps.json`,
`projects.json`, `build_agents.json`, `agents/*.md`.

Three consequences follow directly:

- **Authorization state is instance-wide while data is per-owner.** Two users cannot
  have different grants, because the grant files have no user dimension.
- **Billing and audit have no owner.** The token ledger and the run history cannot
  attribute anything to a person.
- **"Operator" is a hardcoded role in the storage layer**, not a row. `workspace_path()`
  returns the shared path for the operator *and* for any unpinned owner, and every
  cron job, background loop and CLI script pins no owner. So all automation writes
  into the operator's knowledge and learnings.

**Target:**

1. **Every entity with more than one writer or any query need lives in Postgres**,
   with `org_id` and an owner column. That means runs, usage, grants, projects,
   agents and app installs become tables. Files remain for exactly two things:
   rendered exports (`TODO.md`) and genuinely per-thread append logs, and neither is
   ever read back as input. (The 2026-08-17 sweep proved the cost of violating that:
   re-importing the rendered export minted 4 duplicate tickets, flipped a status, and
   baked rendered status markers into 24 stored titles.)
2. **One id scheme.** There are **six** today (SQLite integer, UUIDv7, uuid4 hex,
   ULID, `job_<epoch>_<hex>`, and human slugs), with translation joins between every
   pair: `ticket.external_id`, `chat_message.external_id`, `user_account.legacy_user_id`.
   `ticket.job_ids` is a `text[]` of ids living in a JSONL file, which is a foreign
   key with no referential integrity. Target: UUIDv7 surrogate keys everywhere.
3. **A slug is a label, never an identity.** Today a project rename is not a rename,
   it is a fork: it silently orphans the working tree, the ticket store, the knowledge
   layer, learnings, triager and memory-keeper state, `chat_thread.scope` (33 threads
   on `development` alone, plain strings with no FK), 2,161 lines of usage records,
   5,517 lines of job records, and — worst — **the AAD that a project's secrets are
   encrypted under**, so a renamed project's secrets become undecryptable. Put a
   surrogate key behind every slug and make the slug a mutable attribute.
4. **The identity root moves out of SQLite.** Users live in `auth.db` (SQLite,
   integer PK) and are shadowed into Postgres `user_account` (UUIDv7) joined on
   `legacy_user_id`, with a `SECURITY DEFINER` bridge function. One store, one key.

**Urgent, independent of any of this. I verified both of these myself rather than
relaying them.**

`db/models.py` does not describe the live schema's composite tenant FKs. The
`_org_target()` helper that documents the whole mechanism is invoked **once**, at
`models.py:757`, for `chat_thread`; the ticket-side tables declare single-column FKs.
The live database really does carry the composite form:

```
ticket_project_id_org_fkey => FOREIGN KEY (project_id, org_id)
                              REFERENCES project(id, org_id)
ticket_parent_id_org_fkey, ticket_type_id_org_fkey, ticket_state_id_org_fkey: same
```

Running `compare_metadata()` against the live schema right now returns **46 entries,
29 of them removals: 17 `remove_fk`, 8 `remove_constraint`, 4 `remove_index`** (with
17 matching `add_fk` that put back the single-column form). So the next person to run
`alembic revision --autogenerate` gets a migration that **drops every composite tenant
foreign key** and the actor-consistency CHECKs. The single most valuable safety
property in this schema is invisible to the tool that maintains it, and the failure
mode is silent.

Separately: `user_account` holds 3,692 rows, of which **3,691 are synthetic
`@example.test` accounts with no membership**, minted by a test run on 2026-08-14.
They are invisible to the app because RLS requires a shared membership, but they are
in the production table. This is the "tests must not share owner storage" failure
mode, already recorded as a learning, having actually happened.

### 2.4 The API gets a written contract and a generated client

This is the highest-leverage change in the document, because it converts an entire
class of bug from "someone notices on screen" into "the build fails".

Measured today: **415 routes, 4 with `response_model=`** (all in one 165-line module),
**281 with no return annotation at all**. Request bodies are well typed (113 pydantic
models); responses are essentially undescribed. The frontend therefore hand-writes
**528 types**, casts at **232 `.json()` sites of which 54 have no annotation at all**,
and the drift is real and confirmed on 3 of 4 spot-checked endpoints. The worst:
**archiving a chat returns the entire transcript** (a sibling route measured 4.77 MB
of `messages` in a 4.79 MB response and strips it; these three do not), and the
client type gives no hint because it declares two fields the endpoint does not return.

There is also no convention anywhere: **80 collection GETs return 19 bare arrays and
61 envelopes, across 52 distinct top-level key names**, 44 of which are used exactly
once. Ten routes return failures at HTTP 200 in 3 further shapes. Zero exception
handlers. Zero versioning. Three timestamp encodings and 8 field names for "when".

**Target:**

1. **An ADR for the HTTP contract.** One envelope shape for collections, cursor
   pagination with a hard `le=` ceiling, one error model, one timestamp encoding
   (RFC3339 UTC), one id type, status codes that mean what they say. This is the
   single cheapest item here and it is the one that was never written down.
2. **`response_model` on every route**, enforced by a check. This is what makes the
   OpenAPI schema real.
3. **Serve the schema (authenticated) and generate the TypeScript client from it.**
   Delete the 528 hand-written types and the 363 hand-rolled fetch calls. Drift stops
   being a class of bug: it becomes a compile error in CI.
4. **Version the surface** once there is a second consumer that is not built from
   this repo. The mobile WebView is not one; a public API would be.
5. **Fix the addressing collisions.** 12 sites where a static segment shadows a
   path-param slot in the same position (`/agents/{agent_id}` is shadowed by 7 static
   words), so any resource whose id equals one of those words is unreachable. And a
   ticket is currently addressable four ways across four routers.

### 2.5 Frontend: promote the chat store to the general rule

`components/chat/threadStore.ts` already states the thesis in its own header:

> *"ONE store per thread... This replaces five owners of the transcript that were
> concatenated by convention at render time... Every historical reorder, double-bubble
> and eaten-message report was a disagreement between two of the five."*

That is exactly the bug class the rest of the app still has, and it is measurable:
**50 of 188 API paths are fetched from more than one file**, with concrete same-screen
disagreements. `/api/agents/autonomy/status` has three independent owners with three
different refresh policies, one of which fetches once on mount and never again.

The rest of the measurement: **363 fetch sites in 84 files, 66 components fetching
directly, 13% behind a client module. 21 distinct loading/error state shapes. 341
`useState<T|null>(null)` slots where null means loading and empty and failed. 183
writes, 106 hand-written reloaders, and zero cross-component invalidation of any
kind. 40 polling surfaces of which 25 do not pause when the tab is hidden**, on a PWA,
including a 1.2s poll.

**Target:**

1. **Adopt a server-cache library** (TanStack Query is the obvious choice). 363 call
   sites is far past the point where hand-rolling is cheaper. One entity gets one
   cache key and one owner; a write invalidates the key; every consumer updates.
   That deletes the 106 reloaders, the 21 state shapes, and the divergence class
   outright.
2. **Key it on the generated client from §2.4**, so the cache is typed end to end.
3. **One polling policy.** `usePoll` already exists, is tested, and pauses on hidden.
   Finish the migration: 25 sites remain.
4. **Namespace browser storage by user.** All **19 localStorage keys are global to the
   browser profile**, so a second signed-in user on one machine inherits the first
   user's chat drafts, outbox, scroll positions and device tokens. That is a real
   multi-user defect today, not a future one.
5. **Extract the pure rule from pages.** `pages/` and non-chat `components/` are
   **22,390 lines with zero pure modules and zero tests**, which under the repo's
   documented no-DOM-harness policy makes them not merely untested but *untestable
   in principle*. The testable ceiling is 42% of the frontend and only 16% is
   exercised. Extraction is what raises the ceiling, and it is the same move
   `components/chat/` already made.
6. **Split the 22 files over 600 lines** (34% of non-test code). `ProjectDetail.tsx`
   is 1,785 lines, 16 sections, 60 `useState`, 22 fetches.

### 2.6 Tenancy becomes structural

Single-operator today; the operator's stated direction is multi-user. The gap is not
mostly in the schema.

1. **Add the app-layer `org_id` predicate.** `db/engine.py` claims *"the
   application-level `WHERE org_id = ...` stays as the first layer"*. Measured:
   `org_id` appears in `db/repo.py` six times, **all as a parameter to
   `tenant_session()`, never in a SQL predicate**. RLS is currently the *only* layer.
   It is forced and the app role cannot bypass it, so this is not a leak — it is a
   single point of failure with no defence in depth.
2. **Set `app.current_user`.** All ~22 production `tenant_session()` calls pass only
   `org_id`, so three RLS policies can never evaluate true and `user_session()` has
   zero production callers. Three policies were paid for and are inert.
3. **Delete the 33 process-local mutable globals.** This is the hard blocker on
   running more than one uvicorn worker: the live turn registry, the app state map
   (mutated from a worker thread, read from the request thread, with the lock
   guarding a different dict), the voice session maps, two separate PID maps, and
   `chat_store`'s hidden globals-as-function-attributes used as an out-of-band return
   channel. Two workers would silently diverge, and `_recent_hits` documents that it
   depends on single-worker uvicorn.
4. **Right-size the pool.** `create_engine(url, pool_pre_ping=True)` is QueuePool
   defaults (5+10) behind Starlette's 40-worker threadpool. At N users the pool
   exhausts before the database is touched.
5. **One org per user is currently structural**: `workshop_resolve_tenant` ends
   `ORDER BY m.created_at ASC LIMIT 1`.

### 2.7 Repo layout: code and instance state must not share a directory

Four mutable state files live **inside the source tree**: `backend/grants.json`
(tracked in git), `backend/resource-grants.json` (124 KB, actively written),
`backend/apps.json`, `backend/store-policies.json`. Meanwhile `shared/` mixes
knowledge notes (data) with `shared/queue/` (a ticket store), and `.runtime/`,
`projects/` and `build-agents/` are instance state scattered at the repo root.

Target: **one state root, none of it under a source directory, none of it tracked.**
Code is `backend/ frontend/ mobile/ packages/ infra/ docs/ scripts/`; everything
mutable is under a single `var/` (or the existing `.runtime/`), addressed only through
`platform/paths.py`. 126 distinct `WORKSHOP_*` env vars are read across the backend;
that number should fall out of this, since most of them exist to relocate a path that
should have been derived from one root.

---

## 3. What NOT to build

Stated so it stops being rediscovered.

- **Not a rewrite.** Four audits found the hard parts already correct: RLS with
  composite tenant FKs, the fencing protocol, the loop-thread assertion, the recorded
  transcript ordering. Each was learned from a real incident. A rewrite loses them and
  buys nothing this document needs.
- **Not microservices.** The problem is intra-process module boundaries. Packages fix
  that; a network hop between them adds failure modes and fixes nothing.
- **No knowledge graph**, no compaction, no control-flow authoring surface. Already
  decided in `coherence-overhaul-2026-08-16.md` §6, still correct.
- **Not a bespoke state library.** Adopt TanStack Query. The repo has already proven
  it can build the right thing once and not generalise it; a hand-rolled cache would
  be that pattern again.
- **No multi-tenant guardrails beyond what real multi-user needs.** Per CLAUDE.md.
  Tenancy is a data-model property, not a pile of checks.

---

## 4. Sequence

Ordered by dependency, not by value. Each item is independently shippable and leaves
the tree green.

| # | Work | Why here | Rough size |
|---|---|---|---|
| **0** | The four missing ADRs: HTTP contract, module layout, frontend state ownership, identity and tenancy | This is the causal fix. Coherence exists exactly where a decision was written down | days |
| **0b** | Fix `db/models.py` to describe the composite tenant FKs; delete the 3,691 synthetic user rows | Urgent and independent: autogenerate currently drops the best safety property in the schema | hours |
| **1** | One vocabulary: `task→ticket`, `job→run` | Mechanical, touches ~200 files, must precede any restructuring | days |
| **2** | `response_model` on every route + serve the schema + generate the TS client | Unblocks 5; converts drift into a build failure | weeks |
| **3** | Backend domain packages; kill the SCC and the function-local imports | Independent of 2, can run in parallel | weeks |
| **4** | Data: runs, usage, grants, projects, agents into Postgres with owner columns; one id scheme; surrogate keys behind slugs | Longest pole; gates 6 | weeks |
| **5** | Frontend: server-cache library on the generated client; finish `usePoll`; namespace storage by user; extract pure modules | Depends on 2 | weeks |
| **6** | Tenancy: app-layer predicate, `app.current_user`, delete process globals, size the pool | Depends on 4; enables >1 worker and >1 user | weeks |
| **7** | Extend the ratchet with a check per contract | Continuous. Without it, all of the above regrows | ongoing |

**Item 7 is not optional.** This is the fourth document of its kind in this repo. The
three before it described correct targets and the targets decayed, because nothing
mechanically prevented the next contributor from adding the 53rd envelope shape. Each
ADR in item 0 ships with its check:

- HTTP contract → a check that every route has a `response_model` and that every
  collection response matches the envelope
- Module layout → an import-graph check: no cycles, no upward edges, no
  function-local imports, no new file over ~800 lines without an ADR
- Frontend state → a check that no `fetch(` appears outside the generated client
- Tenancy → a check that no new module-level mutable global is introduced, and that
  every new table carries `org_id` with RLS forced

The existing three checks (`doc_status.py`, `check_one_definition.py`,
`field_occupancy.py`) already proved the model works: `check_one_definition` went from
4 duplicated enum value-sets to 0 in a day, and stays there because the reviewer gate
fails on a regression.

---

## 5. Where the sequence was wrong

Recorded here rather than quietly re-planned, because the correction is evidence-based
and the next reader needs the evidence more than the conclusion.

**Item 1 was specified as one rename and it is two, with very different risk.**
Measured 2026-08-17 on source only, excluding `.venv`:

- `job → run`: roughly 758 references (`job` 286, `jobs` 234, `job_id` 169,
  `job_ids` 69). **No standard-library or framework collision.** Clean and
  mechanical.
- `task → ticket`: 153 backend source files and roughly 2,478 `task`/`tasks`
  tokens, but **54 of them are `asyncio` Tasks, not tickets** (`_drive_task`,
  `pump_task`, `_tick_task`, `_speaker_task`, `TaskGroup`). Worst case:
  `create_task` is BOTH `asyncio.create_task` and ticket creation, in the same
  codebase. A mechanical rename corrupts the concurrency code.

So `task → ticket` needs semantic discrimination per site, which is the property
that makes a rename unsafe to run unattended against a system the operator is
using at the time.

**Two things are also out of scope of the rename and were not called out:**

1. **The on-disk name `.tasks.json` must not change.** It is read by the live
   autonomy shell loop (`scripts/ralph.sh`, `cron_agent.sh`,
   `scripts/queue_runner.sh`, `scripts/project_tasks.py`). Renaming the file
   renames it out from under a running worker. §2.3 already decides these files
   become exports; the name goes when the store does, not before.
2. **The wire path `/api/tasks` (52 frontend call sites) belongs to item 2**, not
   item 1. It is a breaking wire change, and item 2 is already rewriting every
   route's contract. Doing it there costs one pass instead of two.

**Revised sequencing, and the reason:** do `job → run` on its own; let
`task → ticket` ride with item 3, because item 3 MOVES those files into
`domains/tickets/` anyway and renaming a file that is already moving is free,
whereas doing it first means touching all 153 files twice. This preserves item 1's
actual argument (do not rename twice) while inverting its conclusion.

---

## 6. Build log

Newest last. Every entry states what was measured, not what was intended.

**Baselines at the start of the build**, so later regressions are attributable:
backend 2,892 passed / 0 failed; frontend 919 tests across 60 files; `npm run
typecheck` clean; `apply.sh --check` in sync.

A trap found immediately, worth knowing before reading any agent's test report:
the backend suite reports **85 errors and 2 failures** unless
`WORKSHOP_DATABASE_ADMIN_URL` is exported from `~/.config/workshop/db-admin.env`.
Those tests ERROR rather than skipping cleanly (`db/engine.py:229`), so a fresh
shell shows 85 regressions that do not exist. Source it before believing a suite
run.

### Landed

- **The transcript leak in §2.4, fixed** (`ca9c0ea`). `GET /threads/{id}` stripped
  `messages` and carried a comment telling the next reader not to put it back;
  PATCH, archive and restore sat three functions below returning the same
  `chat_store` document raw, so closing a chat shipped up to 4.77 MB of transcript.
  Invisible from the screen: `lib/chat.ts` types all four as `ThreadSummary`, which
  has no `messages` field, so the client parsed it and discarded it in silence. One
  helper now, not a comprehension in one of four sites. Seven tests through the
  router, each verified to fail when any single route is reverted.

- **Item 0, all four ADRs** (`279b607`, `074105d`). `0023-the-http-contract`,
  `0024-a-domain-is-a-package`, `0025-one-owner-per-server-entity`,
  `0026-one-identity-root-and-tenancy-is-a-column`. Each ends with the contract for
  the check that enforces it. Three decisions in them are worth knowing because they
  overrule or sharpen this document:
  - `tasks_all.py:400` is **superseded, not generalised**. Its total/page split
    survives as `page.total`; the entity-named `tasks` key is the mechanism that
    produced 52 names and it forfeits a generic `Page<T>` in the generated client.
    The envelope is `{items, page}`.
  - The error model reaches all 348 raise sites **with zero diff**: a registered
    `HTTPException` handler upgrades a plain-string `detail` into `{code, message,
    hint}`. Highest ratio of coverage to diff in the whole plan.
  - §2.4's versioning trigger ("a consumer not built from this repo") is **wrong**
    and was replaced. An Expo native build sitting in an app-store queue IS built
    from this repo and still cannot be redeployed with the server. The trigger is
    "cannot be redeployed in the same commit as the server."

- **Item 0b, the schema drift** (`f032cc4`). `compare_metadata()` went from 46
  entries / 29 removals to **zero in both directions**. 15 composite tenant FKs, 6
  `UNIQUE (id, org_id)` pairs, 2 actor-consistency CHECKs and 4 indexes now declared
  through helpers rather than hand-spelled. It also found something not in this
  document: `chat_message_turn_fkey` and `chat_thread_current_branch_fkey` had lost
  the column list on their `ON DELETE SET NULL`, which on a composite key nulls the
  tenant column too and raises instead of clearing the pointer. Regression test
  fails on any removal directive and skips cleanly with no database.

- **Item 7, three more ratchets** (`e739fd2`), plus a fourth (below). Each measures,
  pins a baseline, and fails only on regression, and each was proven to catch a
  regression before being committed. Corrected numbers, since the audit's were off:
  function-local imports **451** (not 438), upward layering violations **41**,
  mutable process globals **60** (not 33; the audit's figure was illustrative and
  three of its named examples are in fact never mutated). The 58-module SCC and the
  11 files over 1,200 lines reproduced exactly.

- **§2.5 item 3, the polling migration finished** (`3304955` + a follow-up). 17 sites
  converted. Two judgement calls the API exists to force: `birds/LiveFeed.tsx` (1.2s)
  **pauses**, because it is the watching side of a live feed and nobody is looking at
  a hidden tab; `commute/useCommuteNative.ts` and `birds/recorder.tsx`'s analyzer
  probe **continue**, because the first drives a lock-screen Live Activity and a
  "time to leave" notification, and the second is the broadcasting side of that same
  live feed, on a phone whose screen being off is its normal operating state. Pausing
  either would silently break the feature. Everything still on a raw `setInterval` is
  now a clock tick, a bounded retry or a device watchdog, each with a stated reason.

- **§2.5 item 4, storage namespaced by user** (`c4f0e0c`). 14 logical keys across 9
  files now go through one module as `ws:<user_id>:<key>`; 5 device-scoped keys stay
  global by being **listed with a reason**, so namespacing is the default and an
  exemption is a diff someone reviews. Two questions this document did not ask were
  answered rather than assumed. The bootstrap case is unreachable (`Guard` renders
  nothing of `<App/>` until auth resolves, and every storage-touching page is a
  descendant), and is still quarantined in a `ws:anon:` bucket that cannot collide
  with a real account. Pre-existing keys are **moved, not copied**, once, on first
  authenticated load: a copy would reproduce the exact defect being closed, and
  "whichever account signs in first keeps them, once" is strictly narrower than
  today, where every concurrent user clobbers them on every load forever.

- **A live data defect, found while verifying item 0b and not in this document.**
  The test suite was writing users into the production database. `user_account`
  carries no `org_id`, so deleting a test organization cascades to its membership and
  leaves the user; two fixtures deleted only the org, and the third deleted its user
  with an inline `DELETE` the other two never inherited. Measured 3,826 rows, **one**
  a real person, growing 1,271 / 348 / 170 / 2,036 per day because the rate tracks
  how often anyone runs the tests. It grew 99 rows mid-session from this build's own
  agents. Now one guarded helper called by all three fixtures, refusing any address
  outside the reserved `example.test` domain. Proof is the row count, not a green
  test: 56 DB tests now run with the count unchanged. 3,825 rows backed up and
  deleted; the table is 1 row. This is the recorded "tests must not share owner
  storage" lesson recurring one layer down, in Postgres instead of the file stores.

- **Item 2's foundation, the two pieces that reach the whole surface** (`cf9c47e`).
  The error model is registered and every one of the 348 raise sites is now
  compliant without being edited: `GET /api/tasks` unauthenticated returns
  `{"detail": {"code": "unauthorized", "message": "authentication required",
  "hint": null}}`, and a framework-raised 404 and a 422 validation error carry the
  same shape. `Page[T]` exists as one shared generic so the generated client will
  get one `Page<T>`. `health.py` and `location.py` fully converted, taking
  `response_model` from 4 routes to 13.
  The agent also did the thing that makes this real rather than nominal: it found
  every frontend consumer of the old plain-string `detail` (`lib/auth.ts`,
  `AutonomyControls.tsx`, `notes/api.ts`, the birds standalone entry) and updated
  them in the same commit, so the global change did not silently turn error
  messages into `[object Object]`.

- **§2.2's worst-placed code, moved** (`cdc811f`). The developer-mode interlock
  (ADR 0014's repo-write safety control) is out of `app_runner.py`. Actual
  importers of that 1,426-line process supervisor drop from 10 files to 2, both
  genuine users. The `fastapi.HTTPException` raised from a service module became a
  plain `DevModeRefusal` translated at the three route call sites, so `wsplatform`
  has no web-framework dependency. Refusal status and message unchanged, proven by
  the existing gate tests plus three new ones that patch the new module directly,
  so a surviving alias on `app_runner` could not make them pass. It also found a
  ninth importer nobody had counted, inside a shell heredoc in
  `scripts/dispatch_triggers.sh`.

- **The package is `wsplatform/`, not `platform/`** (`4b82a04`, ADR 0024 amended).
  `backend/` is a `sys.path` root, so a package called `platform` shadows the
  stdlib module process-wide and the backend dies before pytest collects a test,
  because `uuid` calls `platform.system()` at import time. The plan's own tree
  diagram named `platform/`; anyone retyping it hits this.

- **`paths.py` moved into the platform layer** (`723132d`), no shim, 76 files in one
  commit. It is the repo's most-depended-on hub (in-degree ~49) and imports nothing
  but `os` and `pathlib`, so it is exactly the leaf ADR 0024 describes.

  **This one is worth reading for the method, not the result.** Teaching the ratchet
  about `wsplatform` immediately exposed an edge it had been blind to
  (`wsplatform.dev_mode -> services.paths`), taking the count 41 to 42. Moving
  `paths` removed that edge and exposed the next one underneath it
  (`wsplatform.paths -> services.ownership`, a function-local import in
  `chat_cwd()` that was same-layer and therefore invisible while both files lived
  in `services/`). Net 42 both times.

  The agent refused to re-pin a worse number and left the gate red rather than
  hide it, which is the correct behaviour and the reason the ratchet is worth
  having. The baseline was then rewritten to 42 deliberately, because **the code
  did not regress, the measurement got more accurate**: 41 was counted with the
  layer invisible, so the two numbers are not comparable. That reasoning is
  recorded in `check_module_layout.py`'s header, next to the table, so the jump
  cannot later read as a silently pinned regression.

  The remaining edge is named with its fix: clearing it means moving `ownership.py`
  as well, and `ownership` has a function-local `from services import auth_store`,
  so those three move together across 135 files or not at all. One at a time is
  whack-a-mole and each step scores zero. **That is the next piece of item 3.**

### The gate now

Four checks were added to `scripts/reviewer.sh` this build, joining the three that
already existed. Three are ratchets against a pinned baseline; one is absolute.

| Check | Enforces | Kind |
|---|---|---|
| `check_module_layout.py` | ADR 0024: function-local imports, SCC size, upward edges, files over 800 lines | ratchet |
| `check_no_process_globals.py` | §2.6 item 3: module-level mutable globals in `backend/` | ratchet |
| `check_response_model.py` | ADR 0023 clause 6, scoped per changed route file | ratchet |
| `check_no_raw_storage.py` | ADR 0025 clause 4: `localStorage`/`sessionStorage` outside one module | **absolute** |

The last one is absolute rather than ratcheted because the tree is already at zero.
A new raw call is not debt getting marginally worse, it is a key a second signed-in
account on the same machine can read, so there is nothing to ratchet toward.

### Not done, and why

- **Items 1 to 6 in their large form.** What landed is item 0, item 0b, item 7, and
  two clauses of item 5. Items 2, 3, 4 and 6 are weeks of work each and have not
  started. The four contracts they are built against now exist, which is the thing
  that had to happen first.
- **`task → ticket`.** Deliberately deferred to item 3, for the reasons measured in
  §5 above. `job → run` is ready to go and is the next mechanical piece.
- **The `hardcoded_identity` reviewer reason fires on the current tree**, from
  `scripts/smoke_chat.sh` (an env-var default) and four test files using
  `agent:masini` as a fixture. Pre-existing, untouched by this build, and it is
  making the gate noisy, so it wants either a fix or a documented exemption.

## 7. The one-line answer

**Write down the four contracts nobody ever wrote down, rename the code to the model
you already ratified, give every domain a package and every entity one store, generate
the API client instead of hand-writing it, and put a check behind each of those so it
cannot rot again.**

Everything else in this document is detail under those five clauses.
