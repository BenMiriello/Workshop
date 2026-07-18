# Ticket-maker and sub-ticket engine (2026-07-18)

- Status: accepted
- Depends on: entity-model-2026-07-18.md, ADR 0007, ADR 0010, ADR 0011
- Source: operator ask B4/B5 (orchestration-surface requirements); first addendum (decomposition in main thread)

---

## 1. The problem this solves

A complex operator request arrives in chat as a single block of prose. Running it
directly as one ticket produces either:

- a giant overlong run that exceeds iteration/token caps and produces a stub, or
- a confused worker that picks arbitrary sub-scopes and misses others.

The ticket-maker solves this by decomposing the request into a bounded tree of
atomic, independently runnable sub-tickets BEFORE any worker fires.

---

## 2. Two-phase model

**Phase 1 — in-chat (before any run)**

The operator brings a request to a chat. The chat agent:

1. Reads the request and asks clarifying questions IN the chat thread.
2. The operator answers in-thread.
3. The agent produces a proposed decomposition (root ticket + sub-tickets,
   dependencies, effort estimates).
4. The operator sees the plan in the chat and approves it with a `task-proposal`
   card action (`addTasksAndRun` or `addTasks`).

No worker runs during Phase 1. The operator can reject, revise, or dismiss.
This is the standing rule from `orchestration-surface-requirements-addendum-re-s-19f74360f21.md`:
scoping and decomposition happen in the main thread, not inside a run.

**Phase 2 — out-of-chat (after operator approval)**

A ticket-maker agent (role: `triager`) is invoked to formalize the structure:

1. Creates the root ticket with `request_text` set to the verbatim request.
2. Creates each sub-ticket with `parent`, `depends_on`, `text`, `effort`, and
   `touches` set from the approved plan.
3. Returns ticket IDs to the chat so the task-proposal card collapses into a
   linked ticket list.

The ticket-maker does NOT execute any work. It only files the structure.
Workers (maker agents) pick sub-tickets from the queue in dependency order.

---

## 3. The ticket-maker agent

```yaml
slug: ticket-maker
role: triager
autonomy: reactive          # invoked by chat, never background-triggered
latitude: standard
triggers: []                # demand-only; no cron/loop/event
grants:
  - store: workshop://tasks/*
    verbs: [write, read]    # can create tickets in any scope
  - store: workshop://tasks/project:*
    verbs: [write, read]
```

The ticket-maker is the ONLY triager-role agent that writes to the task store
during decomposition. It must not write code, notes, or file-system artifacts.
A triager cannot self-trigger and cannot re-trigger itself through its own output
(structural guarantee, not a policy note).

Implementation: the ticket-maker is invoked as a short-lived subprocess from the
chat's `spawn_task` path when the operator submits the approved plan. It writes
tickets through the existing `task_dedup.dedup_add` API and returns the created
IDs. It is NOT a named agent in the agent registry — it is an inline invocation
using the `triager` role persona (see Phase 3 implementation in
`chat-tickets-orchestrator-plan-2026-07-15.md`).

---

## 4. Schema additions

The following fields are ADDED to the canonical Task schema (tasks.py). They are
additive and backward-compatible: tasks without these fields interpret them as `None`
or `[]`.

### 4.1 `request_text: str | None`

The verbatim original request from the operator, stored on the ROOT ticket only
(the ticket created to represent the decomposed work as a whole). Sub-tickets do
NOT carry `request_text`; they inherit context through the `parent` link.

Properties:

