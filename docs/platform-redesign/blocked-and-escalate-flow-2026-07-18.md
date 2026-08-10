# Blocked-and-escalate flow (2026-07-18)

- Status: accepted
- Depends on: entity-model-2026-07-18.md, resource-access-interface-spec-2026-07-18.md
- Source: operator asks B1/B2/B3/B4 (operator-asks-2026-07-16.md)
- Reasoning detail: scopedness-and-escalation-2026-07-16.md Part 6 and 7 (the full exploration; this doc is its distilled output)
- Consistent with: latitude-model-2026-07-18.md (section 3.2), clarifying-question flow (B5 doc, in progress)

---

## Summary

When a Run hits something it cannot resolve on its own -- a permission boundary, a
missing specification, a rule conflict, or a hard gate -- it raises a **Block**. The
Block parks the Ticket, notifies the operator, and presents a structured request in the
Work page review tab. The operator resolves the Block with one of six verbs: approve
once, grant standing access, amend a rule, rescope the ticket, reject, or defer. A new
Run begins when the Ticket re-enters `todo`. The blocked Run ends; it does not resume.

This flow covers every block source, not only permission denials. "Or anything" in
operator ask B1 is explicit: a decision the worker cannot make, a missing dependency,
an environment failure, and a reviewer rejection that needs the operator are all Blocks
and all land in the same queue.

---

## 1. Block signal shape

A Block is the `awaiting` field on a Ticket (entity-model-2026-07-18.md section 4.3).
The Ticket transitions from `doing` to `blocked` when any producer writes this record.

```
awaiting: {
  kind:        str,             # "permission" | "input" | "external" | "conflict" | other
  why:         str,             # why this Run cannot proceed without a resolution
  unblocks_if: str,             # what condition, in plain text, resolves this
  options:     list[str],       # suggested responses for kind "input"; may be empty
  default:     str | None,      # what fires at deadline if operator does not respond (None = wait)
  proposal:    str | None,      # agent's recommended resolution
  raised_by:   str,             # Run ID, "reviewer", or "system"
  dedupe_key:  str,             # hash of (kind, resource URI, verb) for grouping mass-blocks
  deadline:    datetime | None  # see section 4 (auto-resolve)
}
```

`kind` is an open string. The named values are the seed set; unanticipated cases use
a short slug rather than failing. Validate that it is a slug; do not validate
membership.

The `proposal` field matters most for programmatic resolution. When a block was caused
by an access gate, the gate fills `proposal` with the machine-readable ask in the form
`<verb> on <store URI>` (e.g. `write on workshop://store/notes/project-foo`). When no
programmatic expression exists (a judgment call, a missing spec), `proposal` is None.
The review tab uses the presence or absence of `proposal` to decide which resolution
path to present first (section 3).

`dedupe_key` is stable across retries so the review tab can group ten tasks blocked on
the same thing into one card rather than ten.

`deadline` is set by the resolved latitude (latitude-model-2026-07-18.md section 3.3):
strict -- no deadline; standard -- 4 hours; open -- 1 hour. At deadline the dispatcher
cron fires the `default` action if one is present and the block is reversible (section 4).

---

## 2. Block producers

Four things can raise a Block. All four write the same `awaiting` record to the same
Ticket field and land in the same review tab.

### 2.1 The access gate (`gate.check_action`)

A proposed action against a Store or file path is checked by `gate.check_action(job_ctx,
action)` before execution (proposed in scopedness-and-escalation-2026-07-16.md section
5.3; to be built in latitude model phase E5). When the check returns "block", the gate
constructs the `awaiting` record with `kind: "permission"` and populates `proposal`
from the action's (verb, uri) pair. The worker sees the block as a tool call returning
a structured denial rather than executing silently.

### 2.2 The worker itself (latitude-driven)

At `strict` latitude, a worker that reaches any underspecified decision raises a Block.
At `standard`, it raises a Block only for irreversible decisions. The worker writes the
`awaiting` record directly with `kind: "input"` or `kind: "conflict"`, including the
options it would choose between (in `options`) and what it would pick if given discretion
(in `default`).

This is the common case. Most Blocks come from here, not from the gate.

