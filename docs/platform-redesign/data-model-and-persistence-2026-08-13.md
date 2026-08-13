# Data model and persistence: diagnosis and redesign

- Status: **proposed.** Diagnosis measured against the live tree; design
  validated against PostgreSQL multi-tenancy guidance and against two real
  tracker schemas (Linear's documented state model, Plane's readable Django
  schema). Three open questions remain and are listed in §5.7.
- Date: 2026-08-13
- Supersedes on acceptance: the storage half of `entity-model-2026-07-18.md`
  (its vocabulary section is separately revised here, see §3)
- Driver: operator direction, 2026-08-13. The product target is a production,
  multi-tenant application serving many organizations and workflows of any
  type and scale. The current persistence layer cannot serve that target, and
  the vocabulary it encodes has drifted from both the ratified docs and itself.

---

## 1. What is actually stored today

Measured against the live tree on 2026-08-13, not from docstrings.

### 1.1 There is one database and it holds no domain data

`.runtime/auth.db` is SQLite with seven tables: `users`, `refresh_tokens`,
`sessions`, `device_tokens`, `service_tokens`, `domain_owners`,
`sqlite_sequence`. It is **properly designed**: integer primary keys, real
foreign keys with `ON DELETE CASCADE`, a `UNIQUE COLLATE NOCASE` on email.

Nothing else in the platform uses it. Every domain entity is a file.

### 1.2 Every domain entity is a flat file

| entity | storage | live volume |
|---|---|---|
| Tickets | `.tasks.json`, `shared/queue/.tasks.json` | 112 records, 375 KB |
| Runs (jobs) | `.runtime/jobs.jsonl`, append-only | 2,753 records, 951 KB |
| Chats | `.runtime/users/<id>/chat/<thread>/{log.jsonl,meta.json}` | 130 threads, 1,165 messages, 20 MB |
| Notes | `shared/knowledge/*.md`, one file per note | 28 |
| Projects | `.runtime/projects.json` | 7 |
| Build agents | `.runtime/build_agents.json` | 8 |
| Usage ledger | `.runtime/usage.jsonl` | 1,118 lines |
| Events | `.runtime/events.jsonl` | 20 |

The ticket store is a single JSON array. A read parses the whole file; a write
rewrites the whole file atomically under an advisory `flock`.

### 1.3 There is no tenancy on any work data

This is the blocking defect, not the performance one.

Only **personal** data is partitioned per user, and it is partitioned by
directory path: `.runtime/users/<owner_id>/`. Three such directories exist
(users 1, 6, 1003), holding chat, budget, health, location and finance.

**Tickets, runs, projects, agents, usage, events and notes are global files with
no owner in the path at all.** Two users of this platform would share one ticket
list, one job history and one project registry. There is no column, no path
segment and no policy that separates them, because the concept does not exist in
the store.

Isolation of the personal data that *is* partitioned depends entirely on a
Python `contextvar` being set correctly on every request (`services/ownership.py`).
It fails closed if unset, which is the right choice, but it is an application
convention, not a storage boundary. `SEC-A5` in the live queue already records
this: any process running as the same uid reads every owner's files directly.

### 1.4 There is no schema, and no tooling to have one

No SQLAlchemy, no Alembic, no psycopg or asyncpg, in requirements or installed.
The four modules that use SQLite (`auth_store`, `environment`, `birds_store`,
`birds_analyze`) each hand-write their own DDL with the stdlib `sqlite3` module.
There is no shared data-access layer and no migration mechanism anywhere in the
platform.

The consequence is that a "column" is whatever key `tasks._normalize()` happens
to write into a dict. Nothing declares it, nothing constrains it, and nothing
notices when one is abandoned.

### 1.5 A third of the ticket record has never held a value

Occupancy across all 112 tickets ever written:

- **0%**: `parent`, `latitude`, `max_attempts`, `awaiting`, `sync`,
  `external_refs`, `triage`
- **under 5%**: `acceptance` (5), `depends_on` (5), `relates_to` (4),
  `discovered_from` (2)
- **100% but single-valued**: `assignee` is the string `operator` on all 112