- Set ONCE at ticket creation by the ticket-maker. Immutable after filing.
- `None` for ad-hoc tickets created directly (not through the ticket-maker).
- `None` for sub-tickets (parent != None).
- Stored in plain text, no length cap (the operator's request is preserved in full).
- Not used by the worker or reviewer (it is provenance, not instruction). Workers
  read `text`, which is the scoped work description derived from the request.
- Exposed in the ticket drawer (read-only) as "Original request."

Rationale: `text` is the scoped deliverable (first line = title). `request_text`
is the unabridged source — needed for re-decomposition, audit, and cross-ticket
context ("why was this ticket filed?"). Storing it on the root avoids duplicating
a potentially large string on every sub-ticket.

### 4.2 `parent: str | None` (EXISTING, no change to field; semantics clarified)

Already exists in the schema (`tasks.py:15`). The ticket-maker populates it for
every sub-ticket.

Rules:
- A sub-ticket's `parent` is the ID of its immediate parent ticket.
- A root ticket has `parent: null`.
- `parent` is set at creation and never changed. Moving a ticket to a different
  parent is not supported (it would break the roll-up invariants).
- Maximum depth: 2 levels (root → child → grandchild). See ADR 0010.
  The ticket-maker enforces this: it rejects any proposed decomposition that
  would exceed depth 2.

`children` is the inverse of `parent` and is DERIVED, never stored. Any read
that needs a ticket's children queries `tasks.list(parent=ticket_id)`.

### 4.3 `depends_on: list[str]` (EXISTING, ticket-maker use formalized)

Already exists in the schema. The ticket-maker populates this for sub-tickets
in the same parent.

Rules:
- Edges are between sub-tickets in the SAME parent group (siblings only) for
  decomposition-generated tickets. The general dependency graph (ADR 0011)
  permits edges between any two tickets.
- Cycle detection runs on every `depends_on` mutation (see ADR 0011).
- The ticket-maker sets `depends_on` based on the operator-approved ordering
  from the in-chat plan. It does not infer dependencies automatically.

### 4.4 `roll_up_status` — derived, not stored

Roll-up is NOT a stored field. It is a DERIVED view computed from child statuses
whenever the parent ticket is read. See section 5.

---

## 5. Roll-up semantics

Roll-up is the mechanism by which a parent ticket's effective status reflects
the aggregate state of its sub-tickets. See ADR 0011 for the decision record.

### 5.1 The roll-up algorithm

When a child ticket's status changes, the task store checks the parent:

```
children = tasks.list(parent=parent_id)

if all(c.status in ("done", "cancelled") for c in children):
    # All done — parent can proceed to its own acceptance gate
    if parent.acceptance.kind == "reviewer":
        tasks.update(parent_id, status="in_review")
    elif parent.acceptance.kind == "operator":
        # surface in UI as "awaiting operator sign-off"; don't auto-transition
        pass
    elif parent.acceptance is None or parent.acceptance.kind == "command":
        # auto-run acceptance command if present, then mark done
        # same path as the reviewer gate in ralph.sh
        tasks.update(parent_id, status="in_review")

elif any(c.status == "blocked" for c in children):
    # At least one child is blocked — surface on parent for visibility
    # Do NOT auto-block the parent; a blocked child is already visible
    pass  # roll-up surfaces this in the UI only (see 5.2)

elif any(c.status == "doing" for c in children):
    if parent.status == "todo":
        tasks.update(parent_id, status="doing")  # parent is live when any child is
```

The roll-up transition runs as a post-write hook in `tasks.update` whenever a
task with a non-null `parent` changes status. It is NOT run during claim/lease
operations (only on status changes: todo→doing, doing→done, etc.).

### 5.2 UI representation

The ticket drawer shows a roll-up progress bar for any ticket with children:

```
Sub-tickets: ████████░░░░  4 / 6 done  (1 blocked)
```

Blocked children render their `blocked_reason` / `awaiting.question` inline in
the progress bar tooltip. The parent does not need a duplicate blocked state.

### 5.3 What the roll-up does NOT do

- Does not auto-mark a parent `done`. All terminal transitions go through the
  acceptance gate (reviewer, command, or operator). This is the invariant from
  the entity model: "in_review → done: reviewer approves."
- Does not cascade cancellations downward. Cancelling a parent does not auto-
  cancel children; the operator must cancel children explicitly.
- Does not block the parent when a child is blocked. The parent remains `doing`
  (or `todo` if no child has started yet); the blocked child surfaces in the
  UI. Two blocked states for the same logical work would confuse triage.

---

## 6. Queue eligibility with sub-tickets

The existing eligibility rule (entity-model-2026-07-18.md section 4.4):
> "highest-priority Ticket whose `depends_on` set is fully satisfied"

Extended for sub-tickets:

1. A **root ticket** (parent = null, has children) is NOT picked by the worker
   dispatcher directly. Only leaf tickets (no children) enter the runnable queue.
   The root ticket transitions via roll-up only.

2. A **sub-ticket** (parent != null) IS runnable. The worker picks it like any
   other ticket, subject to its `depends_on` set being satisfied.

3. A **grandchild ticket** (parent != null, grandparent != null) IS runnable,
   same as a child ticket. Depth-2 is the cap.

This means the dispatcher MUST filter out tickets with children (depth-cap
enforcement already handles this since >=3-level parents are rejected at creation).

The current `next-open` query in `project_tasks.py` must be updated to exclude
tickets that have at least one child with status != done/cancelled. Implementation
note: the simplest approach is a `has_open_children` flag set/cleared by the
roll-up hook, since re-querying all children on every `next-open` is O(N * M).

---

## 7. The clarifying-question flow

When the chat agent needs operator input before it can decompose a request, it
surfaces a "clarifying-question sub-chat card" inline in the thread:

```
┌─ Scope question ──────────────────────────────────────┐
│ Should the new auth flow replace the existing one     │
│ entirely, or run alongside it for a transition period?│
│                                                        │
│ [Replace entirely]  [Run alongside, then migrate]     │
│ [Discuss further ↗]  [Cancel]                          │
└────────────────────────────────────────────────────────┘
```

This card is a ui-card kind `clarifying-question`. It renders in-thread.
The operator clicks an option or types a free-text answer. The card collapses
on response; the chat agent continues decomposition with the answer in context.

The clarifying-question flow is SYNCHRONOUS with the in-chat session. The
ticket-maker agent (Phase 2) is not invoked until the operator approves the
final plan. There is no async loop waiting for answers.

---

## 8. Example: full ticket-maker flow

Operator: "Build the new billing reconciliation system — it needs to pull
transactions from Stripe, match them to our internal records, flag discrepancies,
and send a weekly summary email."

**Phase 1 in chat:**

Agent asks: "Should this replace the existing monthly report, or add to it?
Does the Stripe integration need an OAuth setup, or is a static API key enough?"

Operator: "Replace the monthly report. Static API key is fine."

Agent proposes:
```
[BILLING-RECON] Build billing reconciliation system
  ├── [B1] Add Stripe transaction fetch service (effort: M)
  │     touches: backend/services/stripe.py
  ├── [B2] Build internal-record match engine (effort: M, depends: B1)
  │     touches: backend/services/reconcile.py
  ├── [B3] Add discrepancy flagging + store (effort: S, depends: B2)
  │     touches: backend/services/reconcile.py
  └── [B4] Build weekly email summary (effort: S, depends: B3)
        touches: backend/services/mailer.py, scripts/reconcile_weekly.sh
```

Operator clicks "File and run" on the task-proposal card.

**Phase 2 (ticket-maker agent):**

Creates:
- Root ticket `BILLING-RECON` with `request_text = <full operator prose>`,
  `text = "Build billing reconciliation system\n..."`, no parent, no depends_on.
- Child ticket `B1` with parent=BILLING-RECON, depends_on=[], text=...
- Child ticket `B2` with parent=BILLING-RECON, depends_on=[B1], text=...
- Child ticket `B3` with parent=BILLING-RECON, depends_on=[B2], text=...
- Child ticket `B4` with parent=BILLING-RECON, depends_on=[B3], text=...

Returns ticket IDs. Chat card collapses into linked list.

**Queue dispatch:**

Workers pick up B1 (only `B1` is eligible: no depends_on, has no children).
B2 becomes eligible when B1 is done. B3 after B2. B4 after B3.

When B4 completes: roll-up detects all children done → BILLING-RECON moves to
`in_review` → reviewer/operator approves → `done`.

---

## 9. What this does NOT include

- Automatic dependency inference (the ticket-maker does not infer from the
  operator's prose; it uses the explicit order from the in-chat approved plan).
- Reassignment of sub-tickets to named agents (agent routing is a separate
  dispatch concern; the ticket-maker only files and links tickets).
- Auto-retry of failed decompositions (if the ticket-maker invocation fails,
  the chat surfaces the error and the operator can re-approve; no silent retry).
- Deep nesting or arbitrary DAG across unrelated tickets (the ticket-maker's
  sub-tickets are always grouped under one root; cross-root dependencies are an
  operator-added edge, not a ticket-maker output).
- Multi-level recursive decomposition within a run (tickets are already scoped
  at filing time; a running worker must not create sub-tickets of its own
  sub-tickets; it may `discovered_from` new follow-up tickets at the same or
  root level).

---

## 10. Build order

This design depends on:
- T0 (entity-model-2026-07-18.md) — vocabulary and schema spine. Done.
- `chat-tickets-orchestrator-plan-2026-07-15.md` Phase 3 (chat seam + task-proposal card). Required before Phase 2 of the ticket-maker is wired.

Implementation sequence:
1. **Schema**: Add `request_text` field to `tasks.py` (additive, backward-compatible). One migration pass to set `request_text: null` on existing records (no-op; the store ignores unknown fields already).
2. **Roll-up hook**: Add post-write roll-up check in `tasks.update`. Gated on parent-chain navigation.
3. **Depth-cap enforcement**: Add depth check in `tasks.create` — reject if `parent`'s own `parent` is not null AND `parent`'s parent's `parent` is not null (depth > 2).
4. **Dispatcher filter**: Update `next-open` to exclude tickets with open children.
5. **Ticket-maker invocation**: Wire from the chat `spawn_task` path when the `task-proposal` card carries a structured decomposition payload (vs. a single ticket).
6. **UI**: Ticket drawer roll-up progress bar; clarifying-question card kind.

---

## 11. Cross-references

- ADR 0010: Sub-ticket depth cap of 2 (`docs/decisions/0010-sub-ticket-depth-cap.md`)
- ADR 0011: Dependency and roll-up model (`docs/decisions/0011-dependency-rollup-model.md`)
- Entity model: `docs/platform-redesign/entity-model-2026-07-18.md` section 4 (Ticket)
- Dependency edge model: `shared/knowledge/ticket-dependencies-first-class-edge-model-adden-19f7440aaf0.md`
- Chat seam plan: `docs/platform-redesign/chat-tickets-orchestrator-plan-2026-07-15.md` Phase 3
