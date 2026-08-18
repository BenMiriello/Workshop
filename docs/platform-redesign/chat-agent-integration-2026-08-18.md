# Chat ↔ Agent integration: two tracks, one surface (2026-08-18)

Status: mostly ALREADY BUILT (verified in code 2026-08-18); this doc is now a
map of where each piece lives, not a build plan. Step 0 (the chat
delegation-contract fix) was shipped in this pass. An earlier draft of this doc
listed most of section 7 as "not yet built" -- that was written from the design
docs, not the live code, and was wrong. See section 7 for the receipts.
Single-operator system; voice-mode files are out of scope.

Origin: operator raised that the chat keeps launching "background" subagents,
promising to report back, and then going silent. Root cause diagnosed in code
(see §1). Operator then asked to unify how we handle Claude subagents and our own
Workshop agents in the chat, in the backend and the UI.

---

## 1. Why a "background" Claude subagent cannot work here

A chat turn is exactly one `claude -p` subprocess. The drive loop in
`build_streaming.py` (~line 2413) does nothing but poll that process and finalize
when it exits. The turn ends the instant the model emits text with no tool call;
turn retirement calls `os.killpg(pgid, SIGTERM)` on the whole group
(`_retire_turn_proc`), and a subagent is a child in that group. The only path
that would deliver a backgrounded subagent's result is the `<task-notification>`
on the next `--resume` (`build_streaming.py:2258`), i.e. the operator's next
message, by which point the child is already dead. Nothing schedules a follow-up
turn: `reattach_running_turns()` only re-adopts still-"running" journals after a
backend restart.

Conclusion: a Claude subagent is only ever an in-reply helper. "Background agent"
is a real need, but it is a different mechanism (a Workshop agent / a Run), not a
setting on a subagent.

Measured corroboration: 10 of 109 stored threads carry a report/background
promise near their tail.

---

## 2. The two tracks (concept)

There are two kinds of helper a chat can call. They are different mechanisms, not
two settings of one thing.

**Track A: Claude subagent.** A throwaway helper the chat spins up for one
bounded job (read a large tree, run a check) and hands a summary back. No name,
no memory, no saved record. Exists only for the reply that launched it, and MUST
finish before that reply ends. The chat drives it.

**Track B: Workshop agent.** One of our own named agents, or a user's. A durable
thing: name, `role` (maker/reviewer/triager/keeper/broker), persona, settings,
memory, owner. Runs as its own process outside the chat and leaves a Run record.
The chat can initiate and observe it, but does not execute its steps.

The rule: Track A is used up inside one reply; Track B is initiated by the chat
and then lives on its own.

---

## 3. Starting a Workshop agent is plural, and the chat is one initiator

There is no single "start" mechanism, and there does not need to be. A Workshop
agent run can begin from any of:

- manual start (operator, from the Agents page or a control);
- a resident dispatcher/runner picking up a filed ticket
  (`dispatch_triggers.sh` / `queue_runner.sh` / `project_ralph.sh`);
- always-on / pinged (an event trigger or an agent that stays resident);
- cron (a scheduled trigger in the agent definition);
- from a chat, in two forms:
  - direct invocation: "run agent X on this, here" (operator names the agent);
  - role routing: file work and the dispatcher assigns an agent by role.

All of these converge on the SAME Run entity. When a run is thread-linked, it
also surfaces in that chat via the one shared work element (§5). The chat does
not own starting; it is one of several ways in, and the one that adds a
thread link.

---

## 4. Data model (backend)

- A Claude subagent is NOT a data entity. It is a tool call inside a turn.
  Nothing is stored, deliberately. The only "fix" it needs is the prompt
  contract in §6 (shipped).
- A Workshop agent already IS a data entity: a markdown definition
  (`agents_registry.py`) with name, `role`, persona body, `triggers`, `limits`,
  `memory`, `owner_id`, optional `project_slug`. Any user minting an agent writes
  one of these. Running one already produces a Run; a ticket filed from a chat
  already records `source_kind="chat"` + `source_ref=<thread id>`
  (`models.py:414`, index `ix_ticket_source_chat`).

Both gaps this doc originally listed are ALREADY BUILT:

1. **Agent identity on a Run: BUILT.** `jobs.start()` already records
   `agent_id` and `chat_id` (`services/jobs.py`), and `routes/jobs._enrich`
   computes an `agent_name` display field. The frontend `Job` type carries both
   (`frontend/src/lib/jobs.ts:17,37`).