Against 35 stored keys, 4 read-side projections (`text`, `owner`,
`blocked_request`, `exec`) and 6 more added by the route layer (`state_type`,
`ready`, `blocked_by`, `readiness`, `links`, `origin`).

The working schema is six fields: `title`, `body`, `status`, `priority`,
`effort`, `execution`.

`parent` at 0% is the sharpest example. Sub-tickets have an ADR, a ratified
depth cap of two, roll-up semantics and a decomposition engine design. The
column has never been written.

---

## 2. Where the code and the ratified model disagree

`entity-model-2026-07-18.md` is marked `accepted` and describes itself as "the
single authoritative reference for every entity in the Workshop platform."
The implementation contradicts it in at least six places.

1. **Priority direction is inverted.** The doc says `priority: int # lower =
   higher priority`. `tasks.next_ready` sorts `-priority`, so higher runs first,
   and the task-proposal card protocol documents it that way too.

2. **Run states.** The doc defines four (`running | done | failed | cancelled`)
   and states explicitly that "a Run does not have a blocked state."
   `services/jobs.py:52` defines six, including `queued` and `awaiting_input`.

3. **`parked` means two different things.** The doc: "operator explicitly
   suspends work (not a failure; not a Block)." `tasks.py:121`: "dead-letter: a
   poison-pill task that exhausted its attempt cap."

4. **Block kinds.** The doc lists `permission | input | external | conflict |
   other`. `tasks.BLOCK_KINDS` is `permission | resource | input | decision |
   missing-info | dependency | environment | other`. Two of the doc's five are
   absent from the code.

5. **Ticket text.** The doc specifies `text: str # first line = title`.
   The store moved to `title` and `body` as canonical stored fields on
   2026-08-07; `text` is now a read-side projection.

6. **Scope is not a field.** The doc specifies `scope: str` on every entity and
   calls it "the access-control boundary." In the implementation scope is
   *which file you are in*: `development` is the repo root, `workshop` is
   `shared/queue/`. It cannot be selected, joined, indexed or changed without
   moving a record between files.

Point 6 is the structural one. It also means the ratified scope vocabulary
("`workshop` -- top-level, **one per installation**") has single-tenancy written
into it at the definitional level.

### 2.1 Two accepted ADRs are already contradicted by the code

**ADR 0001** ("The Task store is the canonical ticket substrate", accepted
2026-07-15) records that a proposal to add separate `title` and `description`
fields "was pressure-tested and rejected: it would create two sources of truth
for the same string ... all cost and no capability." The store shipped exactly
that change on 2026-08-07. `title` and `body` are now the canonical stored
fields and `text` is the projection. The ADR was never superseded or amended.

The same ADR ratifies per-scope `.tasks.json` as the canonical substrate, so
moving tickets into a database supersedes it. That is a required output of this
work, not an incidental one.

**ADR 0016** ("The work surface is named Tickets", accepted 2026-08-12) records
that renaming the code identifiers `task` and `job` is "explicitly closed
twice", citing entity-model §9.5 and `coherence-migration-plan-2026-07-18.md`
§12 ("Do not litigate"). The operator reopened this on 2026-08-13 with a direct
instruction to review every name, term and distinction. That instruction
supersedes the closure; §5.4 acts on it.

The general lesson for §5: this platform has a habit of ratifying a decision in
a document and then diverging from it in code without amending the document.
A schema in a migration file cannot drift this way, because the schema *is* the
artifact. That is a second, independent argument for the change.

---

## 3. Terms that need review

Collected for the redesign; assessed in §5 once the vocabulary audit lands.

- **task vs ticket**: store says `task`, ratified UI word is `ticket`
  (ADR 0016), both appear in the toolbar.
- **job vs run**: store says `job`, ratified word is `run`.
- **owner vs assignee**: two fields, one meaning, one of them constant.
- **source vs scoped_by vs provenance vs discovered_from**: four fields
  answering variants of "where did this come from", three near-empty.
- **evidence vs comments**: two append-only lists of near-identical shape,
  distinguished by whether the author is a machine.
- **note vs learning vs knowledge vs memory**: four words in use for durable
  written state.
