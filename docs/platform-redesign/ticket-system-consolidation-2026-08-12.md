# Ticket system consolidation

- Status: plan
- Date: 2026-08-12
- Supersedes nothing. Extends `entity-model-2026-07-18.md` (vocabulary) and
  ADR 0016 (surface name).

## 0. The question that started this

"Why are there tickets AND tasks, both buttons in the top bar above the chat?
Why two data models and UIs for what I thought would be the same thing?"

## 1. Diagnosis: there is no second data model

Verified directly, not inferred:

- `TaskPanel.tsx` (the chat "Tasks" drawer) and `Work.tsx` / `TicketDetail.tsx`
  (the Tickets page) both call `/api/tasks`.
- The backend has exactly one task store: `STORE_NAME = ".tasks.json"` in
  `services/tasks.py`. The only other `STORE_NAME` in the codebase is
  `tracker.json` (tracker bindings), which is unrelated.
- There is zero per-chat todo storage. `grep -rl todo frontend/src/components/chat/`
  returns nothing.

Everything that looks like a second model is a projection of the first:
`TODO.md` is a regenerated export, `shared/queue/` is the `workshop` scope's
directory, `services/todos.py` is a dead markdown parser with no endpoints (its
only caller is a one-time migration), `POST /api/backlog` writes the same store.

**The duplication is lexical, not structural.** The store's word is `task`, the
ratified UI word is `ticket` (ADR 0016), and the chat drawer kept the store's
word. Two labels for one row, six pixels apart in one toolbar. A schema audit
cannot see this, which is why several passes over this code missed it.

### The real problems, ranked

1. **Two near-synonymous names on two different KINDS of control.** Corrected
   after review: "Tickets" (`ChatToolbar.tsx:226`) is not a list at all. It is a
   `Menu` holding the direct/orchestrated **mode toggle** plus "File this chat as
   a ticket". "Tasks" (`:258`) is the list drawer. So the toolbar pairs a mode
   control and a list control, under names that are synonyms, with glyphs
   (`Ticket` and `ListTodo`) that are near-identical. The same file records this
   collision being fixed once already: file-as-ticket was folded into the menu
   because it "used to sit right next to this one wearing an almost-identical
   glyph." Merging the two drawers alone would leave the collision standing.
2. **The drawer is an authoring surface where a reference surface belongs.** It
   offers inline editing of eleven fields (title, body, status, priority,
   effort, latitude, assignee, acceptance kind + spec, max_attempts, depends_on,
   relates_to) in a 40vh overlay on top of the conversation.
   **But it is also the only editor for seven of them.** `TicketDetail.saveEdits`
   PATCHes exactly `title`, `body`, `status`, `latitude`; priority, effort,
   acceptance, max_attempts, assignee, depends_on and relates_to are read-only
   on the page. Making the drawer read-only without first extending the page
   deletes the capability outright.
3. **The inbox is functionally empty and has been since July**: 3 open items,
   all stale demo seeds dated 2026-07-31, 0 live items from either producer.
4. **Its "more in other scopes" link is dead.** It navigates to
   `/work?tab=inbox`; `parseWorkTab` silently falls back to `tickets`. The
   backend global inbox already exists (`list_items(chat_scope=None)`). Only the
   page was never built.
5. **Live bug**: approving a memory-promotion review posts `"approved"`; the
   backend compares against `"approve"`, so the item closes as answered and
   nothing is written.
6. **Status has one level.** Eight named statuses with no category above them,
   so every consumer (board lanes, filters, any future tracker sync) invents its
   own grouping. I invented one yesterday.

## 2. What the field converged on

Researched across GitHub Issues, Linear, Jira, Shortcut, Asana, plus Beads and
Backlog.md for the agent-first tier. The findings that change this design:

- **Two-level status is the convention.** Linear (6 types), Jira
  (`statusCategory`, 3, deliberately not customizable), Shortcut (4), GitHub
  (`state` + `state_reason`). Only Asana lacks it, and Asana is the outlier.
  Teams rename states; integrations reason over the fixed category.
- **Outcome belongs in the terminal state, not a parallel field.** Jira's
  `resolution` axis is the one clear anti-pattern in the survey: an item in a
  Done status with an empty resolution counts as unresolved forever.
- **`ready` is a computed predicate, never a stored status.** Beads defines it
  as: unstarted AND all blockers closed AND parent closed AND not deferred. A
  stored "Ready" goes stale the moment a blocker closes.
- **Triage is a gate with exactly four actions**: accept, decline with reason,
  mark duplicate, snooze. Items in triage are excluded from other views.
- **Keep statuses few.** Atlassian's own guidance; a community thread documents
  40-status workflows being clicked through as fast as possible. Linear enforces
  it structurally: you cannot invent a category.
