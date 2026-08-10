# Chat scoping, re-scope, and cross-chat access (2026-07-18)

> **STATUS: DESIGN. NOT BUILT.**  
> Owns items B6 (chat scoping) and B7 (cross-chat access) from the
> orchestration-surface task.  
> Depends on: T0 entity model (`entity-model-2026-07-18.md`).  
> Grounded in: first and second addenda
> (`orchestration-surface-requirements-addendum-re-s-19f74360f21.md`,
> `orchestration-surface-requirements-addendum-re-s-19f743cb2c2.md`)
> and the verbatim operator ask record (`operator-asks-2026-07-16.md`).  
> Companion docs: `chat-work-linkage-2026-07-18.md`,
> `chat-toolbar-audit-redesign-2026-07-18.md`.  
> Single-operator system. Voice-mode files are out of scope.

---

## 1. Problem statement

The current `Chat.tsx` infers a task scope from the `contextKey` string
(`taskScopeFor`, line 73) but has no first-class concept of "which scope does
this chat belong to?" The context key is a prefix convention (`build:`, `project:`,
`agent:`) not a real scope association; there is no stored Chat entity to re-scope,
no warning when scope changes while work is live, and no mechanism for two chats
in the same scope to see or edit each other's tickets.

Four gaps to close:

1. **Chat scope is implicit.** There is no stored `Chat` entity with a `scope`
   field; the contextKey prefix IS the scope. Re-scoping therefore has no shape.

2. **No auto-association heuristic.** When a new chat opens in a page that
   could belong to a project or the workshop root, the right scope isn't inferred —
   any new ticket filed from that chat goes to the wrong scope or no scope.

3. **No re-scope warning.** Nothing stops a user from switching a contextKey while
   a run is live in the old scope.

4. **No cross-chat scope sharing.** Two chats open in `project:foo` share no
   state; tickets filed from Chat A are invisible in Chat B until a full page reload.

---

## 2. Chat scope model

### 2.1 Three scope kinds for Chats

A Chat belongs to exactly one Scope (T0 section 2). For the chat surface, three
kinds are meaningful:

| Scope kind | Address form | Meaning |
|---|---|---|
| **workshop** | `workshop` | Spans the whole installation. Work filed here has no project context. |
| **project** | `project:<slug>` | A named project workspace. The default for build chats and project chats. |
| **app** | `app:<name>` | A running application instance. Tickets/agents are app-scoped. |

An `agent:<slug>` scope is valid for the entity model but a Chat in that scope
behaves like a `project` scope chat for purposes of this design — it inherits the
same rules and the same UI.

### 2.2 Chat entity fields (extends T0 section 7.1)

The stored Chat entity from T0 is extended with one field needed for re-scoping:

```
id: str
scope: str                    # scope address (the canonical association)
scope_locked: bool            # false = auto-inferred; true = operator explicitly chose it
title: str | None
started_at: datetime
last_active_at: datetime
participants: list[str]
linked_ticket_ids: list[str]
linked_run_ids: list[str]
```

`scope_locked` distinguishes auto-associated chats (where the scope can be
revised as the heuristic gains more signal) from explicitly-scoped ones (where
the operator made a deliberate choice and re-scoping requires confirmation).

### 2.3 What the scope controls

A Chat's scope determines:

- **Where tickets are filed.** Tickets filed from this chat carry
  `scope = chat.scope`.
- **Which agents are available.** Agents in the same scope (and in parent scopes
  accessible by inheritance) can be addressed from this chat.
- **What the related-work dropdown shows.** It queries
  `GET /api/tasks?scope=<chat.scope>` (see `chat-work-linkage-2026-07-18.md`).
- **Who else can edit.** Any other Chat in the same scope can edit any Ticket in
  that scope (see section 5).

The scope does NOT constrain what the chat model reads (the assistant can discuss
anything). It constrains where work gets filed, which entities auto-surface, and
who has edit authority without a cross-scope Grant.