- **scope**: used for an access boundary, a storage location, and a routing key.
- **project vs app**: documented as distinct concepts with no bridge between
  them.
- **latitude, touches, grants**: `SEC-A2` in the live queue records that these
  are presented as a security boundary and are advisory in fact.

Project records additionally carry an `autonomy` field that is a dict
(`{"mode": "manual"}`) on one record and null on the other six, and a `dir`
field holding an absolute machine-local filesystem path
(`/home/clawd-a/workshop/projects/<slug>`), which cannot survive a second host
let alone a second tenant.

---

## 4. Why now

Total live domain data: 112 tickets, 1,165 chat messages, 2,753 runs, 130
threads, 28 notes, 7 projects, 1 real user.

A backfill at this size is a single script that runs in seconds. Every month
this is deferred, that script gets longer and the cutover gets riskier, while
none of the reasons to do it change. The abstraction boundary is also already in
the right place: 18 Python modules and every shell worker reach the ticket store
through `services/tasks.py` or its CLI shim, not through the file. Replacing the
backing store behind that module is tractable in a way it will not be if new
direct readers accumulate.

---

## 5. Design

### 5.1 Requirements

Stated by the operator on 2026-08-13 and taken as binding.

**R1. Multi-tenant.** Many organizations on one deployment, isolated from each
other by the storage layer rather than by application convention. A missing
`WHERE` clause must not be able to leak another tenant's data.

**R2. Production-grade.** PostgreSQL, a declared schema, real constraints,
versioned migrations, indexes sized to the access patterns.

**R3. General.** The model must serve workflows of any type and scale, not the
one workflow this installation happens to run. Where the current model has
hard-coded this installation's assumptions (absolute filesystem paths on
project records, `workshop` defined as "one per installation", a single
`operator` assignee) those assumptions come out.

**R4. Normal.** Standard, recognizable structures. A competent engineer joining
this project should recognize the schema from other issue trackers rather than
having to learn a bespoke vocabulary. Where this platform invented a term or a
distinction, it has to justify itself against the standard one or be dropped.

**R5. Fast.** Queries served by indexes. The current list path loads every
record and filters in Python; the substring search over title and body is a
full scan of the whole store.

#### Access patterns the schema must serve

Taken from the live list endpoint (`routes/tasks_all.py:255`), which is the
real consumer:

- filter by status set, by coarse state category, by scope, by readiness
- case-insensitive substring search across title and body
- sort by updated, created, priority, title, status, or scheduler order
  (`priority DESC, created ASC`)
- offset paging with an honest total for the filtered set
- the scheduler's own selection: highest priority whose dependencies are all
  satisfied, optionally narrowed to machine-executable and fully-specified

### 5.2 Non-goals for this phase

- Not rewriting the frontend. The API shape is preserved where it is already
  reasonable; where it changes, it changes because the model changed.
- Not building the sub-ticket, external-tracker-sync or grant-attenuation
  features whose columns currently sit at 0%. The schema makes room for them
  only where doing so costs nothing.
- Not migrating chat message bodies out of per-thread files in the first cut.
  They are the highest-volume data and the least relational; they are sequenced
  after the work model.

### 5.3 Tenancy model

Shared schema, `org_id` on every domain row, Postgres Row-Level Security
enforcing it. This is what AWS calls the "pool" model, and RLS is not optional
in it: there is no logical separation between databases or schemas per tenant,
so the database has to carry the isolation.

Rationale: many small tenants, one deployment, one migration to run. It is the
documented consensus starting point and scales to thousands of tenants.
Schema-per-tenant is the alternative and is the right call for a small number of
large tenants, because it multiplies migration cost by tenant count.

The point of RLS rather than a discipline of always writing `WHERE org_id = ?`
is R1: a forgotten predicate must not be able to leak. Application-level scoping
stays as the first layer; RLS is the second.

#### The two footguns, both of which have bitten real deployments

**1. The table owner bypasses RLS silently.** By default a policy does not apply
to the role that owns the table, and the application usually connects as that
role. `ALTER TABLE ... FORCE ROW LEVEL SECURITY` is required on every
tenant-scoped table, and its absence is documented as the single most common RLS
misconfiguration. Without it the policies are decorative.