- **Scope tightly, write short.** Empirical (arXiv 2512.21426, 3,180 issues):
  well-scoped is the largest positive predictor of an agent PR merging (+16.4%),
  and *longer descriptions reduce merge likelihood ~9%*. This contradicts the
  instinct to over-specify for an agent.
- **Agent writes carry rationale and confidence.** GitHub's 2026 agent
  automation controls: high confidence applies, lower confidence queues as a
  suggestion. This is the one governance mechanism that is not multi-tenant
  cruft, because it is about trusting a non-human, not about a second user.
- **The convergent default view is a list**: "Active" (unstarted + started) and
  "Backlog". Not a board. Linear ships no calendar and no issue-level timeline.

### The finding that decides the chat surface

**For a single operator, every notification reason that encodes your own
participation carries zero information.** GitHub's fifteen `reason` values are a
subscription mechanism for a multi-person repo; four of them are literally your
own activity. With one human, three classes survive:

1. a non-human actor did something (an agent finished, got stuck, needs input),
2. a timer you set for yourself,
3. outside parties, which for a solo operator is near-empty.

So the inbox is **not** redundant with the ticket list. They answer different
questions: *what needs me* versus *what exists*. The thing that is redundant is
the second ticket list.

## 3. The design

### 3.1 Two-level status (backend, projection only)

Keep all eight named statuses. Add a derived `state_type`:

| status | state_type |
|---|---|
| `draft` | `backlog` |
| `parked` | `backlog` |
| `todo` | `unstarted` |
| `doing` | `started` |
| `in_review` | `started` |
| `blocked` | `started` |
| `done` | `completed` |
| `cancelled` | `canceled` |

`blocked` is `started` because the work has begun and is stuck; that is how
Linear and Shortcut model it. `parked` is `backlog` because it is out of the
active plan but not finished.

This maps losslessly onto Linear and Shortcut, onto Jira's three categories
(backlog+unstarted → To Do, started → In Progress, completed+canceled → Done),
and onto GitHub's `state` + `state_reason`. It replaces the four ad-hoc board
lanes invented on 2026-08-11.

**It must be computed in the route layer, not in `_normalize`.** `tasks.py`
strips only `_PROJECTED_KEYS = ("exec", "blocked_request")` before writing, so a
derived field added to the record would be **persisted into `.tasks.json`** and
become a second source of truth, which is the exact failure that docstring
exists to prevent.

