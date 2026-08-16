# Tickets: the write cutover, and what the split it serves still allows

Status: **stage 2 shipped 2026-08-16.** Implementation record for
[`tickets-one-store-2026-08-14.md`](tickets-one-store-2026-08-14.md) and
[ADR 0019](../decisions/0019-work-data-lives-in-postgres.md).

## What was wrong

The stages shipped out of order. Stage 3 (reads from Postgres) landed in
`fcc0ca1`; stage 2 (writes to Postgres) did not. That left the ticket system in
the one configuration the plan never contemplated:

> the authoritative write went to a file that no reader consulted, and the store
> every reader consulted was updated by a best-effort mirror whose failures were
> swallowed by a bare `except: pass`.

This is not a tidiness problem. Two faults fell out of it in a single evening,
both found by using the system rather than by reading it:

1. **A filed ticket that does not exist.** `_persist` wrote `.tasks.json`, then
   called `ticket_sync.mirror`, which returns counts and never raises. A skip
   was indistinguishable from success. The write landed on disk, the reader read
   rows, and the ticket was gone.
2. **A CLI writing to a store nothing reads** (`e7b758a`). `python3
   scripts/project_tasks.py set-status <id> done` printed the updated record and
   exited 0. The file said `done`, Postgres said `todo`, the app showed `todo`.
   Two set-points were missing: an interpreter with sqlalchemy, and a pinned
   data owner. Both failures routed the write to the file, silently.

## Why it violates the permissions split, not just the design

ADR 0019 names the first disqualifying property of the file substrate:

> **There is no tenancy on work data.** ... Tickets, runs, projects, agents,
> usage and notes are global files with no owner anywhere in the path. Two
> tenants would share one ticket list. This is not a scaling concern; it is a
> correctness blocker.

The 2026-08-11 scope-separation audit states the target the platform is built
against: "assume Workshop is a real deployed product where separate end-users
each have their own workshop and must never see or reach anything outside it".
Postgres enforces that with `org_id` on every domain row under Row-Level
Security, chosen so that "a forgotten `WHERE` must not be able to leak across
tenants".

A file has no `WHERE` to forget. `.tasks.json` is one path, same-uid readable,
with no owner or org in it. So while it remained the authoritative write, the
tenancy boundary was enforced on the read side and absent on the write side:
every ticket, for every tenant, went through an unpartitioned file first. The
audit's own §A5 makes the same point about personal data (the partition is
app-layer only, and a Bash `cat` walks around it), and tickets did not even have
the app-layer partition.

That is the sense in which this was a permissions violation and not only debt.

## What shipped

**`services/tasks._persist` branches on the root.** A relational scope writes
Postgres and does not write `.tasks.json`. Every other root (all project trees,
which are still file-backed by design) writes the file exactly as before.
`TODO.md` is rendered either way: it survives as the one-way export, which is
now its only job.

**`db/ticket_sync.mirror` gained a `required` mode** and became the writer for
those roots. In `required` mode it raises `WriteFailed` on any failure,
including the "skipped" outcomes that used to be invisible in the counts. Every
legacy caller keeps the never-raises contract.

**Belonging and reachability are now different questions.**
`tasks.is_relational` answered "these tickets live in Postgres AND the database
is up", which is right for a reader and dangerous for a writer: during an outage
it answers False, the write takes the file path, the database comes back, and
reads resume from rows that never saw it. `tasks.is_relational_scope` is the
static fact, and the writer asks that one and **fails closed**.

**`db/backfill.py --apply` refuses** for a retired scope. Its source is now a
frozen file; re-running it would revert every ticket written since cutover, in
one command. `--force` remains for a deliberate rebuild.

**`db/shadow.py` is deleted.** It compared the two stores, and there is one.
Its last run, immediately before deletion, is the cutover evidence:
`development: 114 in json, 114 in db, 0 differences` and `workshop: 14/14, 0`.

## Evidence

- Live write through the new path as the operator owner: ticket count conserved
  at 113 before and after, the evidence note landed in Postgres, `.tasks.json`
  byte-identical to its pre-write backup (the file was not written), `TODO.md`
  re-rendered.
- Fail-closed path exercised live by making the database unavailable mid-write:
  `WriteFailed` raised, json store mtime unchanged.
- Backup of both stores and the export at `scratch/adr0019-backup/`.
- 2471 backend tests pass. The 2 failures and 82 errors are the DB test modules
  that need `WORKSHOP_DATABASE_ADMIN_URL`, which a plain shell does not load;
  they fail identically before these changes.

## What is deliberately NOT done

**`.tasks.json` is not deleted.** The plan says the last one is left on disk as
a backup artifact and stops being updated, and that it is not deleted in the
same change that stops writing it. It is now frozen. `shared/queue/.tasks.json`
is tracked in git and will stop changing; removing it is a separate commit.

**`reconcile` and `import_markdown` survive.** The plan called for deleting
them, on the assumption that every scope migrates. Project trees are still
file-backed, and for them `TODO.md` healing is live behaviour, not legacy. They
are already inert for relational roots. Deleting them belongs with migrating
project scopes, not before.

## What else violates the same boundary

Measured 2026-08-16. Every one of these is work data with a table already
defined in `db/models.py`, still served from an unpartitioned file:

- **runs / jobs** (`services/jobs.py`): file only. `Run` exists.
- **projects** (`services/projects.py`): file only. `Project` exists.
- **build agents** (`services/build_agents.py`): file only. `Agent` exists.
- **learnings** (`services/learnings.py`): file only.
- **usage ledger** (`services/usage_ledger.py`): file only.
- **notes / knowledge** (`services/knowledge.py`): reaches Postgres.

Tickets were the hard one, because they had two stores at once. The rest have
one store in the wrong place, which is a smaller and better-understood move: the
pattern this cutover establishes (a static `is_relational_scope`, a `required`
write, fail closed, the export rendered from the same call) is what each of them
should follow.
