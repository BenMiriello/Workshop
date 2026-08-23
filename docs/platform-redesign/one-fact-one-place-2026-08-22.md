# One fact, one place: collapsing the "needs you" surfaces (2026-08-22)

- Status: current
- **Supersedes section 6 (Plan) of `deciding-on-work-from-a-chat-2026-08-22.md`**,
  which is otherwise still correct. That plan's five steps all shipped today. This
  doc is what to do NEXT, and it is mostly deletion.
- Source: operator, 2026-08-22, on seeing the shipped result:

  > it seems like we just keep layering more and more abstractions and interfaces
  > and concepts and different UI elements and data elements and concepts on top
  > of each other instead of just coming up with a sensible design ... we CANNOT
  > just try to solve the old problems by just layering more and more, we MUST
  > also refactor and refine and come up with a cohesive distilled coherent
  > framework.

He is right, and this doc is the correction. **Nothing here adds a component.**
The net change is negative in every direction: fewer stores, fewer producers,
fewer sentences on screen.

---

## 1. What is actually on screen, counted

One fact ("ticket 8d406bbe is finished and needs a decision") is currently
rendered by **three stacked elements saying the same sentence**, in this order,
touching:

1. `AUTONOMOUS, NEEDS YOU` label, from `meta.kind` in `MessageList`.
2. The message body, from `services/question_report.py`:
   "The work on this ticket is finished and waiting on you. Accept it to mark it
   done, or send it back with a note saying what to change." Then a second
   paragraph: "Answer it in the Tickets panel in the chat toolbar, or open
   <ticket>."
3. `TicketDecision`, immediately below: heading "The work is finished and waiting
   on you", the two buttons, and a footnote "Accept marks it done. Sending it
   back returns it to the queue with your note."

So the operator is told what the decision is **three times** and where to make it
**twice**, one of which points at a different surface that is already redundant
because the buttons are right there.

This is not a design. It is two designs from two different days, both left in.
(2) was written when there was no inline block and pointing at the panel was the
only honest instruction. (3) was added this morning and nothing deleted (2).

## 2. Why it kept growing: a derived store nobody reduced

Measured on the live record, 2026-08-22:

    open inbox items:  753
    by kind:           notify 746, question 7
    by agent:          "autonomous run" 744, operator 6, memory-keeper 2, commute-builder 1
    dedupe_key prefix: run: 744, task: 6, other 3
    items with a chat_id: 2

Two things fall out of that and they decide the whole design.

**Every single item that actually needs the operator is derived from a ticket.**
All 6 carry `dedupe_key = task:<scope>:<id>`. Not one of them needs the inbox's
generality. The ticket already holds the fact (`status`, `awaiting`), and
`services/run_inbox.sync` exists solely to COPY that status into an inbox row.
Because the copy goes stale the moment a ticket moves, `inbox.reconcile()` has to
run on **every read** to rebuild it (`chat_work.py:158`, `routes/inbox.py`). A
materialised view that must be rebuilt on every read is not a view. It is a query
with a cache-invalidation problem bolted to it.

**746 of 753 items are notifications nobody has ever actioned.** 744 are "an
autonomous run ended with nobody watching". They never reach the chat (the
`surfaces_in_chat` scope filter saves it), so the panel looks fine while the
store behind it is 99% dead weight. That is the layering, visible as a number.

## 3. The distilled model

**One fact. One producer. One renderer per surface. A surface never restates what
the surface above it already said.**

### 3.1 The fact is the ticket, and there is no second copy

A ticket's status IS the fact:

    in_review   finished, needs a decision   -> accept | send back
    blocked     stuck, needs an answer       -> answer (+ grant / rule / cancel)

Everything that needs the operator is one of those two. So the panel, the badge
and the inline block all read **tickets**, filtered by status. `run_inbox.sync`
and the task-derived half of the inbox are deleted, and `reconcile()`-on-read
goes with them.

The inbox keeps only what has no ticket: an ambient agent with something to say
(3 items today). That is a real, small, different thing, and it stops pretending
to be the general "attention" abstraction.

### 3.2 The message and the block are ONE thing

The message stops carrying prose about the decision. It carries what it is
(`meta.kind`, `meta.ticket`) and the block renders it. The whole element becomes:

    AUTONOMOUS, NEEDS YOU
    Move the queue-runner lock off /tmp so one-runner-per-tree holds
    Finished 12m ago, 1 commit.        [Accept]  [Send back]

Three sentences become one line and two buttons. No "answer it in the Tickets
panel": the buttons are here. No footnote explaining the buttons: the buttons are
labelled.

### 3.3 The rule that stops it regrowing

The design system already says **"a constant is not a signal"** about hue. The
same rule governs copy, and it is the one this surface broke:

> **If every instance of a thing says the same sentence, that sentence is not
> information. Move it to the group heading, or delete it.**

Every `in_review` row says "The work on this ticket is finished and waiting on
you." That sentence is the definition of `in_review`. It belongs on the section
heading once, never on the row, and never again under the row.

## 4. When does work appear in the chat, and when not

The operator asked for this logic to be stated and checked. It has never been
written down.

**A chat shows work inline if and only if this conversation owes a decision on
it.** Two conditions, both required:

1. **Provenance**: the ticket's `source` names this thread. (Already enforced by
   `question_report.post_for_task`; §3 of the previous doc measured it and found
   it correct.)