**`parked` needs a companion affordance, not just a bucket.** Parked is this
codebase's dead-letter (attempt cap exhausted) and is explicitly "still open
work the operator sees". Putting it in `backlog` is correct by the convention,
but combined with the Active default (3.6) it would hide a ticket that just
burned three runs. So parked joins drafts in the persistent count strip above
the list. The existing lane comment ("blocked and parked are work that has not
happened yet and still needs the operator") was right about the need and wrong
only about the mechanism.

Spelling note: `canceled` (one L) for the *type*, matching every tracker's API,
while the existing status value stays `cancelled` (two Ls) because it is
persisted in 193 live records and renaming it buys nothing.

### 3.2 `ready` as a computed predicate

Expose what only the scheduler can currently compute:

- `ready: bool` on every list row and the detail record.
- `blocked_by: [{id, scope, title, status}]` so the UI can say *why* not.
- `?ready=true` filter, and a `queue` sort key ordering by
  `(-priority, created ASC)` to match what the scheduler would pick.

**`ready` is a PURE predicate over stored state. It must not call `reclaim()`.**
Corrected after review, and this was nearly a real bug: `next_ready` calls
`reconcile()` *and* `reclaim()`, and `reclaim` rewrites `doing` → `todo` or
→ `parked` at the attempt cap, then persists. It has exactly two callers, the
scheduler and the liveness watchdog, and is deliberately absent from every HTTP
path. `TaskPanel` polls the list every 6s while anything is `doing`. Wiring
`reclaim` into a GET would dead-letter live work simply because a ticket list
was left open in a tab.

The honest consequence, stated rather than papered over: **`ready` lags a
crashed claim by one watchdog interval.** A ticket whose sweep died still reads
`doing` until the watchdog reclaims it.

**Cross-scope dependencies stay invisible to the scheduler, and `ready` matches
the scheduler rather than being more correct than it.** `next_ready` builds
`by_id` from one root, so `if d is not None` treats an unresolvable (cross-scope)
dependency as satisfied. `blocked_by` resolves across scopes for display, so a
row can legitimately show `ready: true` with a non-empty `blocked_by`. Making
the predicate honour cross-scope deps would change which tickets the sweep picks
up, which is out of scope here. Recorded as a known gap.

`blocked_by` is mostly built already: `_resolve_links` returns cross-scope
resolved `depends_on` on the detail record. Per-row in a list it is
O(rows × scopes) store loads, so the list path builds **one index per request**.

### 3.3 Triage completes its four actions

Workshop has accept (`/approve`) and decline (cancel). Add:

- **Mark duplicate**: link to the canonical ticket via `relates_to` and cancel,
  recording the merge in `evidence`. The dedup machinery already exists in
  `task_dedup`; this is the manual entry point it never had.
- **Decline with reason**: cancel already exists, but the reason is not
  required. Capture it into `evidence`, because an unexplained cancel is
  indistinguishable from a mistake three weeks later.

**Snooze is deliberately omitted for tickets** and implemented only for inbox
items (3.4). `parked` and `draft` already express "not now" for a ticket, and a
third deferral concept would be a stored status competing with a computed one.

### 3.4 The inbox becomes what it is for

- **Scope it to non-human actors.** Document the rule, and add the missing
  producer: an autonomous run that finishes or fails without anyone watching
  should raise a `notify`. Today only blocked-ticket questions and memory
  promotions produce items, which is why the surface is empty.
- **Snooze with auto-unsnooze on activity** (Linear's primitive, and the one
  that makes deferral safe): `snoozed_until` on the item, hidden while snoozed,
  woken early if the underlying ticket changes.
- **Fix the approve bug.** The wrong string is in the FRONTEND
  (`InboxStrip.tsx:273` posts `"approved"`; `routes/inbox.py:171` compares
  `== "approve"`). Fix the literal and make the backend comparison tolerant, so
  the pin can be a backend test rather than a test over a `.tsx` string.
- Snooze must filter inside `list_items`, not in the panel: `counts()` is built
  by calling `list_items`, and the chat badge reads it. A client-side filter
  would leave the badge counting rows the panel refuses to show.
- **Build the global inbox** at `/work?tab=inbox`, which the existing footer
  link already points at. The backend is already there
  (`list_items(chat_scope=None)`), so this is independent of the rest of 3.4.

### 3.5 The chat toolbar: one work drawer, and the mode control moves

Two changes, because the collision is between a mode control and a list
control (1.1), not between two lists:

- **The mode toggle and "File this chat as a ticket" move into the existing
  overflow menu** (`MoreVertical`), alongside All chats / Open full screen /
  Delete. Chat mode is a property of the conversation, not a ticket surface.
  That frees the word "Tickets" for the one control that lists tickets.
- **The bell and the Tasks drawer merge into one Tickets drawer.** One badge,
  counting **needs-you items only** (an open-ticket count is ambient information
  wearing an alarm's clothing). Two zones: **Needs you** (inbox items for this
  scope, with their answer/approve verbs) and **Active here** (a compact,
  read-only list of `unstarted`+`started` tickets, each a link, plus "Open all"
  to `/work?scope=`).

**`TaskPanel` is left alone.** Corrected after review: `AgentDetail.tsx:1058`
renders it as a section for agent and build scopes, and making the shared
component read-only would strip create, delete, Approve, the blocked review
verbs and the awaiting-answer box from that page too. The chat gets a new,
small, read-only component instead; `TaskPanel` keeps serving AgentDetail
unchanged.

**Prerequisite: `TicketDetail` must first gain the seven fields it cannot edit**
(priority, effort, assignee, acceptance, max_attempts, depends_on, relates_to).
Until that ships, the chat drawer is the only place several of them can be set.

**Deferred: message overflow → "File as ticket".** `MessageList.tsx` has no
per-message menu, overflow or hover affordance at all today, so this is a new
affordance built from nothing, and that file carries a documented memo boundary
where any non-stable prop silently reintroduces the mobile typing lag. Worth
doing, not worth bundling into this pass. Also corrected: the current filing
path is not blunt. `TicketDraftsDialog` already scans the conversation and
proposes 0..N self-contained drafts for review, and itself replaced a button
that squashed the last twelve messages into one paragraph. The real gap is
per-message granularity, not bluntness.

**Explicitly not built: attaching a ticket to a chat.** Proposed on 2026-08-12
and rejected by the operator the same day: tickets and chats are separate
things, and keeping work out of the conversation is the point of the system. A
chat may do work directly (Direct mode) or file work for agents to pick up. It
does not become a session bound to a ticket.

### 3.6 Views default to Active

The Open tab defaults to `state_type IN (unstarted, started)` rather than "All
statuses", matching the convergent default. Backlog and Done are one filter
change away. The board re-lanes onto the five state types.

### 3.7 Agents: not done in this pass, and here is why

The intent was a `managed: true` flag marking the core ticket agents
(scoping, refining, reviewing, completing) as platform-owned, cloneable but not
editable. **The distinction has no referent yet.** The registry holds exactly
three workspace agents (`<bridge-agent>` the Discord bridge, `<bridge-agent>-commute`,
`memory-keeper`) plus per-project builders, all of which are the operator's own.
There is **no registered reviewer, scoping or refining agent anywhere**: that
pipeline is `scripts/project_ralph.sh`, `scripts/reviewer.sh`,
`scripts/triager_run.sh` and the `work-reviewer` native subagent, none of which
are roster members. `agents_registry.native_subagents()` reads the last one
explicitly read-only and deliberately not as a roster entry.

Adding the flag now would mark the operator's own project builders and Discord
bridge as platform-managed and would mark nothing that scopes, reviews or
completes a ticket. That is worse than leaving it alone. Also worth knowing
before anyone tries: "clone" exists only for **templates**
(`fork_from_template`), not for a live agent, so it is a new function rather
than a reuse.

The prerequisite is registering the pipeline steps as agents at all, which is a
real design question about what an Agent is, and the operator asked for tickets
this pass. Recorded so the next attempt starts from the finding rather than the
intent.

## 4. Deliberately omitted

Each of these was considered and rejected with a reason, so they are not
re-proposed later:

- **A resolution field.** Jira's is the only one in the survey and it is the
  documented trap.
- **A stored "ready" status.** Goes stale the moment a blocker closes.
- **Ticket snooze.** Duplicates `parked`/`draft`.
- **WIP limits.** The Kanban Guide deleted the mandate in its 2025 edition;
  GitHub shipped them as a non-blocking red number. With one operator, WIP is
  bounded by concurrency anyway.
- **Story points / estimates.** Two of five trackers have them natively and the
  inventor disowned them. `effort` (quick/standard/deep) already covers the size
  signal.
- **Custom fields.** Linear has none by design and is the most opinionated
  product in the set.
- **Calendar, timeline, gantt.** Linear ships none of them for issues.
- **Notification categories or per-event settings.** Jira's took 21 years and
  1,574 votes to partially repair. With one human, subscription is the filter.
- **Required fields beyond title.** Readiness stays a checklist, never a veto.
  This was already measured here: 0 of 40 open tickets passed a full gate.
- **Attaching a ticket to a chat.** See 3.5.

## 5. Work breakdown

| # | Stream | Files |
|---|---|---|
| W1 | `state_type` + `ready` + `blocked_by` + `queue` sort, all route-layer | `services/tasks.py`, `routes/tasks_all.py` |
| W2 | Triage: mark-duplicate + decline-with-reason | `routes/tasks_all.py` |
| W3 | Inbox: snooze in `list_items`, approve fix, run-finished producer | `services/inbox.py`, `routes/inbox.py`, `chat/InboxStrip.tsx` |
| W4 | Tickets page: lanes on `state_type`, Active default, ready filter, parked strip | `pages/Work.tsx`, `lib/ticketGroups.ts` |
| W5 | Global inbox tab at `/work?tab=inbox` | `pages/Work.tsx`, `lib/workParams.ts` + its test |
| W6 | Chat: new compact drawer, toolbar recomposition | `chat/ChatToolbar.tsx`, `Chat.tsx`, new `chat/WorkDrawer.tsx` |
| W7 | `TicketDetail` gains the 7 uneditable fields | `pages/TicketDetail.tsx` |

Order, corrected after review:

- **W1 → W2** (same file, serial, not independent).
- **W1 → W4.**
- **W7 → W6** (the chat must not lose the only editor before the page gains it).
- **W3 → W6** (the drawer renders inbox rows).
- **W5 is independent of W3**: the global list endpoint already exists.
- W5 must update `lib/workParams.test.ts`, which currently *pins* `"inbox"` as
  an invalid tab and will go red otherwise.

Dropped: message overflow (deferred, 3.5) and the agents managed flag
(no referent, 3.7).

Three status groupings must end up consistent: `STATUS_ORDER` and `LANE_OF` in
`lib/ticketGroups.ts`, and `_STATUS_RANK` in `tasks.py` (the backend
`sort=status` rank). W1 and W4 own them jointly.

## 6. Acceptance

- `state_type` present on every list row and detail record; five values only,
  and **absent from `.tasks.json` on disk** after a write.
- `?ready=true` returns only tickets the scheduler could pick, using the same
  predicate; `sort=queue` matches its ordering. No GET calls `reclaim`.
- A cross-scope blocker appears in `blocked_by` with its own scope. A row may
  show `ready: true` alongside it; that is the documented scheduler gap.
- Inbox approve on a review actually promotes the note, pinned by a backend test.
- Snoozed items are excluded from both the list and the badge count.
- `/work?tab=inbox` renders the global inbox; the chat footer link reaches it.
- The chat has ONE work button whose badge counts needs-you items, and the mode
  toggle is no longer under a control named "Tickets".
- `AgentDetail`'s task section is unchanged.
- Every field the chat drawer could edit is editable on `TicketDetail`.
- `npm run typecheck`, `check_design.sh`, backend and frontend suites all clean.
