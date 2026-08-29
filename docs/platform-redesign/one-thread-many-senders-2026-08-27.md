# One thread, many senders

**Date:** 2026-08-27
**Status:** **PARTLY BUILT, and superseded on the thread half.** The MESSAGE half
shipped 2026-08-27/28 and is live: `sender` and `asks` on a message, the wake
signal as thread state rather than a queued message, `meta.autonomous` deleted,
and the transcript rules in §6. The THREAD half (one entity, `armed_by` /
`attended_by`, thread-to-thread send) did NOT ship and is superseded by
`threads-branches-and-pages-2026-08-29.md`, which is the model of record. Note
`sender` lives in the message `meta` JSON, not a column.
**Supersedes:** D4 of `agent-interaction-model-2026-07-31.md` ("agent-initiated
messages do not land in the transcript"), and its restatement as law in
`agent-governance-addendum-2026-08-04.md` §4 rule 6 and
`tickets-in-chat-2026-08-15.md` §4. See §6.

## 0. What prompted this

The operator, 2026-08-27, after a session in which one chat read the state of
several others, sent messages into them, and reported back:

> "when I think about the distinction between a chat and an agent, I feel like
> I've been trying to draw sort of artificial lines between them. The further we
> go along in this, those lines sort of break down."

And, on why he keeps having to restate his own intentions:

> "it's all just sitting there. Like it's not done or documented in a consistent
> way that's discoverable."

The relay that prompted it was done by minting a local operator session cookie
and posting to the chat API as if it were the operator, because there is no
thread-to-thread send. That is the tell. The thing that felt closest to the
intended system had to be faked.

## 1. The finding

**One word, "agent", is carrying three independent facts.** Separate them and
the ambiguity the operator is describing disappears without a schema migration.

The three facts:

1. **What advances this thread.** A person sending, or something armed.
2. **Whether a person is attending it right now.**
3. **What it may do without asking.**

A "chat" is the combination (person advances it / person attending / asks about
everything). An "agent" is (trigger advances it / nobody attending / declared
autonomy). Those are two points in a three-dimensional space, and we have been
treating them as two types. Every awkward question the operator raised is a
point in that space with no name: an agent someone is watching, a chat that
worked ahead, a thread another thread is nudging.

## 2. The model

**There is one entity: a Thread.** It has an id, a scope, a transcript, and a
mailbox. Chats and agents are both threads. Nothing converts into anything.

### 2.1 A thread is addressable and anyone can send to it

The sender is a **field on the message**, not a property of the thread. The
operator, a trigger, a finished run, another thread: all senders. This is the
actor/mailbox shape, and it is the one part of this that is a real change.

Today the only entity that can open a thread is a human
(`chat_store.create_thread`, one caller, `POST /chat/threads`), and there is no
Thread-to-Thread edge anywhere in the design corpus. Chat-to-Ticket and
Chat-to-Run edges are designed; Chat-to-Chat is absent.

### 2.2 What advances a thread is armed, not switched

`chat_drain.request_continuation` already exists: it persists a seed and the
backend's `continuation_loop` starts the turn once the thread is idle. **A
thread holding a continuation seed is already a self-advancing thread.** The
mechanism for the unification is built and in production use by `run_report`
and `background_runs`.

So "automated mode" is not a mode. It is whether anything has been armed to
advance this thread. This matters for the operator's stated worry:

> "if you're working on a chat and then you come back the next day and then you
> find the system is sort of like already worked ahead when you didn't intend
> it to, that could be kind of weird."

Under an armed model that cannot happen, and it needs no ceremony to prevent. A
thread you opened by typing has never been armed. There is no switch to leave
in the wrong position.

### 2.3 Attendance is observed, not configured

Whether a person is looking at a thread is a **live fact**, not a setting. It
decides one thing: where a question goes. Someone is attending, so the question
renders inline; nobody is, so it goes to the inbox.

This concept is already in the code, unnamed. `frontend/src/lib/chat.ts:250`
justifies the `meta.autonomous` render tier with the words "**with nobody
watching**". Three producers set that flag (`run_report`, `question_report`,
`background_runs`). What they are actually encoding is the producer's *guess*
about attendance at write time. Naming it lets the same question route by live
reality instead.

The industry terms are Zendesk's: **handoff** (the human becomes first
responder) and **handback**. Not "mode".

### 2.4 Autonomy is unchanged

Per-agent, three values, default `reactive`, global brake as an overlay
(`autonomy-model-2026-07-18.md`, accepted). Nothing here touches it.

