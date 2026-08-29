# Threads, branches and pages

**Status: MODEL OF RECORD as of 2026-08-29.** Supersedes the thread half of
`one-thread-many-senders-2026-08-27.md` (its message half shipped and is
correct), the Agents-as-a-roster model in `primitive-roles-2026-07-18.md`, and
the page structure in `entity-model-2026-07-18.md`. Written after five audits of
the live tree; every count below was measured, not recalled.

The problem this closes: five design generations left their pages in the tree.
The rule from here is that **a change deletes the surface it replaces in the same
commit that adds the new one.** No parallel systems, no "systematize it later".

---

## 1. There is one entity, and it is a Thread

### What is there now

Threads are Postgres rows (`chat_thread`, 214 of them). Agents are markdown
files on disk (`agents/*.md`, four of them). Nothing joins them but a
`context_key` string, used on 2 of 214 threads, and both of those name a slug
with no definition file.

The four declared agents have run **once between them since 2026-07-18**.
Everything that actually does unattended work (176 background job dirs, the
resident queue runner on `development`) appears in Jobs, not in Agents. The
roster and the reality have no overlap at all.

### The model

**A thread is the one entity. Every thread is chattable, always.** There is no
conversion gesture, no "turn this into an agent", and no thread that a person
cannot type into. A thread carries two independent settings:

- **`armed`** — what advances the thread without a person. `off` (default),
  `on-message`, `schedule`, `on-ready-ticket`. This is the **only** field that
  decides whether a thread works unasked.
- **`role`** — optional, nullable. Names a definition (prompt, tools, write
  authority) picked from a list. Null is a perfectly good value: an armed thread
  with no role continues with its own context, which is what "nudge it along"
  means.

**Armed and role are independent, and neither implies the other.** A thread can
be armed with no role, or carry a role and never be armed. This is the ordinary
shape: a scheduled job's trigger and its job definition are two fields
everywhere else, and nobody makes them one checkbox.

"Agent" stops being a kind of object and becomes a description of a thread that
is armed. The word survives in the interface; the second entity does not.

### What this deletes

- `agents/*.md` as a source of live entities. The four inert files are retired.
- The Agents page as a roster. Threads that are armed are a filter on the thread
  list; role definitions get their own page (§2).
- `memory-keeper` specifically. Its own definition body says "the actual work is
  deterministic and is performed by `scripts/memory_keeper_run.sh`". It is a
  cron script wearing an agent costume. The script stays, as a scheduled job in
  Settings; the costume goes. Same for `masini` (its Discord trigger has been
  unroutable since it was written) and `masini-commute` (`role: None`).

---

## 2. A role is a definition, edited in one place

A role is `{ name, prompt, tools, writes }`.

`writes` stays the existing closed enum (`maker | reviewer | triager | keeper |
broker`) because that is what it always was: a **write-authority** set, consumed
by `agent_authority._ROLE_WRITES`. It is a permission, not a job title, and it
stays closed.

`name` is free text the operator picks: Planner, Builder, Researcher, Reviewer,
Orchestrator. That is the layer that was missing, and it is why the existing
five-value enum never felt like it described the work.

Roles live in one global list, plus per-app roles for an app that needs its own.
They are assignable to any thread from the thread and from the thread list. The
existing template/fork machinery (`fork_from_template`, `adopt_group`, three
routes, a full UI) is the closest thing already built and has **never been
exercised** — 0 forks, all four agents carry `template: None`. Assignment
replaces forking: a thread points at a role, it does not copy one.

---

## 3. The word is branch. Worktree is an implementation detail

A branch has exactly one worktree and always has: `worktree_add` creates the
branch, and a partial unique index enforces one live record per
`(project, branch_slug)`. Two words for one thing bought nothing.

**The interface says branch.** The worktree is the directory it lives in, shown
only in a detail view, never as a name. A new branch always gets a worktree; the
operator never picks one and never creates one directly. First write creates it,
named after the work.

Housekeeping this implies: 7 legacy `t-<32 hex>` trees predate the naming fix
(`b82ddd44`) and are exactly what made the branch list unreadable. They get
renamed from their ticket or reaped. And `display_name` is written by the repo,
shaped by the service, and then **dropped on the wire by two of the three routes
that carry it** because their response models do not declare it. That is why
branches showed as hex.

---

## 4. Ticket, branch, thread: what links to what and why

- A **ticket** is the intent. Filed from anywhere.
- A **branch** is the change. Opened *for* a ticket.
- A **thread** is a conversation that happens *on* a branch.

Links, and only these:

- `branch.tickets` — normally one. Many is allowed and deliberately not made
  convenient.
- `thread.branch` — a thread works on one branch.
- **A thread's ticket is its branch's ticket.** There is no direct thread-to-ticket
  ownership link, so there is nothing to keep in sync when three threads work
  the same branch.

