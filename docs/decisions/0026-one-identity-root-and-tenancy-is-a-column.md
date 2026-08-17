# 0026. One identity root, one id scheme, and tenancy is a column

- Status: **proposed**
- Date: 2026-08-17
- Depends on: ADR 0019 (work data lives in PostgreSQL) for the store, ADR 0020
  for the tenant-lookup bridge this record proposes to retire, ADR 0023 for the
  id type that crosses the HTTP boundary.
- Plan: `docs/platform-redesign/target-architecture-2026-08-17.md` §2.3 and
  §2.6, items 4 and 6 in the §4 sequence.

## Context

The relational half of this system is genuinely well built. RLS is enabled AND
forced on all 16 tables, the app role is `NOSUPERUSER NOBYPASSRLS` and
`assert_unprivileged()` refuses to start the process otherwise, tenant-carrying
foreign keys are composite (`REFERENCES project(id, org_id)`) because Postgres
runs referential-integrity checks with row security off, and structural guards
are triggers rather than app code. None of that is in question here.

What is in question is everything identity touches outside those 16 tables.

**There are two identity stores.** Users live in `.runtime/auth.db`, SQLite,
integer primary key, driven by `services/auth_store.py`. They are shadowed into
Postgres `user_account` (UUIDv7) and joined back on `legacy_user_id`
(`db/models.py:260`, indexed at `:267`, whose own comment calls it "the bridge
to it during" the migration). Resolving a request's tenant therefore requires a
`SECURITY DEFINER` function, `workshop_resolve_tenant` (migration
`d4e17c9b3a55`), which takes one integer and returns one `(org_id, user_id)`
pair. ADR 0020 documents why that function has to be `SECURITY DEFINER`: the
three identity tables are RLS-protected by policies keyed on the very settings
this lookup produces, so it is a bootstrap cycle and a narrow definer function
is the standard answer. That reasoning is correct. It is also only necessary
because the authoritative key lives in the other store.

**There are six id schemes.** SQLite integer, UUIDv7, uuid4 hex, ULID,
`job_<epoch>_<hex>`, and human slugs, with translation joins between pairs:
`ticket.external_id`, `chat_message.external_id`, `user_account.legacy_user_id`.
`ticket.job_ids` is a `text[]` of ids that live in a JSONL file. That is a
foreign key with no referential integrity, in both directions.

**A slug is being used as an identity.** This is the sharpest problem in the
list and it has already cost data. `chat_thread.scope` is a plain string with no
foreign key; 33 threads sit on `development` alone.

**Ownership is a directory for some things and nothing for others.**
`.runtime/users/<id>/` is a genuinely correct partition: 17 stores, the owner is
a literal path component, and `services/ownership.py:57` has `current_owner()`
raise `OwnerUnset` rather than defaulting. That is the right shape. But roughly
**30 global singletons have no owner field at all**: `.runtime/jobs.jsonl`
(5,517 records), `.runtime/usage.jsonl` (2,161 records, keyed by project slug
only), `grants.json`, `resource-grants.json`, `store-policies.json`,
`apps.json`, `projects.json`, `build_agents.json`.

And `ownership.workspace_path()` (`services/ownership.py:110`) resolves to
`operator_default` when `caller_is_operator() or owner_or_none() is None`. Its
docstring states rule 2 plainly: "No owner pinned means the operator's". Every
cron job, background loop and CLI script pins no owner. So all automation writes
into the operator's knowledge and learnings. The docstring is honest about the
tradeoff and the tradeoff was right for one user; it is a defect the moment
there are two.

**Defence in depth is claimed and does not exist.** `db/engine.py`'s module
docstring ends: *"The application-level `WHERE org_id = ...` stays as the first
layer. RLS is the second one, and it is the one that holds when a query
forgets."* Measured: `org_id` appears in `db/repo.py` six times (lines 237, 249,
253, 254, 299, 310) and every single one is either a function parameter or an
argument to `tenant_session()`. **Never a SQL predicate.** RLS is not the second
layer, it is the only layer.