### 2.3 The reviewer

If the reviewer finds that the Ticket's acceptance criteria cannot be evaluated without
operator input -- an ambiguous specification, a missing ground-truth fixture -- it may
raise a Block with `raised_by: "reviewer"`. Reviewer-raised Blocks follow stricter rules
(section 6.2): they never auto-resolve at deadline, and a Grant minted from a
reviewer-raised escalation may not name the reviewer as the grantee.

### 2.4 The harness

Environment failures, missing tool binaries, and unresolvable dependency cycles that the
harness detects before or during dispatch produce Blocks with `kind: "environment"` or
`kind: "external"`. These are system-raised; `raised_by: "system"`.

---

## 3. Operator review surface

### 3.1 Where Blocks land: the Work page review tab

The Work page (`frontend/src/pages/Work.tsx`, currently three tabs: tickets, runs,
autonomy) gains a fourth tab: **Review**. The tab label carries a count badge when
blocked Tickets exist, using the `danger` status kind (`AutonomyPanel.tsx:363`).

This is not an approval queue sitting in front of routine work. Nothing routes through
it. If zero Runs block, the tab is empty. The tab is a view over `status == "blocked"`
Tickets, which the Ticket state machine has carried since `tasks.py:61`. It is a
dashboard; removing it would not affect whether work proceeds, only whether the operator
can see and answer Blocks.

### 3.2 Block cards (grouped by dedupe_key)

One card per `dedupe_key` group. Each card is built from `Card + Stack + Row` per the
Workshop Design System (frontend/CLAUDE.md).

Top to bottom:

**Header**: `StatusPill status="danger"` with the kind; the main question from `why`;
a tag showing the group count when greater than 1 ("8 tasks blocked"); age from
`awaiting` creation; a `Clock` icon + deadline when set.

**Body**: `unblocks_if` as the highlighted line (the condition the operator is being
asked to make true), followed by `options` if present.