CORRECTION, 2026-08-27: an earlier revision of this section said "latitude
stays deleted", repeating `work-status-contract-2026-08-24.md` §6. That doc
*decided* to delete it; the deletion never happened. Measured on this tree:
`backend/services/latitude.py` is 211 lines, there is a live `<Select>` at
`frontend/src/components/TaskPanel.tsx:471`, and the field survives in 20+
files. It is set on 1 of 348 tickets, 0 of 215 file-store tasks and 0 of 25
agent definitions, so it is dead in use but alive in code. This is the exact
failure mode this corpus keeps hitting, committed here by the document that
warns about it.

## 3. So what is an agent

**An agent is a thread with a declared definition and something armed to
advance it.** The definition is the existing agent record: a name, a role from
the closed enum, triggers, limits. A chat is a thread with neither.

This answers the operator's question directly:

> "what if I start chatting with an agent, does it move over into chats. Does
> it continue working as an agent after I have chatted with it?"

Nothing moves and nothing stops. You sent a message to a thread; it is in the
transcript. It keeps its definition and its triggers. The single thing that
changed is that you are now attending it, so its next question comes to you
inline rather than to the inbox. No conversion, no migration, no button, and
nothing to undo when you close the tab.

The front end may still show two lists. Two views over one kind of thing is
normal and is what every peer system does (chat vs inbox, local vs cloud,
synchronous vs background). Two *entities* is what causes the ambiguity.

## 4. What this makes emerge

The operator's explicit test was that specific behaviours should fall out of
the model rather than be built as one-offs:

> "these are the sorts of things that I want to sort of naturally emerge from a
> well reasoned, well-refined core schema."

**A new thread succeeding an old one.** Not a `successor` field and not a new
entity. Thread A sends "continuing this work in B" to B; B sends the reverse.
Two messages with a sender field. Both the human and the agent in each thread
can see it. This is precisely what was faked with a minted cookie on 08-27.

**An agent asking the operator something.** Already ADR 0027 (a `prompt` card,
the agent ends its reply). Under attendance it routes: inline if attended,
inbox if not. Same producer, no branch in the producer.

**A chat nudging a run.** `control.steer` and `control.queue` already exist and
already use the correct industry vocabulary. They become ordinary sends with
different urgency, not a separate control plane.

**A thread taking over from a stuck one.** Send, with a sender field. No verb
needed.

## 5. Vocabulary, taken not invented

Every one of these is an existing term of art. We should use them.

- **steer** vs **queue** — inject into the current turn vs append for the next.
  Codex's words, echoed by Claude Code. We already use both in `control.py`.
- **handoff** / **handback** — the attendance transitions (Zendesk). Handback
  is the one people forget to define.
- **input-required** — the state of a thread paused on a person (A2A). It sits
  in the same enum as `working`, classified *interrupted*, not *terminal*.
- **ambient** — event-triggered rather than message-triggered (LangChain).
  Narrower than "autonomous"; it says nothing about oversight.
- Avoid **unattended** (it is RPA vocabulary, not agent vocabulary) and avoid
  **manual/automated mode**, which collapses all three axes back into one.

## 6. The record this corrects

D4 said agent-initiated messages do not land in the transcript. It was decided
once, restated as law twice, and **three shipped code paths do the opposite**:
`run_report.post_for_job`, `question_report.post_for_task`, and
`background_runs._deliver` all append ordinary assistant messages into live
threads. No document reconciles this.

That is not drift. It is a rule that lost to reality, three times, because the
rule was drawn on the wrong axis: it tried to keep senders out of a thread when
the real requirement was to keep *irrelevant* senders out. The predicate that
actually shipped is the right one and should replace D4 outright
(`one-fact-one-place-2026-08-22.md` §4): a message goes inline only if its
provenance names this thread AND it is waiting on a person. **A notify never
goes inline, ever.**