2. **It is waiting on a person**: status is `in_review` or `blocked`.

Everything else is the panel:

- Work with no originating chat (3 of the 4 measured yesterday) has no
  conversation to interrupt.
- Work that is running, queued, or done needs no decision, so it is ambient. It
  belongs in the panel, which is where you look when you choose to.
- A `notify` never goes inline, ever. An FYI does not get to interrupt a
  conversation, and 744 of them is why.

The transcript is a record of **this conversation**. A decision this conversation
caused is part of it. A status change is not.

## 5. The two bugs this surfaced, and why they are the same bug

**A message is pushed, its buttons are polled.** The `ticket_question` message
arrives instantly over SSE. `TicketDecision` renders only once the work payload
contains the item, and that payload is a 20s poll (`useChatWork`) which
`usePoll` **pauses while the tab is hidden** (`whenHidden: "pause"`). So a
message and its own buttons travel by two different transports at two different
speeds and cannot be guaranteed to appear together. The operator saw the message
without the buttons and only got them by reloading. Under §3.1 this disappears:
the block reads the ticket, and the ticket can ride the same stream as the
message.

**The send gate and the send payload disagree about attachments.**
`canSendWith` (attachments.ts:175) blocks only on `status === "uploading"` and
then returns `true` on any non-empty text. `sendableRefs` then keeps only
`status === "ready"`. `take()` calls `sendableRefs` and `clear()` together. So an
attachment in the third state, `failed`, passes the gate on the strength of the
text, is dropped from the payload, and is wiped from the composer in the same
step. The operator sees chips, sends, and they vanish with no notice. Same shape
as the first bug: **two pieces of code deciding one thing, and disagreeing.**

The fix is one function: what a send CARRIES is computed once, and the gate is
derived from it. If it would drop something, the send refuses and says which.

## 6. Plan, ordered, each step a deletion

**Steps 3, 4 and 5 shipped together on 2026-08-22** (they are one change: 3 is
what deletes the copy, 4 is what empties the store, and 5 falls out of 3).
`services/run_inbox.py` is gone, `inbox.reconcile()` and the finished-run sweep
are gone, `services/chat_work` derives the "needs you" column from tickets whose
status is `in_review` or `blocked`, and `POST /inbox/{id}/resolve` answers the
TICKET when the id is the derived `task:<scope>:<id>`. The 753 open items the
retired producers had written were CLOSED, not erased (756 open -> 3). `notify`
survives, bounded: see the decision written at `KINDS` in `services/inbox.py`.

1. **Delete the redundant prose.** `question_report` writes the label and the
   ticket ref and nothing else; `TicketDecision` drops its footnote and its
   restated heading, and shows the ticket TITLE and what happened instead. One
   line, two buttons. (Frontend + backend, small, no new component.)
2. **Fix the send/carry disagreement.** One function decides what a send carries;
   the gate derives from it; a dropped attachment is never silently cleared.
3. **Read "needs you" from tickets, not from the inbox.** Delete
   `run_inbox.sync` and the task-derived inbox path, and with them
   `reconcile()`-on-read. The panel, the badge and the block query ticket status.
   The inbox keeps only chat-less agent notices.
4. **Retire the 744 dead run notifications.** A finished run is on the run and on
   its ticket. Decide whether `notify` survives at all as a kind; if it does, it
   is bounded and it never reaches a transcript.
5. **Attribution.** `run_inbox` sets `agent = assignee or owner`, so an
   operator-filed ticket reads "operator asks" with an `O`, i.e. you asking
   yourself. Whoever DID the work is the run's agent. Fixed for free by step 3,
   which reads the run.
6. **One host for the panel.** The panel is already one component with two hosts
   (`WorkPanel host="sheet" | popover`, chosen by `useIsWide`). The sheet is the
   better-reasoned one: it hangs from the toolbar over the log, inset, with a
   scrim that makes text entry exclusive. The popover anchors to a full-bleed
   toolbar button, which is why it lands hard right while the chat content is
   centred at 1200px. **Delete the popover host** and use the sheet at every
   width, its width following the chat column. Removes a host, a branch and a
   `useIsWide` call.

## 7. What is NOT wrong, so nobody "fixes" it

- **Two places is not the defect.** The panel and the inline block already read
  one payload through one hook, and answering in either resolves both
  (`ticketDecision.ts` header). That is correct and stays. The defect was three
  SENTENCES in one place, not two surfaces in the app.
- **`awaiting` as the single escalation primitive** stays. So does the shared
  decision vocabulary in `services/task_decision.py` from this morning.
- **The scope filter on what reaches a chat** (`surfaces_in_chat`) is correct and
  is the only reason the panel is readable today.

## 8. The open question the operator has to answer

Accepting finished work currently does **nothing to the code**. The worker
commits to the branch before the ticket ever reaches `in_review`; `accept` moves
`in_review -> done` and writes evidence, and that is all. So "review" here is
post-hoc bookkeeping, and the commit reference on the card implies a gate that
does not exist.

That is either fine (the loop is trusted, review is a record) or it is the whole
point of review (nothing lands until accepted), and the second requires worktree
isolation with a real merge on accept. Worktree mode exists but its success path
publishes a PR and this repo has no remote (`C3`). **This is a product decision
and it is not mine to make.** Until it is made, the copy must not imply a gate:
say "the change is already committed; accepting closes the ticket."