2. **Per-thread work-element state: BUILT.** The `card_states` sidecar exists
   end to end: `chat_store.set_card_state` + the `card_states` thread field,
   the `CardStates` / `StoredCardState` models (`routes/chat_models.py:371`),
   `POST /threads/{id}/card-state` (`routes/chat.py:956`), and the client state
   in `useChatThread.ts` read by `UiCard.tsx:373`. The card-duplication bug is
   already fixed: the server stopped appending a separate `job-status` card and
   the source card renders the run itself (`uiCardActions.ts:68`, `chat.py:1292`).

Net: Track B's data model is done. Track A needs no data model.

---

## 5. UI (frontend)

One shared element in the transcript: a card that streams live and then settles
into a result. The ONLY thing that differs between the tracks is the header
label (the actor):

- Track A renders headed "Subagent": anonymous, appears while the reply is
  produced, folds into that reply.
- Track B renders headed by the agent, e.g. "Commute builder (maker)": streams
  the Run status (working / blocked / done), links to the ticket and run, offers
  open-in-original-chat and open-in-new-chat. Its internal steps do NOT appear as
  chat messages, because the chat is observing, not driving.

Out-of-band: when the operator has left the chat, a Track B finish/block/question
also lands in the global inbox (`inbox.py`, typed notify / question / review),
which already exists and is scope-filtered.

This is ALREADY BUILT as the `work/` module: `WorkPanel.tsx`, `TicketRow.tsx`,
`TicketCard.tsx`, `QuestionView.tsx`, `AgentAvatar.tsx`, driven by `ticketCard.ts`
/ `workRows.ts`. It already shows the agent by name with an avatar
(`TicketCard.tsx:377`, `TicketRow.tsx:69`), the live clock and run status, the
receipt counts, and a blocked-run question/answer flow routed to the named agent
(`QuestionView.tsx`). The transcript polls it live (`chat.py:1171`). The only
thing not surfaced this way is a Track A subagent, which by design folds into the
reply rather than becoming a card.

---

## 6. Step 0 (SHIPPED this pass): the chat delegation contract

`DELEGATION_RULE` in `chat_context.py` previously described launching a subagent
without ever saying it must complete inside the reply. It now states plainly that
a subagent lives only inside the reply, that ending the turn kills it, that the
model must never promise a later report or launch-and-stop, and that work which
should run on its own and report back is a Workshop agent / a Run, not a
subagent. This is the change that stops the recurring failure at the source: the
model can no longer be truthfully told it may background a subagent.

---

## 7. What is already built (verified 2026-08-18), with receipts

1. Agent identity on the Run: `jobs.start()` records `agent_id` + `chat_id`;
   `routes/jobs._enrich` adds `agent_name` (`lib/jobs.ts:17,37`). BUILT.
2. `card_states` sidecar + card-duplication fix: `chat_store.set_card_state`,
   `CardStates` model, `POST /threads/{id}/card-state`, client `useChatThread` +
   `UiCard`; the separate `job-status` card was removed (`uiCardActions.ts:68`).
   BUILT.
3. Shared work element with the actor name: the `work/` module (WorkPanel,
   TicketRow, TicketCard, QuestionView, AgentAvatar). Shows agent name + avatar,
   live status/clock, receipts, and a question/answer flow. BUILT.
4. Chat start controls: role-routed file-and-run via the task-proposal card
   (`addTasksAndRun` in `uiCardActions.ts`), AND direct named invocation via an
   `@slug` mention that runs a specific agent in the chat (`chat.py:2960-3040`,
   gated by the agent's `mentionable` / `delegatable` flags). BUILT.

Plus the out-of-band surface (`inbox.py`, notify/question/review) already exists.

## 8. Genuinely optional remainder (decide if any is worth a ticket)

- A Track A subagent could render a lightweight "Subagent" chip in the transcript
  instead of only folding into the reply. Low value: it finishes inside the
  reply, so a persistent card would mostly be noise.
- A one-tap "run agent X here" affordance (a picker) as a shortcut for typing the
  `@slug` mention. Convenience only; the mechanism already exists.
- Runs still live in the `jobs.py` file store, not the Run table. That migration
  is its own already-filed ticket and is independent of this integration.

None of these are load-bearing. The two-track integration works today.