---

## 3. Auto-association heuristic

When a new Chat is created with no explicit scope, the following ordered checks run
to set the initial `scope`:

### 3.1 Rule table (first match wins)

| # | Signal | Inferred scope | Confidence |
|---|---|---|---|
| 1 | URL path starts with `/projects/<slug>` | `project:<slug>` | high |
| 2 | URL path starts with `/apps/<name>` | `app:<name>` | high |
| 3 | URL path starts with `/agents/<slug>` | `agent:<slug>` | high |
| 4 | `contextKey` starts with `build:` | `project:<contextKey[6:]>` | high |
| 5 | `contextKey` starts with `project:` | `project:<contextKey[8:]>` | high |
| 6 | `contextKey` starts with `app:` | `app:<contextKey[4:]>` | high |
| 7 | `contextKey` starts with `agent:` | `agent:<contextKey[6:]>` | high |
| 8 | No strong signal | `workshop` | low |

Rules 1-3 come from the navigation context at chat creation time (passed in from
the host page). Rules 4-7 come from the `contextKey` prefix convention already in
use. Rule 8 is the fallback.

### 3.2 High-confidence vs low-confidence

When confidence is `high`, `scope_locked` is set to `true` immediately — the
scope is unambiguous and should not drift.

When confidence is `low` (rule 8 fallback to `workshop`), `scope_locked` stays
`false`. If the operator later uses a project-scoped command or files a ticket
with an explicit scope, the heuristic can update the Chat's scope once (and then
locks it).

### 3.3 Implementation touchpoints

| File | Change |
|---|---|
| `backend/routes/chat.py` | Thread `POST` body accepts optional `scope: str`. If omitted, server applies heuristic rules 4-7 from the `context_key` param; URL-derived rules 1-3 must be sent by the client. |
| `backend/services/chat_store.py` | Thread record gains `scope` and `scope_locked` fields; `create_thread(context_key, scope, scope_locked)`. |
| `frontend/src/components/Chat.tsx` | On new thread creation, derive `scope` and `scope_locked` from `contextKey` + current path (rules 4-7); pass to backend. If `scope_locked === false` and a ticket is filed into a narrower scope, call `PATCH /api/chat/{ctx}/thread/{id}/scope` with the updated scope and lock it. |
| `frontend/src/lib/chat.ts` | Extend `Thread` type to include `scope` and `scope_locked`. |

---

## 4. Re-scope: warning + disconnect semantics

### 4.1 Trigger conditions

A re-scope attempt is any change to `chat.scope` on a Chat that already has
content. Two cases:

