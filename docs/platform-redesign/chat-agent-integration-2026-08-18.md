# Chat ↔ Agent integration: two tracks, one surface (2026-08-18)

Status: plan. Step 0 (the chat delegation-contract fix) is SHIPPED in this pass.
The rest is staged for build. Single-operator system; voice-mode files are out
of scope.

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

Gaps to add:

1. **Agent identity on a Run.** A Run must clearly record which agent produced it
   (agent slug + role), so the work element and the inbox can name the actor.
2. **Per-thread work-element state.** A `card_states` sidecar on the chat thread
   (already designed in `chat-work-linkage-2026-07-18.md`, not built): a small
   JSON map keyed by the work element's id, so the element persists across reload
   instead of reverting to an editable card. This also fixes the card-duplication
   bug described in that doc.

Net: Track B's data model is most of the way there; we add agent-identity-on-run
and the per-thread element state. Track A needs no data model.

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

Reuse, do not invent: the streaming element is the live-progress element from
`chat-work-linkage-2026-07-18.md`; this plan adds the actor-name header and the
Track A vs Track B label to it.

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

## 7. Build order for the rest (not yet built)

1. Agent identity on the Run record (backend), so the actor is nameable.
2. `card_states` sidecar + fix the card-duplication bug
   (per `chat-work-linkage-2026-07-18.md`).
3. The shared work element with the actor-name header (frontend), rendering both
   tracks.
4. Chat start controls: direct "run agent X here" + role-routed file-and-run,
   both writing the same Run and rendering the same element.

Each is a discrete, reviewable ticket when this leaves Direct mode.