**2. Plain `SET` leaks the tenant across pooled connections.** The tenant
context must be transaction-scoped: `SET LOCAL`, or
`set_config('app.current_org', ..., true)` with the third argument true.
A session-scoped `SET` survives the request and the next request on that pooled
connection inherits the previous tenant's context. This is the failure mode that
turns a pooler into a cross-tenant read.

The tenant is carried in a runtime variable, not in a per-tenant database role.
The role-per-tenant variant requires creating a Postgres role for every customer
and is rejected for that reason.

```sql
ALTER TABLE ticket ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket FORCE  ROW LEVEL SECURITY;   -- required, not optional

CREATE POLICY ticket_isolation ON ticket
  USING (org_id = current_setting('app.current_org')::uuid);
-- USING implies a matching WITH CHECK, so writes are covered too.
```

The set-point is one place: the request middleware opens the transaction and
issues `SET LOCAL`. This mirrors the existing `ownership.py` contextvar
discipline, with the enforcement moved from Python into the database.

#### Primary keys

**UUIDv7.** It keeps the 128-bit keyspace and client-side generation of a UUID
while giving index locality close to a sequential bigint, which is the property
UUIDv4 lacks: v4 inserts land at random points in the B-tree and cause bloat.

One constraint the research surfaced that matters here: `uuidv7()` is **native in
PostgreSQL 18**, and the server on this host is **15.17**. So either the target
runs 18, or ids are generated application-side, or an extension supplies it.
Application-side generation is the safest default since it does not pin the
deployment to a Postgres version, and UUIDv7 is designed to be generated by the
client anyway. Decide in P0, not later; the key type is the one thing that is
genuinely painful to change afterwards.

#### Indexes and tenancy

`org_id` leads every index on a tenant-scoped table, because every query carries
it (the RLS predicate guarantees that even when the application forgets). Where
tenant counts grow large, `org_id` is also the natural partition key, but
partitioning is not phase-1 work.

### 5.4 Core schema

**Working draft.** Postgres. Types shown as intent, not final DDL.

#### Tenancy and identity

```
organization   id, slug (unique), name, created_at
user_account   id, email (unique citext), name, created_at
membership     org_id, user_id, role, created_at        PK (org_id, user_id)
```

`user_account` is global and joins organizations through `membership`, because a
person can belong to more than one. This replaces the current `users` table plus
the `.runtime/users/<id>/` directory convention.

#### Work

```
project        id, org_id, slug, name, description,
               archived_at, created_at, updated_at
               UNIQUE (org_id, slug) WHERE deleted_at IS NULL

workflow_state id, org_id, project_id, name, category, position,
               is_default, color
               UNIQUE (org_id, project_id, name) WHERE deleted_at IS NULL

ticket_type    id, org_id, project_id NULL, name, is_default
               UNIQUE (org_id, project_id, name) WHERE deleted_at IS NULL

ticket         id, org_id, project_id,
               number,                    -- per-project, human-facing
               title, body,
               state_id -> workflow_state,
               type_id  -> ticket_type NULL,
               priority, effort,
               parent_id -> ticket NULL,
               sort_order,
               created_by, assignee_id,
               created_at, updated_at, closed_at, archived_at, deleted_at,
               external_source, external_id
               UNIQUE (org_id, project_id, number)

ticket_link    org_id, from_ticket_id, to_ticket_id, kind, created_at, created_by
               UNIQUE (from_ticket_id, to_ticket_id) WHERE deleted_at IS NULL

label          id, org_id, project_id NULL, name, color
               UNIQUE (org_id, project_id, name) WHERE deleted_at IS NULL
ticket_label   ticket_id, label_id         PK (ticket_id, label_id)

comment        id, org_id, ticket_id, author_type, author_id,
               parent_id -> comment NULL,
               body, created_at, edited_at, deleted_at

ticket_event   id, org_id, ticket_id, actor_type, actor_id,
               verb, field, old_value, new_value,
               comment_id -> comment NULL,
               created_at
```

