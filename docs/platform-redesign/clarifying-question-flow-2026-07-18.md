# Clarifying-question flow (2026-07-18)

- Status: accepted
- Depends on: entity-model-2026-07-18.md, chat-work-linkage-2026-07-18.md, ticket-maker-2026-07-18.md
- Consistent with: blocked-and-escalate-flow-2026-07-18.md (section 7 of that doc commits to this design)
- Source: operator ask B5 (orchestration-surface requirements)

---

## Summary

When the ticket-maker or a running worker needs operator input and the originating
Chat is identified, the question surfaces inside that Chat as a **mini sub-chat
card**: a focused, inline input area that the operator can answer, discuss, or
cancel without leaving the conversation. The card anchors to the top of the
visible chat area when it is out of view. The main Chat manages the question's
lifecycle end-to-end.

This is the synchronous-or-near-synchronous case. The blocked-and-escalate flow
(A2 doc) covers the complement: background work where no originating Chat is
present or the operator is away.

---

## 1. When a clarifying-question card is raised

Three producers can raise a clarifying-question card:

### 1.1 Ticket-maker (Phase 1 in-chat)

The ticket-maker's Phase 1 runs inside the Chat thread before any Run is
started. When the chat agent cannot fully decompose the request without
additional information, it emits a `clarifying-question` card directly into the
thread. This is a synchronous in-chat gesture: the agent pauses decomposition;
the operator responds; decomposition continues with the answer in context.

The ticket-maker NEVER asks clarifying questions via a background channel. Phase
1 is always in-thread.

### 1.2 In-progress Run (worker-raised Block)

A Run can raise a Block with `kind: "input"` (`awaiting` field, entity-model
section 4.3). When that Run was launched from an identifiable originating Chat
(the Ticket carries a `chat_origin` pointer, see section 2.2), the platform
surfaces a mini sub-chat card in THAT Chat in addition to the standard Work
review tab entry.

The card is the fast path. The review tab is the fallback (for the operator not
presently in the Chat). Both surfaces read from and write to the same `awaiting`
field. A resolution from either clears the Block and dismisses both.

### 1.3 Reviewer-raised Block

A reviewer that cannot evaluate acceptance without operator input raises a Block
with `raised_by: "reviewer"`. If the originating Chat is known, the card appears
there too. The card header indicates "Reviewer needs input" so the operator knows
the worker has already stopped.

Reviewer-raised cards never auto-resolve (A2 doc section 6.2). No deadline
countdown is shown.

---

## 2. Card anatomy

### 2.1 Visual structure

The card renders as a `ui-card` kind `clarifying-question` inside the Chat
thread. It uses Workshop Design System primitives (`Card`, `Stack`, `Row`,
`Button`, `Field`).

```
┌─ Clarifying question ──────────────────────── raised by: ticket-maker ─┐
│                                                                          │
│  Should the new auth flow replace the existing one entirely, or run     │
│  alongside it for a transition period?                                   │
│                                                                          │
│  [Replace entirely]  [Run alongside, then migrate]                      │
│                                                                          │
│  ─────────────────────────────────────────────────────────────────       │
│  Or type a different answer:                                             │
│  ┌────────────────────────────────────────────────────────────────┐     │
│  │                                                                │     │
│  └────────────────────────────────────────────────────────────────┘     │
│                                                                          │
│  [Stop this run]  [Discuss in chat]  [Cancel question]                  │
└──────────────────────────────────────────────────────────────────────────┘
```

Top-to-bottom layout:

**Header strip**: label "Clarifying question" + raised-by tag (`ticket-maker` /
`worker` / `reviewer`). For worker/reviewer cards: Ticket link and elapsed time
since raised. For `kind: "input"` with a deadline: a `Clock` icon and
countdown. Reviewer-raised: a `GitBranch` icon; no countdown.

**Question body**: the `awaiting.why` text (for worker/reviewer cards) or the
in-chat question text (for ticket-maker Phase 1 cards). Plain prose, no
truncation.

**Option buttons** (when `awaiting.options` is non-empty): rendered as
`Button variant="secondary"` chips. Clicking one is an immediate submit — no
extra confirm step. `awaiting.default`, if set, is pre-highlighted.

**Free-text field**: always present below the options. A `Field` component with
placeholder "Type a different answer…". Submits on Enter or an explicit Send
button when the field has content. If the operator types, the option buttons
lose their pre-highlight state.

