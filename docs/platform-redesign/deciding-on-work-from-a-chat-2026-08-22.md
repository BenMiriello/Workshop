# Deciding on work from a chat (2026-08-22)

- Status: current
- Supersedes nothing. Implements the decision half of
  `clarifying-question-flow-2026-07-18.md` (accepted, never built) and repairs a
  divergence from `entity-model-2026-07-18.md`.
- Source: operator, 2026-08-22, after answering a ticket question from the chat
  toolbar and being unable to tell whether anything had happened.

---

## 0. What the operator hit

Verbatim, because every clause below is a real defect:

> I selected one and had to go into it cause it was pretty ambiguous what all
> those 4, now 3, tickets or questions was about ... it just asked me for my
> input or response or something but it was unclear if it was actually blocked
> or needed anything ... the description and title were quite jargoney ... I came
> to infer that maybe this isnt actually blocked since it has a commit and
> somewhere in there implied it was done? Couldn't tell ... I came back and said
> 'i approve' ... But idk if that was necessary or if it did or changed
> anything ... you say 'answer here or in the ticket' but the ticket is like a
> url partial path. Not followable. I'm in a mobile app.

Five distinct defects. Four are confirmed below with the live record. One is a
convention that has never existed.

---

## 1. The headline: "Approve" did the opposite of approving

The word `approve` names two opposite outcomes in two routers.

`backend/routes/jobs.py`, `_ACTIONS`:

    "approve": ("done", "in_review")    # accept the worker's completion
    "reject":  ("todo", "in_review")    # send it back for rework

`backend/routes/tasks_all.py`, `review_task`, whose docstring reads
"Answer a **blocked** task":

    approve   release the block; the task is `todo` and runs again

The chat's Tickets panel posts to `POST /inbox/{id}/resolve`. That routes through
`routes/inbox._answer_the_task`, which maps any free-text answer to
`action="approve"` **on the second router**. So on a ticket whose status is
`in_review`, the chat's "Approve" executes what the Work page calls **reject**.