**`workflow_state` is the direct answer to R3.** Tenants define their own state
names; every state carries a `category` from a fixed enum. Integrations and
scheduling reason over the category, humans see the name. This makes "any type of
workflow" a configuration rather than a fork, and the eight hard-coded statuses
in `tasks.py` become seeded rows.

Verified against two real implementations rather than assumed:

- **Linear** uses exactly five: `backlog | unstarted | started | completed |
  canceled`, and scopes workflows to a team. Its own product team runs three
  distinct states inside `started` (In Progress, In Review, Ready to Merge) and
  three inside `canceled`, which is the case that proves the category has to be
  separate from the name.
- **Plane** (open source, schema readable) uses six: the same five plus
  `triage`, and scopes states to a **project**
  (`unique_together: ["name", "project", "deleted_at"]`), with a `sequence`
  ordering float, a `default` flag and an `is_triage` flag.

Decision: five categories, Linear's set, scoped per project. `triage` is left
out because this platform already has `draft` for the same purpose, and adding a
sixth category to serve one state would be inventing a distinction rather than
adopting one.

**`ticket_event` is a field-level audit row, not a generic payload.** My earlier
draft had `action` plus a `jsonb` blob. Plane's `IssueActivity` is
`(verb, field, old_value, new_value, actor, comment)`, and that shape is better:
an activity feed renders directly from it without a per-event parser, and
"who changed priority from 0 to 100" is a query rather than a jsonb dig. Adopted.

It replaces five current fields. `source`, `scoped_by`, `provenance`,
`discovered_from` and `evidence` are all "an actor did a thing at a time". This
is also a new capability: today `update()` overwrites in place with no history
at all, so "who changed the priority" is unrecoverable.

**`comment` stays separate from `ticket_event`**, revising what I said earlier
in chat. Comments are mutable user content with their own lifecycle; events are
immutable history. Plane, GitHub and Linear all keep them apart, and Plane's
comment additionally carries `parent` (threading) and `edited_at`, both of which
are meaningless on an audit row. Collapsing them would force either mutable
events or immutable comments, and both are wrong.

**Relations are a join table AND a parent FK, not one or the other.** Plane
models hierarchy as a self-referencing `parent` FK on the issue, and everything
else as `IssueRelation` rows with a `relation_type`
(`duplicate`, `relates_to`, `blocked_by`, `start_before`, `finish_before`,
`implemented_by`). That validates the split in this draft: `parent_id` is a
column, `depends_on` and `relates_to` become `ticket_link` rows with a `kind`.
Adding a link type is then a value, not a migration.

**External sync is two columns, not a `sync` object.** Plane puts
`external_source` and `external_id` directly on the issue, the state and the
comment. The current Workshop design reserves a `sync` jsonb holding a per-field
shadow for three-way merge, at 0% occupancy. Two nullable columns cost nothing
and cover the case that actually occurs; the shadow-merge design comes back only
if bidirectional sync is genuinely built.

**`ticket_type` resolves the open question about `execution`.** Plane has a
first-class `IssueType`; Jira and GitHub have the same concept. "What kind of
work is this" is a standard tenant-configurable dimension, so
`execution` (code / judgement / external) stops being a bespoke enum column and
becomes seeded rows in `ticket_type`. That is the R4 test applied to this
platform's own invention, and the invention loses.

**Soft delete with partial unique constraints.** Plane carries `deleted_at`
everywhere and writes its unique constraints as
`UNIQUE (...) WHERE deleted_at IS NULL`, so a name can be reused after deletion
without a hard delete losing history. Adopted for the same reason.

#### Execution

```
agent          id, org_id, slug, name, role, autonomy, config jsonb
               UNIQUE (org_id, slug)

run            id, org_id, ticket_id NULL, agent_id NULL,
               trigger, status,
               started_at, ended_at, exit_code, summary,
               claim_token, lease_expires_at, heartbeat_at, worker_pid
               attempt_no
```

**A Run is the attempt.** The nine flat claim and lease columns currently on the
ticket (`claim_token`, `claim_pid`, `lease_until`, `last_heartbeat`, `attempts`,
`job_ids`, ...) move here, where they describe one execution rather than
polluting the durable work item. `ticket.attempts` becomes `count(run)`, which
removes a hand-maintained counter that has already produced one real bug.

