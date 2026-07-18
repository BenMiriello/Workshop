# Chat ↔ Work Linkage Design (2026-07-18)

Status: design doc, not built. Depends on: T0 (ground clearing), Phase 2 (Work surface), Phase 3 (chat seam) from `chat-tickets-orchestrator-plan-2026-07-15.md`.
Single-operator system. Voice-mode files are out of scope.

---

## 1. Problem statement

Four things are broken or missing today:

1. **Card duplication after File-and-run.** When the operator clicks "File and run" on a task-proposal card, two tracking artifacts appear: the card transitions to a success banner ("Filed and running"), AND the server appends a separate `job-status` card below it. If the page is reloaded, the task-proposal card comes back in edit state — re-offering File and File-and-run as if nothing happened. The job-status card persists, creating two representations of the same run.

2. **No per-kind auto-run preference.** The operator frequently files-and-runs certain context types (build chats, project chats). There is no way to express "always run proposals from this context type immediately" — the two-click flow is repeated every time.

3. **No in-chat related-work summary.** Within a chat, there is no lightweight view of what work has been filed from this context, what's currently running, and what has settled. The closest affordance is "Open in Work" — a full-page navigation away. The operator has to leave the conversation to check work status.

4. **No notification for major events.** A run completing (or blocking) while the operator is in the chat produces nothing. No signal in the chat surface; they have to go to `/jobs` or `/work` to find out.

---

## 2. Card lifecycle: fixing the duplication bug

### 2.1 Current flow (the bug)

```
operator clicks "File and run"
  → addTasksAndRun handler runs
  → POST /api/tasks → task created
  → POST /api/tasks/{scope}/{id}/run → job_id returned
  → handler returns {status:"success", note:"Filed and running", values:{__job_id:...}, actions:[viewRun, openWork]}
  → UiCard sets phase = "success"  ← EPHEMERAL; gone on reload
  → server (in the /run endpoint) appends assistant message with job-status card ← PERSISTED
  → chat stream delivers the new job-status card as a second message
```

Result: two artifacts, one ephemeral (the card success banner) + one persistent (the appended job-status message). On reload: only the persistent one survives, but the original task-proposal card is back in edit state, offering File/File-and-run again.

Root cause: the card's client-side phase is not persisted, and the server creates a second artifact to compensate — but only persists the second artifact, not the phase change on the first.

### 2.2 Fix: per-thread card state store

Introduce a `card_states` sidecar on each chat thread: a small JSON map keyed by `spec.id`:

```jsonc
// stored alongside the thread's messages, e.g. thread_id/card_states.json
{
  "card_abc123": {
    "phase": "tracking",
    "job_id": "job_xyz",
    "task_id": "task_abc",
    "scope": "project:my-project"
  }
}
```

When the operator clicks "File and run":
1. `addTasksAndRun` handler completes as today (files ticket + starts run).
2. Handler emits `{status: "success", values: {..., __job_id: "...", __task_id: "..."}}` — same as now.
3. Additionally, the handler calls a new thin endpoint: `PATCH /api/chat/{contextKey}/thread/{threadId}/card-states/{cardId}` with `{phase:"tracking", job_id, task_id, scope}`. This writes to the `card_states` sidecar.
4. The server's `/run` endpoint no longer appends a separate `job-status` assistant message — the original card handles tracking.

On page load:
- Chat renderer fetches `card_states` alongside messages (extended in the thread response or as a parallel fetch).
- `UiCard` receives `initialCardState` prop; when it has `phase:"tracking"`, it renders the `JobStatusCard` poller directly, bypassing the edit phase entirely.
- No re-offer of File/File-and-run. No duplicate artifact.

### 2.3 "Tracking" phase behavior

After File-and-run, the card collapses into a compact live-progress element:

```
┌──────────────────────────────────────────────┐
│ ☑ Add OAuth to the auth service              │
│   running  ·  0:42                           │
│   [View job ↗]  [Open ticket ↗]             │
└──────────────────────────────────────────────┘
```