The existing `source_kind='chat' / source_ref` stays, as **provenance** (where
this ticket came from), not ownership. Those are different questions and one
field cannot answer both.

**What the link is for**, since it is worth being explicit: it is how the system
knows who is working on what, so a second worker does not pick up the same
ticket, and so "done" can be checked against something. Which brings up the
finding that dwarfs the rest of this document:

> **162 development tickets read `done`. Nothing has ever been merged.** HEAD is
> 136 commits ahead of local `main` and 948 ahead of `origin/main`. Ticket
> `e48268d0` ("a ticket may not read done while its work sits unmerged on a
> branch") names this and nothing enforces it.

A ticket is done when its branch is merged. Until that is enforced, the ticket
board is a list of claims.

---

## 5. The lease is a queue, not a negotiation

The lease is on the **branch**, held by a **thread**. Only the holder writes.

- A holder releases at the end of a turn when nothing else is running. Today
  runs never release; they expire, which is why every lease on this install
  reads expired.
- A thread that wants to write and finds the branch held **queues**. When the
  holder releases, the first waiter takes it.
- The operator's message always takes the branch, says so in one line, and does
  not ask. (Built, `7a4233b3`.)
- Preempt stays human-only. (Built, and correctly enforced: an agent may never
  preempt an agent.)

No agent-to-agent negotiation for the lease. A queue is a mutex with a wait
list, which is the standard answer, and asking-for-the-lease is ceremony on top
of a solved problem. Thread-to-thread send is still needed, but for routing work
and answers between threads, not for this.

**No "primary" or "orchestrator" setting.** The branch row shows who holds the
lease, which is a fact and needs no configuration. If one thread plans and
another builds, their roles already say so. A field the operator has to maintain
that nothing reads is worse than no field.

---

## 6. Pages

Measured surface today: 32 top-level pages, 13 pure redirects, 12 settings rows,
8 fixed tabs per app, and five pairs that answer the same question.

### The three levels

**Global (the dock):** Home, Chats, one tile per app, Settings.

- **Chats is a flat list of threads, newest first.** It is the quick-jump
  surface. No branch headings, no grouping. (Reverted once already; it stays
  reverted.) A branch is a small tag on the row, next to scope and role.
- **Home** keeps the dashboard cards and the rows that are genuinely global:
  Tickets, Memory, Usage, Roles.

**Per app:** Overview, **Work**, Chats, Memory, Files, Settings.

**Work** is the new one and it absorbs four pages. **A branch is a row.**
Collapsed it shows the name, the ticket, and the holder. Expanded it shows the
threads on it and the state of its change. Each thread row carries its own
controls: armed on/off, its role, and whether it holds the lease.

This is the branch-centred view, placed where work actually lives instead of on
top of the global chat list where it did not belong. The global level organises
by recency because that is what a chat list is for; the app level organises by
branch because that is what work is.

**Per thread:** unchanged. The toolbar keeps the scope picker and the branch
pill.

### Retired, each in the commit that replaces it

- `/projects` and `/projects/:slug` (1801 lines). Project is not an entity; that
  was settled on 2026-08-20. The app's Overview and Work replace it.
- `/branches` and `Branches.tsx` (486 lines, imported by nothing).
- `/runs`, `/changes`, `/deploys` as top-level pages: they become the app's Work
  view. `/runs` is already unreachable from nav and its own comment cites a nav
  path that no longer exists.
- The deploy subsystem, which has never run once and is still mounted
  (ticket `726d75a9`).
- `/agents` as a roster. Becomes Home > Roles, plus an armed filter on Chats.

`/marketplace` and `/stores` are held, not retired: they are unreachable from
nav today but belong to the app-loader work (Stage 5) and get decided there.

### Settings: 12 rows to 9

Account, Appearance, Chat, Memory, Roles, Access, Models, **Tools and MCP as one
page** (both answer "what can this instance call"), Apps.

Trackers folds into Apps. The autonomy brake stops being in two places (Settings
and the top of the Deploys page).

---

## 7. Order of work

Each step deletes the old surface in the same change that adds the new one.

1. Thread fields (`armed`, `role`) land **and** the file-agent roster is retired,
   in one change. Not one, then the other.
2. Branch vocabulary and the branch row component.
3. The app Work view absorbs Tickets, Runs, Changes; the top-level pages are
   deleted in that change.
4. `ProjectDetail` and `/projects` are deleted; app Overview absorbs what they
   had.
5. Settings collapses to nine rows.

Prerequisite for any of it, and currently blocking: **`development` is not an app
row.** The `dev_only` migration was never applied and sits on one of six alembic
heads, so the app table has no such column. Until that lands, `development` has
no app place and therefore no Work, Chats or Memory tab, which is the whole
reason its memory does not appear inside it.