`ticket.job_ids` (an ordered array) becomes `run.ticket_id`, a normal
one-to-many.

#### Dropped

`assignee` as a constant, `owner` as a mirror of it, `text` / `blocked_request` /
`exec` as projections, `latitude`, `max_attempts`, `sync`, `external_refs`.
Each is either 0% occupancy, a second name for data already present, or a field
whose consumer was never built. They come back when something needs them, as a
migration.

### 5.5 Vocabulary decisions

**Working draft.** The audit will add cases; these are the ones already
evidenced in §2 and §3.

The governing principle is R4: prefer the word the rest of the industry uses.
This platform has repeatedly ratified a name in a document and then diverged in
code. A migration file cannot drift that way, so the rename lands with the
schema or not at all.

**Renames that land with the schema.** Phase P3 rewrites these call sites
anyway, which is the only cheap moment to do it.

- `task` becomes `ticket`. Two words for one concept, and `ticket` is already
  the ratified user-facing word (ADR 0016). ADR 0016 closed the code rename;
  the operator reopened it on 2026-08-13.
- `job` becomes `run`. Same argument, same ADR.
- `awaiting` becomes `block`, matching the ratified concept name, which the
  entity model already calls Block while the field says `awaiting`.

**Fields that collapse.**

- `owner` and `assignee` become one nullable `assignee_id` FK. Today one is a
  mirror of the other and both are the constant string `operator`, which is a
  single-tenant assumption written into a column.
- `source`, `scoped_by`, `provenance`, `discovered_from` and `evidence` become
  rows in `ticket_event`. Five fields answering "who did what when", three of
  them under 50% occupancy and one free text.
- `text`, `blocked_request` and `exec` disappear. They are projections that
  exist because the store had no other way to offer a second view of its own
  data. A query does that.

**Distinctions to drop.**

- **`parked` as a status.** It currently means two contradictory things: the
  entity model says "operator explicitly suspends work, not a failure",
  `tasks.py:121` says "dead-letter, exhausted its attempt cap". Those are
  different facts about different situations and neither needs a status:
  suspension is a `backlog`-category workflow state, and attempt exhaustion is
  derivable from the run history. If both are still wanted they get two names.
- **`state_type` as a computed projection.** It becomes
  `workflow_state.category`, a real column on a real row, which is what makes
  per-tenant workflows possible at all.
- **`evidence` versus `comments` split by author.** The real distinction is
  mutable user content versus immutable history, not human versus machine. An
  agent can leave a comment; a human action belongs in the event log.

**Left alone deliberately.**

- `effort` and `execution` earn their place. Both have live consumers and
  non-trivial occupancy, and `execution` is the only recently-added field with
  real usage, because it shipped with its consumer.
- `scope` is not renamed here because it is not one concept. It is doing three
  jobs (storage location, access boundary, routing key) and resolving that is
  its own decision, taken in §5.4 by replacing it with `org_id` +
  `project_id` for work data. The note and agent scopes (`agent:`, `role:`,
  `app:`) are sequenced with P6 and are not settled by this document.

#### What the enum audit found

Every enum-like constant in `backend/services/` was enumerated. The result is
worse than the field-level duplication above, and it is one pattern repeated.

**`KINDS` is defined seven times, with seven unrelated meanings.**

```
chat_log.KINDS          message | run | boundary | title | scope
inbox.KINDS             notify | question | review
scope_version.KINDS     note | ticket | run
chat_attachments.KINDS  image | pdf | text
triggers.KINDS          manual | cron | event | discord | loop
jobs.KINDS              ralph | sweep | deploy | deploy-rollback | categorize | reason
semantic_index.KINDS    note | doc | chat | vault
tasks.BLOCK_KINDS       permission | resource | input | decision | ... (8)
```

None of these is wrong on its own. Collectively, `kind` has stopped being a word
and become a placeholder for "a discriminator, you work out for what." In a
schema each of these is a differently-named column
(`message_type`, `inbox_item_type`, `attachment_type`, `trigger`, `run_kind`),
and the naming is forced to be specific because the column has to live next to
its siblings in one table definition.