**Control row**: three actions (section 2.3), always visible.

### 2.2 Raised-by tag and originating context

The Ticket schema carries a `chat_origin` field (a `{context_key, thread_id}`
pair) set by the ticket-maker when it creates the Ticket. This is how the
platform knows which Chat to surface the card in.

For ticket-maker Phase 1 cards, there is no Ticket yet; the card is rendered
directly by the chat agent via a tool call that emits a `clarifying-question`
card spec.

For worker/reviewer cards, the backend delivers the card via the
`ChatNotificationDrawer` top-of-chat event (B1 linkage doc, section 5) with
`event.kind: "needs-input"` and `event.card_spec` included. The Chat renders
the card inline when the operator opens the drawer OR when the chat is the
originating context for that Ticket.

### 2.3 Controls

| Control | Verb | Effect |
|---------|------|--------|
| **Submit** (option click or Enter) | approve-once | Writes operator's answer to `awaiting.proposal` and calls the unblock path: `update(status="todo", awaiting=None)`, appends answer to Ticket evidence, calls `restore_attempt`. For Phase 1: passes answer back to the ticket-maker in-context; no Ticket mutation. |
| **Stop this run** | reject | Calls `update(status="cancelled", blocked_reason="operator stopped via clarifying-question card")` + `ConfirmDialog`. Only available for worker/reviewer cards (there is no run to stop for Phase 1). |
| **Discuss in chat** | defer-to-thread | Collapses the card to a compact "Paused for discussion" strip; re-opens the normal chat input so the operator can talk with the chat agent about the question. The card is re-openable by clicking the strip. For Phase 1: the ticket-maker reads the subsequent chat turns as context. For worker/reviewer cards: the Ticket stays `blocked`; the operator can re-submit from the strip at any time. |
| **Cancel question** | defer | For Phase 1: dismisses the card; the ticket-maker proceeds without the answer (using its default / best-effort decomposition). For worker/reviewer: same as Defer in the A2 verb set — calls `update(status="parked")`; card dismissed. |

"Stop this run" uses `ConfirmDialog` per the destructive-action rule in
`frontend/CLAUDE.md`.

"Discuss in chat" is the key differentiator from the Work review tab. The
operator can have a multi-turn conversation about the question, look at context
in the thread, and then decide. The card stays pinned at the top until they do.

### 2.4 Float-to-top behavior

When the card is not visible in the current scroll position (because the thread
has scrolled past it), a sticky banner anchors at the top of the chat message
area:

```
┌────────────────────────────────────────────────────────────┐
│ ⚠ Clarifying question waiting  —  auth flow replacement?  │
│   [Answer now ↑]                                           │
└────────────────────────────────────────────────────────────┘
```

Clicking "Answer now" smoothly scrolls to the card AND expands it if it had
been collapsed to the "Paused for discussion" strip.

The banner uses `StatusPill kind="warn"` for worker/reviewer cards and
`StatusPill kind="running"` for active Phase 1 ticket-maker questions (the
operator is expected to respond now).

Only one float banner per chat at a time. If multiple cards exist (unusual but
possible if the operator has not answered a Phase 1 question and a second worker
block arrives), they are queued; the banner shows the oldest unresolved one with
a "2 waiting" count.

---

## 3. States and lifecycle

```
                    ticket-maker Phase 1
                    emits card spec
                          │
                          ▼
                      [open]  ──── operator submits ──► [resolved]
                          │              │
                          │         answer in context
                          │         ticket-maker continues
                          │
                          ├──── "discuss in chat" ──► [discussing]
                          │              │
                          │         strip shown
                          │         chat open
                          │              │
                          │         re-submit ──────► [resolved]
                          │
                          └──── "cancel question" ──► [dismissed]
                                     ticket-maker proceeds without answer


                    worker raises Block
                    (awaiting.kind = "input")
                          │
                          ▼
                     [open] ─────── operator submits ──► [resolved]
                          │               │
                          │         Ticket → todo
                          │         new Run queued
                          │
                          ├──── "stop this run" (confirm) ──► [stopped]
                          │               │
                          │         Ticket → cancelled
                          │
                          ├──── "discuss in chat" ──► [discussing]
                          │               │
                          │         strip shown, Ticket stays blocked
                          │               │
                          │         re-submit ──────► [resolved]
                          │
                          └──── "cancel question" (defer) ──► [deferred]
                                     Ticket → parked
```