**Metadata**: scope label; resolved latitude and its source (e.g. "standard -- from
project:foo"). A Block raised at `open` latitude is more significant: the worker judged
the question genuinely worth stopping for, and the operator should read it that way.

**Tasks**: collapsed list of Ticket links inside the group; expanded when the group
contains a single Ticket.

**Proposed resolution** (see section 3.3): prefilled based on `proposal` presence.

**Actions row**: the six operator verbs in priority order (section 3.4). Reject uses
`ConfirmDialog` per frontend/CLAUDE.md destructive-action rule.

Icon by kind (lucide only, per design system):

| kind | icon |
| --- | --- |
| permission | `KeyRound` |
| input | `GitBranch` |
| external | `AlertTriangle` |
| conflict | `AlertTriangle` |
| environment | `AlertTriangle` |
| other | `HelpCircle` |

### 3.3 Proposed resolution: how the system drives the offer toward a rule

The review tab proposes a resolution based on `proposal`:

**`proposal` is present** (named a resource + verb): offer a Grant, prefilled as a
read-only line showing verb and Store URI, with a `Select` for duration (once / standing
/ 7 days, mapping to caveats in `permissions.py:697`). The textual option is secondary.

**`proposal` is absent** (a judgment call or missing spec): offer a textual rule,
prefilled with `why` and any `options`. A scope selector defaults to the Ticket's scope
(never `global`; see section 6.3). Show the near-dupe warning if `learnings.py`'s
Jaccard check (`learnings.py:31-35`) finds an existing entry within 0.85 similarity.

**`kind == "input"` with `options` and `default`**: present the options as radio buttons.
Selecting one is the approve-once answer; a checkbox ("remember this choice") converts
it into a standing textual rule.

The operator never opens a rule editor. Rules are minted from Blocks with context
already filled in. That is operator ask B4 satisfied structurally rather than by
convention.

### 3.4 The six operator response verbs

| Verb | Effect | Mechanism |
| --- | --- | --- |
| **Approve once** | This Ticket proceeds; nothing durable changes | `update(status="todo", awaiting=None)` + answer appended to Ticket evidence + `restore_attempt` (section 5.2) |
| **Grant standing** | A durable, machine-checked programmatic rule | `mint_grant()` (`permissions.py:616`) prefilled from `awaiting.proposal`; scope-limited by the policy ceiling (A1 doc) |
| **Amend rule** | A durable textual rule | `learnings.add(scope=<ticket-scope>)` from the question + answer; never `global` by default |
| **Rescope ticket** | The ticket text was wrong, not the worker | Opens the ticket drawer; operator edits `text`, `touches`, `acceptance`; no new Run starts until saved |
| **Reject** | Do not proceed; the reason is recorded | `update(status="cancelled", blocked_reason=...)` + optional learning; `ConfirmDialog` required |
| **Defer** | Not now; skip this block this sweep | `update(status="parked")` (existing `parked` state, `tasks.py:57-60`) |

**Approve once** and **Grant standing** are the same gesture with different durability.
Their adjacency in the UI is intentional: the operator makes the durability choice at
the moment they have the context to make it, not before (B3).

**Grant standing** resolves the entire `dedupe_key` group automatically. On
`mint_grant()`, the platform sweeps open Blocks whose `proposal` the new Grant now
satisfies via `permissions.check()` and unblocks them without operator interaction
(stale-block guard; scopedness-and-escalation-2026-07-16.md section 8.2). This is why
the mass-block (ten tasks asking the same question) needs only one verb on one card.

### 3.5 Batch controls

For `kind: "input"` groups without a programmatic `proposal`, a "Select all" row allows
approve-once or defer across the selection. Grant standing is deliberately not a batch
verb: it resolves the group via mechanism (section 3.4), so a batch path to the same
effect would be a second code path that could diverge.

Empty-tab state: `StateBlock` with "No blocked work." The normal state should read as
healthy, not as an empty inbox.

### 3.6 Discord notification

The Discord bridge (the bridge agent) posts a notification when Blocks accumulate: "N blocks
waiting, oldest Xm" with a deep link to the review tab. One notification per scope per
hour, not one per Block. Mass-blocking is one problem; it pings once.

Discord is notification, not decision. Approvals are not recorded via chat. The answer
requires structure (which verb, which scope, which resource), and a chat log is not a
system of record for authorization.

---

## 4. Auto-resolve at deadline

A Block may resolve itself without operator action when all three hold:

1. The resolved latitude is `standard` or `open` (never `strict`).
2. `awaiting.default` is non-null (the worker proposed what it would do).
3. The blocked decision is reversible (see latitude-model-2026-07-18.md section 1.2 for
   the reversibility definition: a later `git revert` or one-line edit fully undoes it,
   with no external side effect and no third party having observed it).

At deadline the dispatcher cron takes the `default` action, records it as evidence on
the Ticket, marks the Block `auto_resolved`, and re-queues the Ticket as `todo`. The
resolved Block is surfaced in the review tab for post-hoc confirmation; the operator
can always override it with Rescope or Reject after the fact.

**Hard gates never auto-resolve at any latitude.** If `gate.check_action` blocked it, a
deadline cannot unblock it. The floor does not have a timeout.

**Reviewer-raised Blocks never auto-resolve** (section 2.3). If the reviewer cannot
evaluate acceptance, guessing produces a false approval, which is exactly the failure
mode the completion gate exists to prevent.

---

## 5. Resume semantics

### 5.1 The blocked Run ends

A Block is a Ticket state, not a Run state (entity-model-2026-07-18.md section 5.1).
When a worker raises a Block, the current Run ends. It does not pause; it does not hold
a worker slot or a lease. The Ticket carries the Block. A new Run begins when the Ticket
re-enters `todo`.

This is not a compromise. It is what Job=attempt already means and it is what
`tasks.py:783-788` already implements (releases lease and pid on any transition out of
`doing`, including `blocked`). Building resume would mean undoing that property and
reintroducing the possibility of a frozen slot.

The LLM context at block time is also stale by construction. A new Run re-reads the
Ticket, the evidence list (`tasks.py:29`), and the operator's answer, and begins from a
clean, correct context. The cost is re-running the pre-block work; the benefit is a
context whose most salient content is the authoritative answer rather than the worker's
dead-end reasoning about a question that has since been resolved.

### 5.2 Evidence handoff

The blocking Run's last action before ending is to write what it learned to the Ticket's
evidence list:

- What it was doing at block time.
- Why it blocked (the `awaiting.why` field, also stored separately for the review tab).
- Any partial results worth preserving (files written, searches completed, dead ends
  ruled out).

The operator's answer is appended as evidence when any resolution verb is applied
(existing behavior in `TaskPanel.tsx:291-296`). The new Run reads the Ticket text plus
the full evidence list and is ready immediately; no state is lost beyond the LLM's
in-flight context.

If partial results are substantial enough that they must not be re-computed, the correct
response is to create sub-tickets for the completed portions (`parent`, `tasks.py:15`).
A ticket blocking repeatedly because it is too large is a signal to split it, not to
resurrect the blocked Run.

### 5.3 Attempt accounting

Each Run attempt burns one entry in the Ticket's attempt count (`tasks.py:622`). A
Ticket at its cap is parked (`tasks.py:627-633`, `RECLAIM_ATTEMPT_CAP`).

For operator-answered Blocks, `restore_attempt()` (`tasks.py:745`) is called on the
unblock path so a legitimately-blocked-and-answered Ticket is not penalized as a failed
attempt. The restore is capped at two per Ticket. Past two, repeated blocking is a
signal: the Ticket is under-specified, the scope has a missing skill, or the worker is
gaming the block.

---

## 6. Edge cases

### 6.1 Mass-block (N tasks, one wall)

Expected case for a high-fanout sweep. Ten tasks asking for the same Grant in one
minute produce one card in the review tab via `dedupe_key` grouping (section 3.2). One
Grant standing resolves all ten via the stale-block sweep (section 3.4). The N-task
case costs the operator one action, not ten.

### 6.2 Reviewer-raised Blocks

If the reviewer raises a Block:

- Never auto-resolve at deadline, regardless of latitude.
- The Grant minted from the escalation may not name the reviewer Run as grantee.
  (A reviewer whose block mints authority to itself is self-approving.)
- Mark the Ticket `awaiting.raised_by: "reviewer"` so the review tab can present it
  differently: "Reviewer needs operator input before it can approve."

### 6.3 Textual rules and scope pollution

A textual rule minted from a Block in `project:foo` is a `project:foo` learning. The
scope selector in the review tab defaults to the Ticket's scope. `global` scope is a
deliberate act, not the default. `global` means "injected into every agent's context"
(`learnings.py:20-24`); that cost must be conscious.

This requires the learnings scope-key mismatch (F3 in `review-findings-2026-07-15.md`)
to be fixed before any rules are minted into the store. Minting rules into a mis-scoped
store that silently falls back to global would accelerate a confirmed HIGH bug. The
latitude model phases list this as E0 (blocking for E4).

### 6.4 Stale Block (already satisfied)

A Ticket is blocked asking for `write` on a Store. The operator grants it. Other Tickets
blocked on the same proposal are still in the queue.

Guard 1: at `mint_grant()` time, sweep open Blocks whose `proposal` the new Grant
satisfies via `permissions.check()` and auto-unblock them.

Guard 2: at render time, the review tab re-checks each `proposal` before displaying.
A Block satisfied since it was raised never reaches the operator's eyes.

Guard 1 handles the common case instantly. Guard 2 catches grants minted out-of-band.

### 6.5 Gaming the Block

A worker at `strict` latitude that discovers blocking produces operator answers may
block on a pretext to broaden its authority. Three defenses:

1. The answer is scoped, not accumulated. Approve-once resolves this escalation's
   `proposal`. Free text in the operator's answer is evidence; it does not widen
   the worker's Grants.
2. Attempt accounting already punishes block-looping (section 5.3). A worker cannot
   block indefinitely; the attempt cap parks it.
3. `dedupe_key` makes it visible. Repeated blocks on the same thing are one card with
   a rising count.

Defense 1 is the real one. The others are backstops. The threat is not a malicious
agent -- it is a model that found a cheap gradient. Removing the gradient (answer grants
nothing beyond the ask) is sufficient.

---

## 7. Relationship to the clarifying-question flow (B5)

The clarifying-question flow (B5, in progress) covers the case where the ticket-maker
or an in-progress Run needs input and the operator is actively present: the question
surfaces in the originating Chat as a mini sub-chat card the operator stays in until
cancel or submit.

The blocked-and-escalate flow (this doc) covers the complement: background or overnight
work that has already stopped, where the operator is not present. Same `awaiting` field;
different presentation surface.

Specifically:

- When a Run is launched from a Chat (chat-to-ticket via the ticket-maker), a Block
  raised by that Run surfaces in both: the Chat gets a mini card (B5 surface) and the
  Work review tab gets a Block card (this doc's surface). The Chat card is the fast
  path for an operator who is present; the review tab is the fallback.
- When a Run is launched by the autonomy dispatcher (no originating Chat), the Block
  surfaces only in the review tab and via the Discord notification.
- The six operator verbs (section 3.4) are the same regardless of which surface
  triggers them. Approve-once from a Chat card and approve-once from the review tab
  produce the same state change on the Ticket.
- B5's "stop/discuss controls" map to the Reject and Defer verbs in this doc's verb set.

Both surfaces read from and write to the same `awaiting` field on the Ticket. A
resolution from either surface clears the Block, re-queues the Ticket, and dismisses
both cards.

---

## 8. Implementation phases

These map to the latitude model phases (latitude-model-2026-07-18.md section 5), which
already sequence the escalation machinery.

**E0 -- F3 prerequisite (blocking for textual rule minting)**: Fix the learnings
scope-key mismatch (`scripts/project_ralph.sh:194`, `scripts/ralph.sh:331-347`) before
any Block can mint textual rules. E4 depends on E0.

**E3 -- structured escalation**: Widen `awaiting` in `tasks.py:23` to the shape in
section 1. Add the six operator response verbs to the task action endpoint. Add
`restore_attempt` on the operator-answered unblock path, capped at two per Ticket.
This is additive: `TaskPanel.tsx:291-310` and `JobDetail.tsx:399-403` keep working.

**E4 -- review tab**: Add the fourth "review" tab to `Work.tsx`. Build `ReviewQueue.tsx`
with grouped cards, six verbs, four list states, batch row. Wire Grant standing to
`mint_grant()` scoped to the Ticket's scope. Wire Amend to `learnings.add()` scoped to
the Ticket's scope. Add the stale-block render guard (section 6.4 guard 2). Wire the
Discord notification for first Block per scope per hour.

**E5 -- enforced floor (produces permission Blocks)**: Build `gate.check_action` with
the prefilled escalation on block (section 2.1). Wire via `PreToolUse` hook (builder
runtime) and the action dispatcher in `routes/chat.py` (assistant runtime). The
review tab and the escalation record already exist by E4; E5 adds a new producer of
the same record.

**E6 -- promotion and hygiene**: Promotion proposal cards from textual rules that
reach the provenance threshold. Disuse expiry surfacing (90-day check). Near-dupe
warning at rule mint time.

---

## 9. What this doc deliberately excludes

- **A separate resume path.** The blocked Run ends; the Ticket holds the state.
  A durable-execution engine for resuming LLM context is out of scope
  (entity-model-2026-07-18.md section 9.7; scopedness-and-escalation-2026-07-16.md
  Part 2 Temporal analysis).
- **Per-action confirmation for unblocked work.** The review tab is behind work that
  already stopped; it is not in front of work that has not started.
- **A rule editor.** Rules are minted from Blocks. They are listed read-only in
  Settings.
- **Audit journals of Block decisions.** Evidence on the Ticket records what happened
  as a byproduct of the resolution; a separate journal is redundant.
- **Trust ladders or per-agent performance scoring.** Multi-tenant governance,
  incompatible with the single-operator hard rule.
- **A policy DSL.** `mint_grant + check` in `permissions.py` already is the policy
  engine for machine-checked rules.
- **Automatic promotion.** An enforced rule minted without the operator confirming is
  the defensive-cruft failure arriving through the back door. Promotion is proposed,
  never automatic (latitude-model-2026-07-18.md section 4.3).