**Near-duplicate enums that were allowed to diverge.**

- `triggers.KINDS` and `jobs.TRIGGERS` are the same concept: the latter is the
  former minus `discord`.
- `ticket_scope_review.VERDICTS` (`ok | split | underspecified`) and
  `tasks.TRIAGE_DECISIONS` (the same three plus `none`). The docstring at
  `ticket_scope_review.py:36-49` explicitly notes the divergence and argues for
  keeping it, which means this one was seen and accepted rather than missed.
- Three unrelated `source` vocabularies: `latitude.SOURCES`
  (`invocation | task | scope | default`), `todos._VALID_SOURCES`
  (`operator | agent`), `learnings._AUTHORED_SOURCES`
  (`legacy-md | operator | chat`).
- Two verb vocabularies: `permissions._VERBS`
  (`read | list | search | append | write`) and `tasks._VALID_PROPOSAL_VERBS`
  (`read | write | append`).

**A retired axis is still the storage key.** `agents_registry.RUNTIMES` is
`("builder", "assistant")` and it is load-bearing: it decides the record id
(`:472`), gates writes (`:878`) and derives the role (`:201`). Both the repo's
own CLAUDE.md ("Agents are keyed by `role`, NOT by `runtime`") and the entity
model ("Assistant ... never in code identifiers, schema fields, or design docs")
say this axis is gone. It is not gone.

**Durable written state is spread over six modules**: `knowledge.py`,
`learnings.py`, `memory_keeper.py`, `memory_promotion.py`, `memory_tree.py`,
`notes_vault.py`. Four words (note, learning, knowledge, memory) across six
files for "text the system wrote down and wants to read later." Whether these
are genuinely distinct concepts is not answerable from the code today, and
P6 has to answer it before any of them gets a table.

#### Still deferred

`project` versus `app`, and `chat` versus `thread` versus `session`. Both are
P6-scope and neither blocks the work model.

### 5.6 Indexes

Driven by the access patterns in §5.1. `org_id` leads every one of them, because
the RLS predicate puts it in every query whether or not the application wrote it.

**The list view.** `(org_id, project_id, state_id)` and
`(org_id, project_id, updated_at DESC)` cover the default list and its state
filter. The coarse category filter (Active / Backlog / Done) resolves through
`workflow_state.category`, so it is a join rather than a column on the ticket,
which is the cost of making states tenant-configurable and is the right trade.

**The scheduler.** `priority DESC, created ASC` restricted to open tickets is a
partial index:

```sql
CREATE INDEX ticket_queue ON ticket (org_id, project_id, priority DESC, created_at)
  WHERE closed_at IS NULL AND deleted_at IS NULL;
```

Partial because the queue only ever looks at open work, and 63 of the 112 live
tickets are already `done`. That ratio only grows.

**Search.** The current substring match over title and body is a full scan in
Python. In Postgres this is either a GIN index over a `tsvector` (better for word
search, needs a stemming configuration) or a trigram GIN index (better for
partial-word and typo-tolerant match, which is closer to the current substring
behaviour). Trigram is the honest replacement for what exists today; full-text is
the better product. Decide in P1, and it is cheap to change later since neither
is user-visible state.

**Everything else** is covered by the foreign keys: `ticket_label(label_id)`,
`comment(ticket_id, created_at)`, `ticket_event(ticket_id, created_at)`,
`run(ticket_id, started_at DESC)`.

**The readiness predicate is not an index.** "All dependencies satisfied" is a
`NOT EXISTS` over `ticket_link` joined to `workflow_state.category`. It is
currently computed by loading every ticket into Python and iterating; as SQL it
is a correlated subquery the planner can serve from the link table's index. No
new structure is needed, and the per-row readiness check that the list endpoint
now runs 98 times per request stops being an N+1.

### 5.7 Migration plan

#### What makes this tractable