**[open]**: card fully expanded, controls visible.

**[discussing]**: card collapsed to "Paused for discussion ↓" strip. Normal chat
input active. Strip click re-opens to [open].

**[resolved]**: card replaced by a compact "Answered ✓" strip showing the answer
(truncated to ~60 chars). No further interaction. Strip persists in-thread for
history.

**[stopped]**: card replaced by "Run stopped" strip. Ticket link shown.

**[deferred]**: card dismissed (no strip left). Ticket is `parked` in the queue.
The Work review tab still shows it.

---

## 4. Multiple simultaneous cards

A chat can have at most one open Phase 1 ticket-maker card at a time (the
ticket-maker is synchronous; it does not ask the next question until the first is
answered or cancelled).

Worker/reviewer cards are independent of Phase 1 cards. In principle, multiple
worker Blocks from different Tickets could surface in the same Chat if that Chat
is the originating context for multiple Tickets.

Handling:
- Each card is a separate element in the thread at its original position.
- The float banner shows the oldest unresolved card with a "N waiting" count.
- The operator works through them in any order.
- Cards from different Tickets do not interact.

This is an expected-rare case. Typical sessions have one active ticket-maker
question or one worker block at a time, not both simultaneously.

---

## 5. Relationship to blocked-and-escalate (A2)

The A2 doc (blocked-and-escalate-flow-2026-07-18.md, section 7) commits to this
relationship explicitly. Restated here for completeness:

| Dimension | Clarifying-question card (B5) | Work review tab (A2) |
|-----------|-------------------------------|----------------------|
| Surface | Originating Chat (inline) | Work page / Review tab |
| When | Operator likely present; synchronous input | Background/overnight; operator away |
| Available for | ticket-maker Phase 1 + worker/reviewer Blocks with `chat_origin` | All Blocks, always |
| Operator verbs | Submit, Stop this run, Discuss in chat, Cancel question | Approve once, Grant standing, Amend rule, Rescope, Reject, Defer |
| Durability | Approve-once semantics (answer resolves this Block) | Full verb set including standing Grants + rule minting |
| Data source | Same `awaiting` field on the Ticket | Same `awaiting` field |
| Resolution | Clears the Block; both surfaces dismiss simultaneously | Same |

The card is deliberately limited to approve-once semantics. Grant-standing and
rule-minting require the structured context of the Work review tab (the scope
selector, the resource URI, the near-dupe warning). The card is for the fast
path; the review tab is for the durable path.

When the operator dismisses a card via "Discuss in chat" and decides during the
discussion that a standing grant is appropriate, they go to the Work review tab
for that verb. The card's "Discuss in chat" state makes this easy: the Ticket
link is in the strip header.

---

## 6. Delivery mechanics

### 6.1 Phase 1 cards (ticket-maker)

Delivered as a standard card spec from the chat agent's tool output (same
mechanism as `task-proposal` cards, `uiCardActions.ts`). The card kind is
`clarifying-question`. The chat agent emits it when it determines it needs
operator input before decomposing.

Schema:

```jsonc
{
  "kind": "clarifying-question",
  "id": "cq_<uuid>",
  "source": "ticket-maker",
  "ticket_id": null,       // null for Phase 1 (no Ticket yet)
  "question": "...",
  "options": ["Option A", "Option B"],   // may be []
  "default": "Option A" | null
}
```

The chat agent reads the operator's response from the card's submit action and
continues decomposition synchronously. No polling needed; the card submit handler
delivers the answer into the chat stream.

### 6.2 Worker/reviewer cards

Delivered via the `ChatNotificationDrawer` event mechanism already defined in
the B1 linkage doc (section 5.3). The drawer event has `kind: "needs-input"`;
the drawer payload now optionally carries a `card_spec`:

```jsonc
{
  "event": "needs-input",
  "task_id": "abc123",
  "task_title": "Build billing reconciliation system",
  "preview": "Should the new auth flow replace...",
  "card_spec": {
    "kind": "clarifying-question",
    "id": "cq_<ticket_id>",
    "source": "worker",           // or "reviewer"
    "ticket_id": "abc123",
    "question": "...",
    "options": ["..."],
    "default": "..." | null,
    "deadline": "2026-07-18T20:00:00Z" | null,
    "raised_by_run": "run_xyz"
  }
}
```

