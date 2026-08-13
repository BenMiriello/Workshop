# 0019. Work data lives in PostgreSQL, not in JSON files

- Status: **proposed**
- Date: 2026-08-13
- Supersedes: ADR 0001 (The Task store is the canonical ticket substrate), in
  its storage claim. ADR 0001's other holding, that there is ONE work concept
  rather than a Task/Job/to-do split, is reaffirmed and carried forward.
- Plan: `docs/platform-redesign/data-model-and-persistence-2026-08-13.md`

## Context

ADR 0001 (2026-07-15) ratified per-scope `.tasks.json` files driven by
`services/tasks.py` as the canonical ticket substrate. That was a reasonable
call for a single-operator tool. It is not a persistence layer for the product
this is becoming.

The operator restated the product target directly on 2026-08-13: a production,
multi-tenant application serving many organizations and workflows of any type
and scale. Measured against that target, the current substrate has four
disqualifying properties.

**There is no tenancy on work data.** Only personal stores (chat, health,
location, budget) are partitioned, and they are partitioned by directory path
under `.runtime/users/<owner_id>/`, enforced by a Python contextvar. Tickets,
runs, projects, agents, usage and notes are global files with no owner anywhere
in the path. Two tenants would share one ticket list. This is not a scaling
concern; it is a correctness blocker.

**There is no schema.** A column is whatever key `tasks._normalize()` writes
into a dict. Nothing declares it, constrains it, or notices when one is
abandoned. Thirteen of the 35 ticket keys have never held a value across all 112
records ever written, including `parent`, which has an ADR, a ratified depth cap
and a decomposition design behind it.

**There are no transactions, indexes or queries.** Consistency is one advisory
`flock` plus atomic file replace, which is why the codebase hand-rolls a
monotonic claim fence and a lease/heartbeat protocol. Every filter and sort
loads all records and iterates in Python; the substring search over title and
body is a full scan.

**Documents drift from code.** Six concrete contradictions between
`entity-model-2026-07-18.md` (marked accepted) and the implementation are listed
in the plan, including an inverted priority direction and two different
definitions of the `parked` status. ADR 0001 itself is contradicted: it
explicitly rejected separate `title`/`body` fields as "all cost and no
capability", and the store shipped them on 2026-08-07 without amending the ADR.

## Decision

Work data moves to PostgreSQL with a declared, migrated schema.

- **Shared schema, `org_id` on every domain row, enforced by Row-Level
  Security.** The point of RLS over a discipline of always writing the predicate
  is that a forgotten `WHERE` must not be able to leak across tenants.
- **Tenant-configurable workflow states with a fixed category above them**
  (`backlog | unstarted | started | completed | canceled`). Humans see the
  tenant's own state names; scheduling and integrations reason over the
  category. This is what makes "any type of workflow" configuration rather than
  a fork, and it is what Linear and Jira both do.
- **SQLAlchemy 2.0 + Alembic + psycopg3.** None of these exist in the project
  today; the four SQLite users each hand-write DDL against the stdlib module.
- **The repository boundary stays where it is.** 18 Python modules and every
  shell worker reach tickets through `services/tasks.py` or its CLI shim, not
  through the file. Function signatures are preserved; bodies are replaced.

## Consequences

**Gained.** Tenant isolation enforced by the database. Referential integrity
instead of hand-written dangling-reference checks. Indexed queries instead of
full scans. Versioned migrations, so the schema is the artifact and cannot
silently diverge from a document. A real change history, which does not exist
today at all: `update()` overwrites in place, so "who changed the priority" is
currently unrecoverable.

**Cost.** A dependency on a database process, which the platform did not
previously require. Phase 0 is genuine infrastructure work. The JSON stores are
git-diffable and trivially inspectable and that property is lost; `psql` and an
admin view replace it.

**Timing.** Total live domain data is 112 tickets, 2,753 runs, 1,165 chat
messages, 130 threads, 28 notes, 7 projects and 1 real user. The backfill is one
script that runs in seconds. That cost only grows, and the reasons to do it do
not change.

**Risk.** The cutover is the risk, not the schema. Mitigated by phasing: schema
and backfill land before any read path moves, and the ticket cut-over
read-shadows the old store before switching.

## Alternatives considered

**Stay on JSON files, add a tenant directory level.** Cheapest, and it addresses
only the tenancy defect. Leaves no schema, no transactions, no indexes, and no
history, and every one of those is independently required by the target.

**SQLite instead of PostgreSQL.** Already present, already used by five stores,
zero operational cost. Rejected for a multi-tenant server product: single-writer
concurrency, no RLS, and a migration to Postgres later would be this same
migration done twice.

**Schema-per-tenant.** Stronger isolation, and the standard choice for a small
number of large tenants. Rejected because it multiplies migration cost by tenant
count, and the expected shape here is many small tenants.