- Fields and action buttons are gone.
- Status pill reflects the live job state (polling `GET /api/jobs/{id}` every 5s while running).
- On completion: shows receipt highlights (files count, commits count, summary first line) before the links.
- On failure: status pill is `danger`, summary is the first line of the error.
- Uses the existing `JobStatusCard` sub-component (or extracts it as `JobProgressInline`).
- The task title comes from `values.title` (already in card state).

### 2.4 Reload-persistence with `card_states`

The `card_states` sidecar is a per-thread JSON file:
- Location: same directory as the thread store, or as a field inside it.
- Written on File-and-run (and File-only, which sets `phase:"filed"`).
- Read when the thread is fetched.

Card state shape:
```ts
type CardState = {
  phase: "filed" | "tracking" | "dismissed";
  job_id?: string;
  task_id?: string;
  scope?: string;
};
```

The `"filed"` phase (for `addTasks` without run) renders a compact "Filed ✓" card:
```
┌────────────────────────────────┐
│ ✓ Add OAuth to the auth service│
│   Filed in project:auth        │
│   [Open in Work ↗]            │
└────────────────────────────────┘
```

No action buttons offered. The work is tracked in `/work`; no second offer needed.

### 2.5 Implementation touchpoints

| File | Change |
|------|--------|
| `backend/services/chat_store.py` | `set_card_state(thread_id, card_id, state)`, `get_card_states(thread_id)` — writes to `threads/{id}/card_states.json` |
| `backend/routes/chat.py` | Thread GET response includes `card_states` map; new `PATCH /api/chat/{ctx}/thread/{thread_id}/card-states/{card_id}` endpoint |
| `backend/routes/tasks_all.py` | `/run` endpoint no longer appends the standalone `job-status` assistant message (the card handles it now) |
| `frontend/src/components/chat/UiCard.tsx` | New `"tracking"` and `"filed"` phases; accept `initialCardState` prop; render `JobProgressInline` in tracking phase |
| `frontend/src/components/chat/uiCardActions.ts` | `addTasksAndRun` and `addTasks` call the card-state PATCH after success |
| `frontend/src/components/Chat.tsx` | Fetch card states alongside messages; pass `initialCardState` to each `UiCard` |

The `job-status` card kind is retained as a standalone kind for cases where a run is started from outside a proposal card (e.g., "Run now" in the Work drawer). The duplication fix applies only to the task-proposal → run flow.

---

## 3. Per-kind auto-run toggle

### 3.1 Motivation

After the operator files-and-runs a few proposals from a build chat, it is clear that for build-context proposals they always want immediate execution. The two-click flow (edit card → File and run) becomes noise.

The toggle is a per-context-kind preference: "for proposals that come from build/project/general chats, skip the edit step and auto-run immediately."

### 3.2 Surface

After a successful File-and-run, the tracking card includes a small preference row:

```
┌──────────────────────────────────────────────┐
│ ✓ Add OAuth to the auth service              │
│   done · 2m 14s · 3 files · 2 commits        │
│   [View job ↗]  [Open ticket ↗]             │
│                                              │
│   Auto-run proposals in build chats? [Off ▾]│
└──────────────────────────────────────────────┘
```