This is **not a leak.** RLS is forced, the role cannot bypass it, and the
process refuses to start if the role is privileged. It is a single point of
failure with a docstring asserting there are two.

**Three RLS policies are inert.** All ~22 production `tenant_session()` calls
pass only `org_id`, so `app.current_user` is never set. `user_session()` has
**zero production callers**: the only two references in the tree are
`tests/test_db_tenancy.py:185` and a comment inside migration `7bf9ae20fe0d`.
Three policies keyed on `app.current_user` were designed, migrated and paid for,
and can never evaluate true.

**33 module-level mutable globals, and that is the hard blocker on a second
uvicorn worker.** The worst of them:

- `services/build_streaming.py:356`, `TURNS: dict[str, Turn] = {}`, the live turn
  registry, read cross-module from `routes/chat.py:3800` and `:3823`.
- `services/app_runner.py:505`, `_STATE: dict[str, dict]`, mutated from a worker
  thread and read from the request thread, with `_STATE_LOCK` at `:507`
  guarding a different dict.
- Two separate PID maps, and the voice session maps.
- `services/chat_store.py`, `_fold.last_mode` (`:415`) and `_fold.last_summary`
  (`:429`): globals disguised as function attributes, written with
  `# type: ignore[attr-defined]`, used as an out-of-band return channel because
  the value would not fit the tuple. The comment at `:278` says so.
- `services/birds_analyze.py:106-109` documents the whole problem in its own
  comment: *"The backend is a single uvicorn worker, so module state persists
  across requests."*

**The pool is sized for one user.** `db/engine.py:94` is
`create_engine(_url(), pool_pre_ping=True, future=True)`. No `pool_size`, no
`max_overflow`, so QueuePool defaults of 5 + 10, behind Starlette's 40-worker
threadpool for sync endpoints.

**One org per user is structural.** `workshop_resolve_tenant` ends
`ORDER BY m.created_at ASC, m.org_id ASC` with `LIMIT 1`. There is no code path
by which a user acts in their second organization.

## Decision

Seven clauses. They are one decision because every one of them is the same
question, "where does identity live", answered at a different layer.

### 1. One identity root: Postgres, UUIDv7

`user_account` in Postgres becomes authoritative. `.runtime/auth.db` is retired,
`legacy_user_id` is dropped, and `workshop_resolve_tenant` collapses to a
lookup that no longer joins two stores. It may still need to be
`SECURITY DEFINER` for the bootstrap reason ADR 0020 gives, and that is fine:
the definer function is the answer to a real cycle, and this ADR is only
removing the second store, not the cycle.

Migration order, because getting this backwards is how the data gets lost:

1. Postgres becomes the write target for new users and for every credential
   mutation. SQLite keeps receiving writes as a shadow, not as a source.
2. Backfill and reconcile: every `auth.db` row has a `user_account` row and the
   `legacy_user_id` join is total in both directions. **This is where the 3,691
   synthetic `@example.test` rows minted by a test run on 2026-08-14 get
   deleted**, before they can be treated as identity. They are invisible to the
   app only because RLS requires a shared membership; they are still in the
   production table. That is the "tests must not share owner storage" learning,
   having actually happened.
3. Read paths move to Postgres, one at a time, shadow-reading first.
4. SQLite becomes read-only, then is deleted with the bridge column.

The integer id survives one place and one place only: `.runtime/users/<id>/`
directory names, until clause 4 empties them. A rename of 17 store roots is a
migration nobody needs to run twice.

### 2. One id scheme: UUIDv7 surrogate keys

Every entity gets a UUIDv7 primary key, minted app-side (the existing choice,
made for PG13 portability, and it stays). ULID, uuid4 hex and
`job_<epoch>_<hex>` are retired as they migrate. `external_id` columns stay only
as long as an external system genuinely owns the other id, and
`ticket.job_ids` stops being a `text[]` of strings in a file: runs become rows
and the array becomes a real foreign key, per ADR 0019's target and §2.3 clause
1 of the plan.