Related record casualties, for whoever picks this up:
- **Two chat-mode vocabularies, both live, neither superseded**: Direct/Delegated
  (`agent-governance-addendum` §1, "that is a binary, so the control is a
  binary") against direct/orchestrated/auto (`tickets-in-chat-2026-08-15` §7).
- **The modes may not mean anything.** `work-loop-core-design-2026-08-21.md` §3:
  they are prompt text only. "Either they gate real transitions or they go."
- **"Discuss in chat"** was called "the key differentiator" by an accepted doc
  and then removed by D4 "with nothing resolving the contradiction".

## 7. The external check

At the execution layer the convergence is close to unanimous: **"blocked on a
human" is a state of one entity, reached through the same primitive used to
wait on any external party.** Temporal (signal), Restate (awakeable), Step
Functions (task token), Inngest, Trigger.dev, DBOS, Azure Durable Functions,
LangGraph (`interrupt`), OpenAI Agents SDK, A2A. Not one of them ships a
distinct human-process type. A2A puts `input-required` in the same `TaskState`
enum as `working`.

The one dissenter is **BPMN**, which made User Task and Service Task different
XML elements twenty years ago. Its documented cost is exactly the problem being
described here: escalating automated work to a human is a *model change*, not a
runtime decision. That is the rigidity we would be buying by keeping two types.

**Worth taking from A2A: the two-level split.** A durable `contextId` (mutable,
addressable, long-lived) containing immutable `Task`s (each with its own
terminal lifecycle). That maps onto Thread-contains-Runs, and it gives one
addressable thread plus clean per-run audit without making "background" a
different kind of thread. It also matches what we already have, where a run's
report lands in the thread but the run itself is a closed record.

## 8. Sharp edges, from systems that hit them

1. **Two writers on one working tree is not the same as two writers on one
   conversation.** Claude Code documents concurrent multi-surface writing into a
   conversation. Devin and Cursor both make *environment* control exclusive, and
   Devin says why: "make sure Devin is paused before taking over the IDE to
   avoid simultaneous, conflicting changes." A thread anyone can send to is
   fine. A thread with a git worktree behind it needs a lease. Ours is designed
   and unbuilt (`workspace-ownership-and-leases-2026-08-22.md` B5-B7).
2. **A mailbox anyone can send to is a mailbox anyone can flood.** The oldest
   actor lesson (Erlang, Akka: unbounded by default, OOM under load). We have
   `chat_queue`; it needs to be the only door.
3. **Resume is not idempotent unless you make it so.** LangGraph re-runs the
   whole node after an interrupt; side effects before the pause run twice.
4. **Ordering is pairwise only.** With a human, a timer and two threads sending,
   there is no natural total order.
5. **Nobody gives you sender identity.** A2A's `role` enum is `user | agent`
   where `user` means "the client", so a human and a calling agent are
   indistinguishable. Every durable-execution system lacks it on the resume
   path. If who-sent-this matters, and here it does, we have to add it. That is
   the one place we are ahead of the standards rather than behind them.

## 9. The routing role already exists and has never run

The operator's complaint is that work gets dropped unless he restates it. The
role whose job that is exists in the closed enum:

> `triager` — routes/curates inbound work into tasks.
> (`agents_registry.py:173`)

Measured 2026-08-27: **one occurrence of "triager" in 11,957 job records, and it
is the job that wrote the document defining the role.** The only declared
triager is `agents/masini.md`, marked "INERT UNTIL FLIPPED LIVE".
`primitive-roles-2026-07-18.md` was already corrected on 2026-08-23 with
"the triager has never fired once".

So the function the operator performed by hand on 08-27, and had a chat
impersonate him to perform, is a declared role of this system that has never
executed. It cannot execute, because a triager with no thread-to-thread send
has no way to route anything into a conversation.

## 10. What to build first, and what not to

**Build one thing: a thread can send to a thread.** A real endpoint, with
`sender` on the message, going through the existing `chat_queue` door. That
single primitive gives successor links, the triager, cross-thread nudges, and
retires the minted-cookie hack. Everything else in this document is naming
things that already exist.

**Do not build:**
- A conversion or promotion gesture. There is nothing to convert, and promotion
  was locked out on 2026-07-12 and has stayed out.
- A live run transcript viewer. `autonomy-turn-on-2026-08-12.md` §7 already
  argues the case against: "richer progress UI has not been shown to improve
  oversight, and has coincided with less of it."
- A new mode enum. We have three unreconciled ones already. The next move on
  modes is deletion, not addition.
- Attendance as a stored setting. It is observed or it is nothing.

## 11. Open, and genuinely the operator's

1. **Does a person sending into an armed thread silence it?** Intercom treats a
   human reply as an implicit takeover; Zendesk makes it explicit. Silence-the-
   agent or merge-and-continue: this is a taste call, not a technical one.
2. **Is attendance per-person or per-thread?** Single-operator today, so
   per-thread is enough and cheaper. Worth knowing if that is a permanent
   assumption.
3. **Do the three chat modes gate anything, or do they go?** Open since
   2026-08-21 and blocking any coherent statement of what a thread is.