- Toggle shows only on the first settled tracking card in a session (or when preference is not set).
- Toggle options: `Off` / `On (this project)` / `On (all builds)`.
- Toggling commits the preference immediately (no Save button; this is a view-pref commit-on-change pattern per the design system's editability model — "view preferences commit on change").

When preference is `On`, the next task-proposal card from the same context kind bypasses the edit phase: auto-invokes `addTasksAndRun` on mount, shows "Auto-running..." instead of buttons, then transitions to tracking phase.

An "Undo auto-run" link appears on the auto-running tracking card for 10 seconds (before the run actually starts). After that window closes, the operator can cancel from the Work surface.

### 3.3 Preference storage and lookup

Preference storage is handled by the learnings store (`backend/services/learnings.py`). See ADR 0006 for the mechanism decision.

The key point here: `chat_context.py`, when building a `task-proposal` card spec, looks up the auto-run preference for this context kind. If set, it injects `"auto_run": true` into the spec. The client sees `spec.auto_run === true` and auto-invokes `addTasksAndRun` on mount.

This is server-injected (not client-discovered) so the preference check happens at card-emit time, not at card-render time. The client does not need access to the learnings store.

```python
# chat_context.py, inside the task-proposal card builder
auto_run = learnings.get_preference(
    key="auto_run_proposals",
    scope=context_kind,          # e.g. "build", "project", "general"
    specific=project_slug or ""  # more specific wins over broader
)
card_spec["auto_run"] = auto_run
```

Preference scoping (coarse to fine, more specific wins):
- `"general"` (all chats)
- `"build"` (all build chats)
- `"project"` (all project chats)
- `"project:my-project"` (one specific project)

### 3.4 Implementation touchpoints

| File | Change |
|------|--------|
| `backend/services/learnings.py` | `get_preference(key, scope, specific)` — structured lookup over existing store, returns bool; `set_preference(key, scope, value)` — writes structured pref entry |
| `backend/services/chat_context.py` | Inject `auto_run` flag into `task-proposal` spec on emit |
| `frontend/src/components/chat/UiCard.tsx` | If `spec.auto_run === true`, auto-invoke `addTasksAndRun` on mount; show "Auto-running..." indicator; render "Undo" link for 10s |
| `frontend/src/components/chat/uiCardActions.ts` | New handler `setAutoRunPreference` — calls `PATCH /api/chat/{ctx}/preferences` with `{key:"auto_run_proposals", scope, value}` |
| `backend/routes/chat.py` | New `PATCH /api/chat/{ctx}/preferences` endpoint — writes to learnings store |

---

## 4. In-chat related-work dropdown

### 4.1 Placement and trigger

A compact badge button in the chat header row, right-aligned:

```
[← Chat title]                     [2 active ↓]   [settings]
```

- Shows only when the context has associated work (queued/running/blocked tasks or recent done tasks).
- Badge count reflects active-only (todo + doing + in_review + blocked).
- Click expands a dropdown panel anchored below the button.
- Keyboard: `Esc` collapses; focus-trap within the dropdown.

### 4.2 Dropdown contents

```
┌─────────────────────────────────────┐
│ Active                              │
│  ● Add OAuth to auth service        │
│    running  ·  3m                  │
│  ○ Upgrade database driver          │
│    queued                           │
│  ⚠ Fix the config loader            │
│    needs input                  [?]│
│                                     │
│ Recently done (2)          [Show ▸] │
└─────────────────────────────────────┘
```

- Active section: tasks where status is `todo`, `doing`, `in_review`, or `blocked`. Sorted by `priority` desc, then `updated` desc.
- "Recently done" section: collapsed by default; shows last 5 done tasks from this context's scope.
- Each row: status dot (design system `StatusDot`), task title (truncated to ~40 chars), status text, elapsed time if running.
- Blocked rows: show a `?` link that opens the task's awaiting question in the Work drawer.
- Overflow: "…and N more" link → `/work?scope={scope}`.
- Click on a row: opens the Work drawer for that task (via `ctx.navigate`).

### 4.3 Data source

```
GET /api/tasks?scope={contextScope}&status=todo,doing,in_review,blocked
GET /api/tasks?scope={contextScope}&status=done&limit=5&sort=updated_desc
```

Poll interval: 15s while dropdown is open; paused when closed.

Context scope mapping: the same `task_scope.root_for_context(context_key)` lookup used by the chat provider — whatever scope the current chat's proposals would file into.

### 4.4 Implementation touchpoints

| File | Change |
|------|--------|
| `frontend/src/components/Chat.tsx` | Add related-work badge button to chat header; pass `contextScope` to `RelatedWorkDropdown` |
| `frontend/src/components/chat/RelatedWorkDropdown.tsx` | New component; fetches task lists, renders two sections with polling |
| `backend/routes/tasks_all.py` | Ensure `GET /api/tasks` supports `sort=updated_desc` and `limit` param (likely already present or trivial add) |

No backend changes other than confirming the query params. The task list API is already scoped and filterable.

---

## 5. Top notification drawer

### 5.1 Behavior

A thin slide-down banner anchored at the top of the chat area (below the header, above messages). It is NOT a modal. It occupies ~48px when visible.

Triggered by:
- A task associated with this chat completing (status → `done` or `failed`)
- A task associated with this chat becoming blocked (`awaiting` field set, `dismissed: false`)

```
┌─────────────────────────────────────────────────┐
│ ✓ Add OAuth to auth service — done in 2m 14s   │
│   [View receipt]   [Dismiss]                    │
└─────────────────────────────────────────────────┘
```

```
┌─────────────────────────────────────────────────┐
│ ⚠ Upgrade database driver — needs input         │
│   "Which version of pg8000 should I use?"       │
│   [Review]   [Dismiss]                          │
└─────────────────────────────────────────────────┘
```

Styling: uses `StatusPill` kind `ok` (done), `danger` (failed), `warn` (blocked). No custom colors.

### 5.2 Dismissal

- Done/failed: auto-dismiss after 10 seconds (with a countdown progress underline).
- Needs input: no auto-dismiss; persists until the operator acts or dismisses. A persistent "needs input" indicator in the related-work badge continues to reflect this state.

Multiple events: drawer shows one at a time; queues additional events as a count badge on the "next" button. Showing is FIFO; "needs input" events are promoted above "done" events.

### 5.3 Event detection

The notification drawer is driven by the same poll that backs the related-work dropdown (shared state, no duplicate fetches):
- Diff the task list between polls.
- If a task's status changed to `done`/`failed` and it wasn't in that state at the last poll: enqueue a done-event notification.
- If a task's `awaiting` field appeared (non-null, non-dismissed) and wasn't there before: enqueue a needs-input notification.

No SSE or websocket required for this. 15s poll is fine for these event types.

### 5.4 "Review" for blocked tasks

The "Review" button on a needs-input notification opens the Work drawer for that task, pre-scrolled to the `awaiting` section. The user types an answer or dismisses the block from there. After dismissal, the task re-enters the dispatch loop.

### 5.5 Implementation touchpoints

| File | Change |
|------|--------|
| `frontend/src/components/Chat.tsx` | Integrate notification drawer; derive events from the related-work poll state diff |
| `frontend/src/components/chat/ChatNotificationDrawer.tsx` | New component; renders one event at a time, countdown for done events |
| `frontend/src/components/chat/RelatedWorkDropdown.tsx` | Expose previous/current task list diff for the notification drawer to consume (shared poll state via parent or a shared hook) |

The simplest wiring: a `useRelatedWork(contextScope)` hook owns the polling and returns `{tasks, notifications, clearNotification}`. Both `RelatedWorkDropdown` and `ChatNotificationDrawer` consume it.

---

## 6. Sequencing

These four pieces have natural dependencies:

| # | Piece | Depends on |
|---|-------|-----------|
| L1 | Card-state sidecar (persistence layer) | Phase 3 (tasks_all.py `/run` endpoint) |
| L2 | Card collapse after File-and-run | L1, Phase 3 UiCard kinds |
| L3 | Auto-run toggle (UI) | L2 (appears on settled tracking card) |
| L4 | Auto-run lookup (server injection) | ADR 0006, learnings preferences API |
| L5 | Related-work dropdown | Phase 2 (`/api/tasks` endpoint) |
| L6 | Notification drawer | L5 (shared poll state) |

L1+L2 are the highest-priority fix (card duplication bug). L5+L6 and L3+L4 are independent of each other and can ship in either order after their dependencies.

---

## 7. Out of scope

- WebSocket or SSE push (polling suffices for this use-case; revisit if poll latency is felt)
- Multi-run parallel tracking in a single card (one card → one run; parallel runs go to `/work`)
- Notification drawer on non-chat pages (scope: chat surface only)
- Card state persistence beyond the thread's lifetime (clearing a thread clears card state)
- Any voice-mode surface (unrelated)