The repository boundary is already in the right place. 18 Python modules and
every shell worker reach tickets through `services/tasks.py` or its CLI shim
`scripts/project_tasks.py`, not through the file. Only two places touch the path
directly, both existence checks inside embedded Python
(`liveness_watchdog.sh:90`, `dispatch_triggers.sh:291`).

So the work is: keep the `services/tasks.py` function signatures, replace their
bodies. Callers do not change in phase 1.

#### Infrastructure that does not exist yet

There is no SQLAlchemy, Alembic, psycopg or asyncpg in requirements or
installed. The four SQLite users each hand-write DDL against the stdlib module.
Phase 0 is therefore real work, not a config change.

A PostgreSQL 15 server is already running on this host on 127.0.0.1:5432 (plus
5433 and 5436), but it is credentialed and belongs to another stack. Workshop
gets its own database and role, or its own container; it does not borrow one.

#### Phases

**P0. Foundation.** Add SQLAlchemy 2.0 + Alembic + psycopg3. Provision a
Workshop database and role. Establish the session/connection lifecycle, the
per-request tenant set-point, and the RLS policy pattern. One trivial table
end-to-end to prove the whole path including migrations.

**P1. Schema.** The §5.4 tables as an Alembic migration, with constraints,
indexes and RLS policies. Seed `workflow_state` with the current eight statuses
mapped onto the five categories. No application code reads it yet.

**P2. Backfill.** A one-shot script: 112 tickets, 2,753 runs, 7 projects, 8
agents, 130 chat threads, 28 notes, 1 user into one seeded organization.
Idempotent and re-runnable. Verified by count and by spot-checking records, not
by the script reporting success.

**P3. Cut over tickets.** Replace the bodies of `services/tasks.py` against the
new tables, preserving signatures. This is the phase where the vocabulary
decisions in §5.5 land, because renaming is free while the call sites are being
touched anyway. Dual-write or read-shadow to compare old and new results before
switching.

**P4. Cut over runs, projects, agents.** Same pattern, smaller.

**P5. Retire the files.** Delete `.tasks.json`, the JSONL ledgers and the
registries. Supersede ADR 0001. Archive `TODO.md` generation or repoint it.

**P6. Chat.** The highest-volume data and the least relational, sequenced last
and separately.

#### Open questions

Four were open in the first draft. Three are now settled by the research; the
numbering mechanism and the note/agent scope remain.

**Settled.**

- *Primary key type* is UUIDv7, generated application-side. See §5.3, including
  the PostgreSQL 15 versus 18 constraint.
- *`execution` as a column* is dropped in favour of `ticket_type`, following
  Plane's `IssueType` and the same concept in Jira and GitHub. See §5.4.
- *Ticket numbering scope* is **per project**, not per organization. Plane's
  `sequence_id` is project-scoped and that is what produces the familiar
  `PROJ-142` handle. The draft had it per-org, which would have made the number
  meaningless once a tenant ran more than one project.

**Still open.**

1. **How the per-project number is allocated.** Scoping it is decided;
   generating it is not. A monotonic counter under concurrent insert is a
   contention point, and a Postgres sequence is global rather than per-project.
   Options: a counter row on `project` taken with `SELECT ... FOR UPDATE`, a
   transaction-level advisory lock keyed on the project id, or a sequence
   created per project. This is worth settling properly in P1 because the
   numbers are user-visible and permanent, so it cannot be changed later.

2. **Where scope lands for notes and agents.** Resolved for work data
   (`org_id` + `project_id`); unresolved for the `agent:`, `role:` and `app:`
   scope kinds, which are sequenced with P6.

3. **Priority as integer or enum.** Workshop uses a signed integer where higher
   wins. Plane uses a five-value string enum (`urgent | high | medium | low |
   none`); Linear uses a small bounded integer. The enum reads better in a UI
   and sorts by a defined order; the integer allows arbitrary ranking. The
   current queue ordering depends on arbitrary integers, so this is a behaviour
   change, not just a type change.

#### What is deliberately NOT in the plan

Migrating the personal stores (health, location, budget, finance) out of
`.runtime/users/<id>/`. They are a separate concern with their own privacy
posture, and folding them in would triple the size of this change. `SEC-A5`
tracks their isolation gap.