The `job_<epoch>_<hex>` scheme in particular encodes a timestamp in a string,
which is the one thing UUIDv7 already does correctly and sortably.

### 3. A slug is a label, never an identity

**This is the clause that is currently costing data, so it is the one with
teeth.**

Every entity addressed by a human-readable name gets a UUIDv7 surrogate key.
Every reference in every store points at the surrogate key. The slug becomes a
mutable attribute with a uniqueness constraint, and renaming it is an `UPDATE`
of one column in one row.

Today it is not an update of one column. **A project rename is not a rename, it
is a fork.** It silently orphans, all at once:

- the working tree under `projects/<slug>/`
- the ticket store for that scope
- the knowledge layer and the learnings for that scope
- triager and memory-keeper state
- `chat_thread.scope`, a plain string with no FK; 33 threads on `development`
  alone
- 2,161 usage records keyed by project slug
- 5,517 job records
- and the AAD that the project's secrets are encrypted under

That last one is not recoverable by fixing up rows afterwards. **A renamed
project's secrets become undecryptable**, because the additional authenticated
data the ciphertext was sealed with is the old slug and the code will present
the new one. Every other item on that list is a broken pointer someone can
repair; this one is ciphertext nobody can open.

The migration therefore has a specific order too: mint surrogate keys and
backfill every reference **before** rename becomes an offered operation, and
re-key the secrets under the surrogate id (a read-decrypt-reencrypt pass under
the old AAD, run once, while the slug is still correct) as the first step of
that backfill rather than the last.

Slugs remain in URLs. A URL is a label too, and ADR 0023's addressing rules
govern how a slug resolves to a key at the edge. What changes is that nothing
behind the edge stores one.

### 4. Ownership is a column, not a directory and not a role

Every row that belongs to somebody carries `org_id` and an owner column. That
means the ~30 global singletons become tables: runs, usage, grants, resource
grants, store policies, apps, projects, build agents, the agent roster.

Three consequences of the current shape are the reason, and each is a present
defect rather than a scaling concern:

- **Two users cannot have different grants**, because the grant files have no
  user dimension at all. Authorization state is instance-wide while the data it
  authorizes is per-owner.
- **Billing and audit cannot attribute anything to a person.** The token ledger
  (2,161 usage records) and the run history (5,517 job records) have no owner
  field in the record schema, so "what did this user cost" and "who ran this"
  are not questions the data can answer, at any price.
- **"Operator" is a hardcoded role in the storage layer**, not a row.
  `workspace_path()` returns the shared operator path both for the operator
  AND for any unpinned owner, so every cron job, background loop and CLI script
  writes into the operator's knowledge and learnings.

`.runtime/users/<id>/` is not the model to extend, even though it is correct.
It is correct because the owner is unforgeable in the path, and that property
transfers to a column plus RLS, which additionally gives queries, joins and
constraints. Files remain for exactly two things, per §2.3: rendered exports and
genuinely per-thread append logs, neither of which is ever read back as input.

The `current_owner()` raise-rather-than-default behaviour is kept exactly as it
is, and generalised. `workspace_path()`'s "no owner pinned means the operator's"
rule is the one that goes: an automation pass sets its owner, or it fails.

### 5. The app-layer predicate goes back in, so the docstring becomes true

`db/repo.py` writes `WHERE org_id = :org_id` explicitly, in addition to
`tenant_session()`. RLS stays exactly as it is and stays forced.

This is defence in depth, not belt and braces for its own sake: the two layers
fail differently. A missing app predicate is caught by RLS. A policy that is
subtly wrong, a table added without `FORCE`, a future migration that touches
`pg_policy`, is caught by the predicate. Today neither catches the other because
only one exists, and the docstring at `db/engine.py` asserts that two do, which
is worse than knowing there is one.

And `tenant_session()` starts carrying the acting user, so `app.current_user` is
set on every request. Three policies that can never evaluate true today start
evaluating. `user_session()` either acquires its production caller (the
membership bootstrap it was written for) or it is deleted; a helper with zero
callers and a security purpose is a trap either way.