When the Chat is the originating context for the Ticket (`ticket.chat_origin.context_key
== current_context_key`), the Chat renders the card inline (scrolled to the
position right after the tracking card for that Ticket, or at the end of the
thread if that card is not found).

The submit handler calls `PATCH /api/tasks/{scope}/{id}/block-answer` with the
operator's answer. This endpoint:
1. Appends the answer to the Ticket's evidence list.
2. Calls `restore_attempt` (A2 doc section 5.3).
3. Sets `awaiting = null`.
4. Sets `status = "todo"`.
5. Returns `{status: "ok"}`.

The card transitions to `[resolved]` state on `200`. Error case: card stays
open; an inline error row appears below the controls.

### 6.3 The `chat_origin` Ticket field

The canonical schema addition (onto entity-model-2026-07-18.md section 4, Ticket
schema):

```ts
chat_origin?: {
  context_key: string;   // the chat context that filed or launched this Ticket
  thread_id: string;     // the specific thread
} | null
```

Set by:
- The `addTasksAndRun` / `addTasks` action handlers in `uiCardActions.ts` (they
  know the `context_key` and `thread_id`).
- The ticket-maker agent when it creates child Tickets under a root that has
  `chat_origin` — all children inherit the same `chat_origin`.

Never set for autonomy-dispatcher-launched Tickets (no originating Chat).

---

## 7. Implementation touchpoints

| File | Change |
|------|--------|
| `frontend/src/components/chat/UiCard.tsx` | New `clarifying-question` card kind; [open] / [discussing] / [resolved] / [stopped] / [deferred] rendering states |
| `frontend/src/components/chat/uiCardActions.ts` | `submitClarifyingAnswer` handler → calls `PATCH /api/tasks/{scope}/{id}/block-answer` for worker cards; for Phase 1 cards, delivers answer into chat stream |
| `frontend/src/components/chat/ClarifyingQuestionFloat.tsx` | New component: sticky float banner at top of message area; scroll-to-card + expand on click |
| `frontend/src/components/chat/ChatNotificationDrawer.tsx` | Extend `needs-input` event handling to accept optional `card_spec`; when present and Chat is originating context, inject card inline |
| `frontend/src/components/Chat.tsx` | Mount `ClarifyingQuestionFloat`; track open clarifying-question cards; pass float data |
| `backend/routes/tasks_all.py` | New `PATCH /api/tasks/{scope}/{id}/block-answer` endpoint: answer → evidence, `restore_attempt`, `awaiting=None`, `status=todo` |
| `backend/services/tasks.py` | `chat_origin` field on Task schema (additive, `None` for existing tasks) |
| `backend/routes/chat.py` (or `uiCardActions.ts` equivalent) | Set `chat_origin` on the Ticket when the ticket-maker files it from a Chat |
| `backend/services/chat_store.py` | Include `card_states` / `clarifying_cards` in thread fetch so Phase 1 cards survive reload if the operator hasn't answered yet |

Card state persistence for Phase 1 cards: use the `card_states` sidecar defined
in the B1 linkage doc (section 2.2). A Phase 1 card that the operator hasn't
answered yet survives a page reload as a card in `[open]` state.

---

## 8. What this doc deliberately excludes

- **Grant-standing and rule-minting from the card.** Those verbs require the
  structured context of the Work review tab. The card is approve-once only.
- **Parallel ask flows.** The ticket-maker does not ask multiple questions
  simultaneously. One card at a time, in sequence.
- **Async wait on the server side.** The server does not hold a connection open
  waiting for the operator's answer. The worker's Run has already ended when the
  Block was raised; the next Run begins when the Ticket returns to `todo`. No
  long-poll or suspended coroutine.
- **Push delivery (WebSocket/SSE).** The `ChatNotificationDrawer` uses polling
  (B1 doc section 5.3, 15s interval). This is sufficient for the response times
  expected of this flow.
- **Voice-mode surface.** Out of scope for this ticket.
- **Multi-turn question series.** If the ticket-maker needs N answers, it asks
  them one at a time (each one a separate card, sequentially). Stacked
  multi-question forms are not supported.