**Case A: idle chat** (no live runs, no active agents in the old scope)
No warning. Scope changes immediately. All future tickets file into the new scope.
Previously filed tickets remain in the old scope (they don't retroactively
move — a ticket's scope is set at file time and is stable).

**Case B: live chat** (≥1 active run or ≥1 active agent associated with the
old scope, determined by checking `linked_run_ids` against `GET /api/jobs?status=running`
and the scope's active agent roster)

Show the warning dialog before changing scope.

### 4.2 Warning dialog spec

```
┌─────────────────────────────────────────────────────────┐
│ Move to <new scope label>?                              │
│                                                         │
│ This chat has 2 active runs and 1 agent in              │
│ <old scope label>. Moving now will disconnect them      │
│ from this chat. They continue running in their scope    │
│ but won't appear here.                                  │
│                                                         │
│ Tickets you've filed stay in <old scope label>.         │
│                                                         │
│   [Cancel]              [Move and disconnect]           │
└─────────────────────────────────────────────────────────┘
```

Exact wording from operator addendum: *"Move to X and disconnect from
runs/agents/other outside this scope?"* — this dialog implements that prompt
verbatim, expanded into a full ConfirmDialog.

Design system: `ConfirmDialog` component (destructive path), `danger` variant on
the confirm button. Body text uses the Design System's paragraph style.

### 4.3 "Disconnect" semantics

Disconnect means:
- The chat's `scope` field updates to the new scope.
- The chat's `scope_locked` is set to `true`.
- `linked_run_ids` and `linked_ticket_ids` are NOT cleared — the links remain as
  read-only history in the chat transcript.
- The runs continue executing in their original scope; they are not cancelled.
- Agents in the old scope remain active; they are not stopped.
- The related-work dropdown and notification drawer shift to the new scope
  (querying `GET /api/tasks?scope=<new scope>`) — old-scope work disappears
  from those surfaces, consistent with the new scope.

The operator can navigate to `/work?scope=<old scope>` or reopen the runs from
the transcript links to see the old work. The disconnect is a UI unsubscription,
not a cancellation.

### 4.4 Where re-scoping is triggered

Two surfaces trigger re-scope (both behind the same warning):

**A. Scope picker in the toolbar.** The redesigned toolbar
(`chat-toolbar-audit-redesign-2026-07-18.md`) collapses the provider and thread
selects into a labeled `Scope` dropdown. Changing the scope dropdown value
triggers re-scope.

**B. Filing a ticket into a different scope.** If the ticket-filing flow resolves
to a different scope than the chat's current scope (e.g. the operator explicitly
types `project:other-project` as the scope), surface the same warning before
filing.

### 4.5 Implementation touchpoints

| File | Change |
|---|---|
| `backend/routes/chat.py` | `PATCH /api/chat/{ctx}/thread/{id}/scope` — updates `scope`, `scope_locked`; returns conflict info (active run/agent count) for pre-check |
| `backend/routes/chat.py` | `GET /api/chat/{ctx}/thread/{id}/live-scope-check` — returns `{active_runs: int, active_agents: int}` for use in the warning dialog |
| `frontend/src/components/Chat.tsx` | `rescopeChat(newScope)` — fetches live-scope-check; if non-zero, shows `ConfirmDialog`; on confirm calls PATCH; updates local state |
| `frontend/src/components/Chat.tsx` | Scope picker in toolbar (variant: dropdown using the `Select` primitive, label "Scope") |
| `frontend/src/lib/chat.ts` | `Thread.scope`, `Thread.scope_locked`; `setThreadScope(threadId, scope)` |

---

## 5. Cross-chat in-scope access

### 5.1 The rule (from second addendum)

> This chat OR any other chat in the same scope must be able to edit any task or
> other entity within that scope. Before overwriting or changing plans, the
> editing chat must look for and think about conflicts and context.

The entity model (T0 section 7.2) ratifies this: "Any Chat in the same Scope can
edit any Ticket in that Scope. Before overwriting or changing plans, the editing
Chat must surface conflicts to the operator."

### 5.2 Editing authority

Any Chat with `scope == X` can:
- Create a Ticket in scope `X`
- Edit the `text`, `status`, `priority`, `latitude`, or `depends_on` of any
  Ticket in scope `X`
- Cancel a Run in scope `X`
- Address a Block (send an operator response verb) for any Ticket in scope `X`

Any Chat with `scope == X` CANNOT (without a cross-scope Grant):
- Edit Tickets in `project:Y` from a chat scoped to `project:X`
- Read the transcript of another Chat in `project:X` directly (transcripts are
  chat-private; only entities are shared)

### 5.3 Conflict detection

When Chat B attempts to edit a Ticket that was last modified from Chat A within
the last 60 seconds, the backend responds with an HTTP 409 including:

```json
{
  "conflict": true,
  "last_editor_chat_id": "chat-abc",
  "last_edited_at": "2026-07-18T14:23:01Z",
  "last_edit_summary": "status changed to doing by ralph (run job-xyz)"
}
```

The frontend presents this as a non-blocking conflict notice (a `warn` StatusPill
inlined into the editing surface, not a blocking modal):

```
┌────────────────────────────────────────────────────┐
│ ⚠ Another chat modified this ticket 12s ago       │
│   "status changed to doing by ralph (run job-xyz)" │
│   [Show context ↗]   [Edit anyway]                │
└────────────────────────────────────────────────────┘
```

"Show context" links to the other chat (by its `id`). "Edit anyway" proceeds.

The 60-second window prevents stale-read conflicts but does not require
collaborative locking. Chats in the same scope are single-operator; the conflict
surface is informational, not a gate.

### 5.4 Surfacing other chats' work

When the operator opens a ticket detail from within Chat B, and that ticket was
originated from Chat A (i.e. `ticket.linked_chat_id != current_chat.id`):

- A small breadcrumb reads: "Filed from [Chat A title] ↗" and links to Chat A.
- If Chat A is the same browser session, the link opens it in the same tab.

This is a read-only annotation — no cross-chat transcript access. The operator
navigates to the originating chat if they need that context.

### 5.5 Implementation touchpoints

| File | Change |
|---|---|
| `backend/services/tasks.py` | Add `last_editor_chat_id: str | None` and `last_edited_at: datetime | None` fields to Task; update on every mutation. |
| `backend/routes/tasks_all.py` | On PUT/PATCH to a Task, check `last_edited_at` recency; return 409 with conflict body if within 60s and `last_editor_chat_id != requesting_chat_id`. |
| `backend/routes/tasks_all.py` | Accept `requesting_chat_id` as a request header (`X-Chat-Id`) on all mutating task endpoints. |
| `frontend/src/components/chat/UiCard.tsx` | Pass `X-Chat-Id` header on ticket mutation calls; handle 409 by rendering the conflict notice instead of failing silently. |
| `frontend/src/components/TaskPanel.tsx` | Same: pass `X-Chat-Id`, render conflict notice on 409. |
| `backend/services/tasks.py` | `linked_chat_id: str | None` added to Task (the chat that created it, set at file time, never changed). |

---

## 6. Auto-update wiring

### 6.1 The shared-scope polling contract

All Chats in scope `X` that are currently open in the same browser session share
one poll clock for the scope's entity state. This avoids N×15s fan-out when
multiple chats are visible (e.g. Chat A open in a tab, project page open in
another).

Implementation: a module-level `scopePoller.ts` singleton keyed by scope string.
Any Chat that mounts and registers `useRelatedWork(scope)` increments a ref-count
for that scope's poller; on unmount it decrements. The poller runs while
ref-count > 0. All consumers of that scope's poll receive the same state.

```ts
// scopePoller.ts
const pollers = new Map<string, ScopePoller>();

export function useScopePoller(scope: string): ScopeState {
  useEffect(() => {
    const p = getOrCreate(scope);
    p.addRef();
    return () => p.removeRef();
  }, [scope]);
  return useExternalStore(/* scope's store */);
}
```

### 6.2 What auto-updates

Within the poll cycle (15s while any subscriber is mounted):

| Entity | Signal | What updates |
|---|---|---|
| Ticket | `status` change | related-work dropdown badge count; inline card status if tracking phase |
| Ticket | `awaiting` set | notification drawer (needs-input event) |
| Run | `status → done/failed` | notification drawer (done event); tracking card in the originating chat |
| Ticket | Edit by another chat (detected via `last_editor_chat_id` change) | conflict breadcrumb on any open ticket detail |

The poll fetches:
```
GET /api/tasks?scope=<scope>&status=todo,doing,in_review,blocked
GET /api/tasks?scope=<scope>&status=done&limit=5&sort=updated_desc
```

Run status is polled separately by the tracking card (`GET /api/jobs/{id}` every
5s while running, per `chat-work-linkage-2026-07-18.md` section 2.3).

### 6.3 Cross-tab update (out of scope for this design)

Multiple browser tabs in the same scope are NOT synchronized in this design
(no BroadcastChannel, no SSE). Each tab has its own poller. 15s poll latency
is acceptable for the single-operator use-case; a tab that is not in focus
pauses its poll via `visibilitychange` to save resources.

If the operator has two tabs open in the same scope and edits a ticket in one,
the other tab sees the update within 15s on the next poll cycle.

### 6.4 Implementation touchpoints

| File | Change |
|---|---|
| `frontend/src/lib/scopePoller.ts` | New singleton; `getOrCreate(scope)`, `addRef()`, `removeRef()`, `useScopePoller(scope)` hook |
| `frontend/src/components/chat/RelatedWorkDropdown.tsx` | Consume `useScopePoller` instead of its own internal poll |
| `frontend/src/components/Chat.tsx` | Register with `useScopePoller` when scope is set; pause on `visibilitychange` |
| `frontend/src/components/TaskPanel.tsx` | Consume `useScopePoller` for live task state in the task panel |

---

## 7. Scope picker UI (toolbar integration)

The re-scoped chat toolbar (`chat-toolbar-audit-redesign-2026-07-18.md`) includes
a labeled `Scope` picker. This section specifies its behavior.

### 7.1 Control

A labeled `Select` primitive (Design System rule 1) showing the current scope's
display label:

| Scope | Display label |
|---|---|
| `workshop` | Workshop |
| `project:foo` | Project: Foo |
| `app:birds` | App: Birds |
| `agent:ralph` | Agent: Ralph |

Options list: the current scope is pre-selected. Other options come from
`GET /api/scopes` returning the operator's active projects and apps.

Changing the selection calls `rescopeChat(newScope)` (section 4.5).

### 7.2 When it shows

The scope picker shows in:
- `variant === "full"` (FocusedChat page)
- `variant === "panel"` when the panel is wide enough (≥ 480px)

In `variant === "dock"` the scope is display-only (a small `Tag` in the toolbar,
not interactive) to preserve vertical space.

### 7.3 Scope display in the transcript

After a successful re-scope, the transcript logs a system message:

```
─── Scope changed to Project: Foo ────────────────────────────
```

Using the existing `chat-spawn-note` banner class (already in Chat.tsx), variant
neutral (no status color — this is informational, not a status change).

---

## 8. Summary of new API surface

| Method | Path | Purpose |
|---|---|---|
| PATCH | `/api/chat/{ctx}/thread/{id}/scope` | Re-scope a thread; requires `{scope}` body |
| GET | `/api/chat/{ctx}/thread/{id}/live-scope-check` | Returns `{active_runs, active_agents}` for warning dialog |
| GET | `/api/scopes` | List scopes available for the scope picker |
| Header | `X-Chat-Id: <id>` | Passed on all mutating task calls for conflict detection |

The `PATCH .../scope` endpoint is idempotent: scoping to the current scope is a
no-op.

---

## 9. Sequencing

| # | Piece | Depends on |
|---|---|---|
| S1 | Chat entity scope field + heuristic (section 3) | T0 entity model, thread store migration |
| S2 | Scope picker in toolbar | S1, toolbar redesign (B8) |
| S3 | Re-scope warning dialog | S1, S2 |
| S4 | Cross-chat conflict detection (section 5.3) | S1, `last_editor_chat_id` on Task |
| S5 | Scope poller singleton (section 6) | S1; runs in parallel with S2-S4 |
| S6 | Scope breadcrumb on ticket detail | S4 |

S1 is the foundation. S2/S3 and S4/S5 are independent of each other after S1.
S6 is cosmetic and can trail S4.

---

## 10. Out of scope

- WebSocket or SSE push (15s polling sufficient for single-operator)
- Concurrent multi-user locking (single-operator system; conflict notice is
  informational, not a gate)
- Cross-scope Chat editing (requires an explicit Grant; not part of this design)
- Merging or migrating previously filed tickets when a chat re-scopes (tickets
  stay in their original scope; re-scope is not a bulk move)
- Voice-mode surface (unrelated)
- Any <blocked-org> org content