### 6. No process-local mutable state in a request path

A value that outlives a request and is read by a later one lives in Postgres or
in Redis, not in a module-level dict. This is the clause that unblocks a second
uvicorn worker, and nothing else on this list matters at N users until it lands,
because two workers would silently diverge on all 33 globals.

Named, in the order they should go:

- **`build_streaming.TURNS`** first, because it is read cross-module and it is
  the one whose divergence the operator would see: a turn started on worker A is
  invisible to a cancel or a status read routed to worker B.
- **`app_runner._STATE`**, because it is already a threading bug independent of
  workers. It is mutated from a worker thread and read from the request thread
  while `_STATE_LOCK` guards a different dict.
- **`chat_store._fold.last_mode` and `_fold.last_summary`**, because they are
  not even state, they are a return value smuggled past a function signature.
  The fix is to widen the return type, and it is the smallest item here.
- The two PID maps and the voice session maps, which are process supervision and
  belong wherever the process registry ends up living.
- `birds_analyze._recent_hits` last, because its own comment already documents
  the dependency and the failure mode is a re-warm, not a wrong answer.

`chat_log.py`'s event-loop-thread assertion, which raises `OffLoopWrite` when a
worker thread writes, is the model for how this gets enforced rather than hoped
for. It stays and it is the precedent cited in the check below.

### 7. The pool is sized, and one org per user goes

`create_engine` gets an explicit `pool_size` and `max_overflow` derived from the
threadpool that actually calls it, not the QueuePool defaults of 5 + 10 behind
40 Starlette workers. At N users the pool exhausts before the database is
touched, and the symptom is a timeout that looks like a slow query and is not
one. The number is a configuration value with a comment giving the arithmetic,
because the right value depends on worker count and on Postgres
`max_connections`, and a magic literal here is how it goes stale.

**`workshop_resolve_tenant`'s `LIMIT 1` does not stay.** Resolution takes an
explicit org when the caller has one and returns the membership list when it
does not; the session carries the chosen org. One user in two organizations is
the ordinary case for the product this is becoming, and `ORDER BY created_at
LIMIT 1` silently picks one and gives the user no way to know a second exists.
The UI affordance for switching is out of scope here; removing the structural
impossibility is not.

## This is a data model, not a pile of checks

Stated explicitly because this record is the one most likely to be misread as
licence.

Per CLAUDE.md, Workshop is a single-operator system today and **must not grow
multi-tenant guardrail cruft**: no allowlists, no audit journals, no
mention-required gating, no rate limiting, no confirmation prompts beyond what
one user needs. That rule is unchanged by this ADR and this ADR adds none of
those things.

Everything decided above is a property of where data lives and what shape it
has: a column, a key, a predicate, a pool size, a row instead of a hardcoded
role. Not one clause adds a check on a user's action. The distinction matters
because the two are easy to confuse and only one of them is cheap: **an owner
column is free forever once it exists, and a permission prompt is a tax on every
use.** The plan says it in one line and it is the right line: *tenancy is a
data-model property, not a pile of checks.*

If implementing any clause here starts producing an allowlist or a confirmation
dialog, the implementation has gone wrong, not the rule.

## Consequences

**Gained.** One place a user exists. One kind of id, sortable and generated
anywhere. Renaming a project becomes an ordinary update instead of a data-loss
event that takes the secrets with it. Grants that can differ per user. Billing
and audit that can name a person. A second uvicorn worker, which is currently
impossible, and therefore any vertical scaling at all. Two layers of tenant
isolation where the docstring already promises two.

**Cost.** Clause 4 is the longest pole in the whole target document: roughly 30
files become tables with backfills and read-path cutovers, and `.runtime/*.jsonl`
is git-diffable and trivially greppable in a way a table is not. Clause 6 is
scattered across 33 sites and each needs its own thinking about what the value
actually is. Clause 3's secret re-keying is a one-shot pass that must not be
interrupted halfway, and it must run before anyone renames anything.

