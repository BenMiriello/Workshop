# Target architecture: what to build, ignoring what is there

- Status: PROPOSAL, awaiting operator decision. Nothing here is built.
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

## 5. The one-line answer

**Write down the four contracts nobody ever wrote down, rename the code to the model
you already ratified, give every domain a package and every entity one store, generate
the API client instead of hand-writing it, and put a check behind each of those so it
cannot rot again.**

Everything else in this document is detail under those five clauses.