Confirmed on the live record for ticket `86f58aea` ("Pin the backend-to-renderer
seam with a contract test"), which the operator approved at 07:04:28Z:

    evidence: {"kind": "review", "ref": "approved: Approve"}
    status:   todo          (was in_review)
    attempts: 1             (refunded by restore_attempt)
    body:     ## Clarifications
              - **Q:** Worker finished this task. Review the change and approve it...
                **A:** Approve

Three things went wrong at once:

1. **The work was un-accepted.** The ticket's work is finished and committed
   (`d6ec10a`). It is now sitting in `todo` with `execution: "code"`, which makes
   it eligible for the unattended queue. The loop can claim it and redo work that
   is already in the tree.
2. **An attempt was refunded.** `restore_attempt` exists so a ticket that stopped
   to ask a good question is not parked as though it had failed. An acceptance is
   not a question, so the park cap silently stopped meaning what it says.
3. **The word "Approve" was written into the ticket body as a clarification.** A
   worker is handed the body and nothing else. The next attempt reads a
   Q/A pair whose answer is the bare word "Approve" and has to guess what it
   answers.

This is not a new opinion about what approval should mean. `entity-model-2026-07-18.md`
already ratifies the lifecycle:

    in_review -> done   reviewer approves
    in_review -> doing  reviewer requests changes

The inbox path diverges from the model of record. Two vocabularies for one act,
one of them wrong, is exactly the parallel-systems entropy the coherence effort
exists to remove.

### 1.1 The design rule

**A verb is named for its outcome, and one act has one name.**

`awaiting` stays the single escalation primitive; that part of the model is
good. What must stop is deciding what an answer MEANS without looking at what
was asked. There are two questions, and they take different answers:

| The ticket is | The question is | The answers are |
|---|---|---|
| `in_review` | the work is finished, is it right? | **accept** -> `done`, **send back** -> `todo` carrying the note |
| `blocked` | the worker is stuck | **answer** -> `todo` carrying the answer, plus grant / rule / cancel / dismiss |

So the resolution is one shared decision module, called by both routers, whose
verbs are `accept`, `send_back`, `answer`, `grant`, `rule`, `cancel`, `dismiss`.
`approve` is retired as a name in the task-review router because it cannot be
read correctly by anyone who has seen the other one.

Which verb an untyped free-text answer becomes is chosen **by the ticket's
status**, not assumed. A ticket in `in_review` gets `accept`/`send_back`; a
ticket in `blocked` gets `answer`.

`send_back` goes to `todo`, not to the entity model's `doing`. `doing` claims a
run that is not running. Recorded here as a deliberate, narrow divergence rather
than changed silently.

Only `answer` and `send_back` append to the body, because only they are
instructions to a future attempt. An acceptance is a fact about the past and
belongs in evidence.

---

## 2. The question was posted where it could not be answered

`services/question_report.py` composes a plain markdown message into the
originating chat, ending:

    Answer here, or on the ticket: /tickets/development/86f58aea1cea...

Three things wrong with that one line:

- **The path is not a link.** `workshop:ticket/<id>` has been a real ref kind
  since managed links shipped: `services/refs.py` `VALID_KINDS` includes
  `ticket`, `_resolve_ticket` returns its title, status and href, and
  `ChatMarkdown` renders it as a chip. The composer just never used it. On a
  phone a bare path is not followable at all.
- **"Answer here" is false.** Nothing reads a chat reply and writes it back to
  the ticket's `awaiting`. Typing an answer into the composer does nothing to
  the ticket. The only real paths are the toolbar panel and the ticket page.
- **Pointing away is the wrong gesture anyway.** The operator's instinct
  ("is that even the right idea or ux or ui here? Idk if it is! Seems like
  not right!") matches both the accepted design and the industry pattern: an
  approval lands where the person already is, with the context and the buttons
  inline, and never as a link to somewhere else
  ([OpenAI Agents SDK](https://openai.github.io/openai-agents-js/guides/human-in-the-loop/),
  [Human-in-the-loop UX pattern](https://aiuxplayground.com/pattern/human-in-the-loop/)).

`clarifying-question-flow-2026-07-18.md` is marked **accepted** and specifies
exactly this: the question renders as an inline card in the thread, answerable
without leaving the conversation. It was never built. What shipped was a text
message with a URL in it.

### 2.1 The precedent is already in the tree

`frontend/src/components/chat/ApprovalBlock.tsx` is the same problem solved
correctly for tool permissions, and its header states the rule:

> Rendered INLINE in the log, under the message that caused it — not in the strip
> above the transcript, which is for things pointing OUT of the chat.

A ticket the chat itself filed is not a thing pointing out of the chat. It is a
decision to make, and it belongs inline.

The message the backend already writes carries everything needed:
`meta: {kind: "ticket_question", ticket: <id>, scope, block_kind}`, and
`MessageList.tsx` already reads that kind to label the message
"Autonomous, needs you". So the hook point exists.

### 2.2 Why not a `ui-card`

A card is parsed out of message text against a fixed kind registry, and its
"already acted on" state lives in the `card_states` sidecar keyed by the card.
That records what THIS card did, not what happened to the ticket, so a question
answered on the ticket page would leave the in-chat card still offering buttons.

The block instead renders from the SAME live data the toolbar panel uses
(`useChatWork`), keyed by `meta.ticket`. One source of truth, two renderings, and
answering anywhere resolves both. Staleness stops being something to remember.

---

## 3. Why only one of the four asked in the chat

`question_report.post_for_task` posts only when `source.kind == "chat"` and
`source.ref` is a thread id. Measured:

    86f58aea  source: {"kind": "chat",     "ref": "4102a2fe..."}   -> asked in chat
    8d406bbe  source: {"kind": "agent",    "ref": null}            -> no chat to ask
    d2012fdc  source: {"kind": "operator", "ref": "chat-scan-2026-08-12"}
    d73229b4  source: {"kind": "operator", "ref": "chat-scan-2026-08-12"}

The last two look like they carry a chat reference and do not: `chat-scan-2026-08-12`
is a provenance string, not a thread id. So the gate is behaving correctly and
**this one is not a bug**. Three of the four genuinely have no originating
conversation, and the toolbar panel is the right and only surface for them.

Worth stating because the asymmetry reads as a defect from the outside, and the
fix for the felt problem is section 4, not a change here.

---

## 4. The list did not say what anything was about

Every `in_review` row carries the same body, from `scripts/project_ralph.sh`:

    "Worker finished this task. Review the change and approve it, or send it back with a note."

So four rows in the panel said the same sentence under four titles written for a
worker. Nothing said which of the two questions it was, and nothing said the
work was already committed.

A row must answer, before it is opened: **what is this, what state is it in, and
what am I being asked to do.** The state word already exists per row; what is
missing is that the ask is generic and the title is jargon. The generic sentence
should be replaced by the specific fact, which the record already holds: the
ticket is `in_review`, its last run committed, and the decision is accept or
send back.

---

## 5. Nobody has ever written down how to write a ticket

The operator asked directly: "Do we not have any convention or guidance on
writing ticket titles and descriptions?"

Half of one exists and is invisible.

- `services/ticket_format.py` owns four labels: **Objective / Output / Start from
  / Boundaries**, with synonyms, a composer and a regex table. It is genuinely
  good and it is enforced.
- `services/ticket_readiness.py` checks a ticket states them, and
  `tasks.title_warnings` warns on an empty, over-long or elided title.

What is missing is everything about *writing*, and one whole reader.

**The format serves the worker. Nothing serves the operator.** All four labels
are instructions to a machine that will execute the ticket. The person who opens
the ticket to decide something gets a wall of worker-instructions and has to
infer the state, which is exactly what happened: "I came to infer that maybe this
isnt actually blocked since it has a commit."

The offending ticket is one I wrote, and it is a fair example of every failure:

> **Pin the backend-to-renderer seam with a contract test**
> Objective: On 2026-08-14 a backend wire-format change put the operator's new
> message above a half-hour block of work while every ordering test stayed green...

"Pin", "seam" and "contract test" are all insider words; the title says how
rather than what; and the Objective opens with a dated story instead of the
outcome. External practice is consistent that a title should be short,
descriptive, actionable, name the affected system, and be skimmable in a list
without opening it ([Lullabot](https://www.lullabot.com/articles/guidelines-for-writing-proper-tickets-and-commits),
[Atlassian community](https://community.atlassian.com/forums/Jira-articles/How-to-write-a-useful-Jira-ticket/ba-p/2147004),
[OBO Academy](https://oboacademy.github.io/obook/explanation/writing-good-issues/)).

### 5.1 The convention

Written as `docs/ticket-writing.md`, and referenced from the places that produce
tickets so it is not another doc nobody reads.

**Title.** One line, under 80 characters, ideally under about ten words.
Imperative. Says WHAT changes and WHERE, in words a reader who has not been in
the conversation would use. It must be skimmable in a list: a title that needs
the body to make sense has failed. No metaphors for code structure ("seam",
"spine", "surface") unless the word is the actual name of the thing.

**Objective.** One sentence, stating the outcome in the present tense as though
already true ("the chat's Tickets panel answers a finished ticket by accepting
it"). Not the history, not the diagnosis. If a story is needed it goes lower,
under its own heading.

**Output.** The observable thing that proves it is done. Never "it works".

**Start from.** The files, symbols or commands to read first.

**Boundaries.** What not to touch and what not to build. "none: may touch
anything the objective requires" is a valid, stated answer; an absent line is
an omission.

**Plain language rule.** Prefer the ordinary word. Jargon is allowed when it is
the real name of a thing in the tree (a filename, a function, a status), and
should be glossed on first use otherwise. Anyone who has not read this
conversation is the audience, including the operator six weeks later.

**State, for a human reader.** A ticket that is waiting on a person says so at
the top of its body, in one line, in plain words: what happened, what is being
asked, and what each choice does. This is generated, not typed, so it cannot go
stale.

---

## 6. Plan

Ordered so nothing is half-present in the shared tree. Each step commits on its
own and is useful alone.

1. **One decision vocabulary** (backend). A shared decision module; verbs named
   for their outcome; the inbox chooses by ticket status; no clarification
   appended for an acceptance; no attempt refunded for an acceptance. Tests
   drive an `in_review` ticket through accept and through send-back.
2. **Repair the damaged record.** `86f58aea` back to the state the operator's
   answer meant: `done`, the bogus clarification removed, why recorded in
   evidence. Check the other three for the same shape.
3. **The question message** (backend). Managed ticket link, states plainly what
   is being asked and what each choice does, drops the false "answer here".
4. **Decide inline** (frontend). An in-transcript decision block under a
   `ticket_question` message, rendering from live ticket state, resolving in
   place. This is the piece that makes the toolbar panel optional rather than
   mandatory.
5. **The convention** (`docs/ticket-writing.md`), referenced from the ticket
   producers.

## 7. Not doing

- Changing the `awaiting` primitive. One escalation record for every reason to
  stop is right; only the answer routing was wrong.
- Routing a plain chat reply back to a ticket. It sounds obvious and it means
  guessing which of N open questions a sentence answers. The inline block makes
  the target explicit, which is the same convenience without the guess.
- Anything about the three tickets that have no originating chat (section 3).