**Sequencing, and it is not negotiable.** Clause 3's surrogate keys land before
rename is offered. Clause 1's backfill deletes the 3,691 synthetic rows before
Postgres becomes authoritative. Clause 6 lands before anyone increases the
worker count, and the check below is what makes that safe to forget.

**Related and urgent, decided elsewhere but noted here because it can destroy
what this ADR depends on.** `db/models.py` does not describe the live schema's
composite tenant foreign keys: `_org_target()` is invoked once, at
`models.py:757`, for `chat_thread`, while the live database carries
`ticket_project_id_org_fkey`, `ticket_parent_id_org_fkey`,
`ticket_type_id_org_fkey` and `ticket_state_id_org_fkey` in composite form.
`compare_metadata()` against the live schema returns 46 entries, 29 of them
removals. The next `alembic revision --autogenerate` writes a migration that
**drops every composite tenant foreign key**. That is item 0b in the plan's
sequence and it is hours of work, and every clause here assumes those keys still
exist.

**Not decided here.** Whether the ephemeral cross-request state in clause 6
lands in Postgres or in Redis. Live turn state is written several times a second
and is worthless after the turn ends, which argues Redis; adding a second
datastore is a real cost, which argues Postgres with an unlogged table. Decide
per item, with a measurement, when the first one moves.

**Not re-litigated.** SQLite as the identity store, and directory-based
ownership as the model to extend. ADR 0019 closed the first for work data and
this record closes it for identity. The second is closed above: the partition is
correct and the mechanism does not generalise.

## The check

Two checks, because they run at different times and fail for different reasons.
A sibling agent implements both; this is the contract.

**`scripts/check_no_request_globals.py`.** Static, runs in the reviewer gate.
Walks the AST of `backend/services/`, `backend/routes/`, `backend/db/` and
`backend/domains/` and flags every module-level assignment whose value is a
mutable container (dict, list, set, or a class instance that is not frozen),
excluding names that are `Final`, `frozenset`, tuple, or `UPPER_CASE` bound to
an immutable literal. It also flags **function attributes assigned outside the
function's own body or under a `# type: ignore[attr-defined]`**, which is what
catches the `_fold.last_mode` pattern specifically; that one is invisible to a
plain module-scope scan, and it is on this list because it already happened.

Baseline mechanism, same as ADR 0025's: the 33 existing globals are frozen in a
checked-in list with a one-line reason each, the check fails on any new entrant
and on any baseline entry gaining a new writer, and the list may only shrink.
The reason field is the useful part. It is where "this depends on single-worker
uvicorn" gets written down by the person adding it, rather than discovered by an
audit two years later, which is exactly what `birds_analyze._recent_hits` did
right by accident.

**`scripts/check_tenant_columns.py`.** Static, runs in the reviewer gate.
Over `db/models.py` and the migrations:

1. every table in the domain set declares `org_id`, and its migration declares
   both `ENABLE` and `FORCE ROW LEVEL SECURITY` (this half generalises the
   existing `tests/test_db_rls_coverage.py`, which needs no database)
2. every foreign key between two tenant-scoped tables is composite on
   `(id, org_id)`, which is the property `_org_target()` exists to express and
   currently expresses once
3. every `tenant_session()` call site passes a user as well as an org
4. every query in `db/repo.py` and its successors that reads a tenant-scoped
   table carries an explicit `org_id` predicate, so `db/engine.py`'s docstring
   describes the code
5. no new id column is typed anything other than UUIDv7, and no new column named
   `*_slug` or `*_name` is the target of a foreign key or is stored as a
   reference on another table. This is clause 3, and it is the one worth
   checking mechanically, because the failure it prevents is silent and one of
   its consequences is unrecoverable.

Rule 2 deserves the loudest failure of the five. `compare_metadata()` currently
proposes dropping 17 composite foreign keys, and the single most valuable safety
property in this schema is invisible to the tool that maintains it.
