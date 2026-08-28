# Who is speaking in a thread

**Status: proposal, 2026-08-28.** The transcript half of
[`one-thread-many-senders-2026-08-27.md`](one-thread-many-senders-2026-08-27.md),
which decided the model and was never built. This adds nothing to that model. It
says what the schema, the log and the screen do with it, because the gap between
"decided" and "on screen" is where every defect below lives.

---

## 1. What was observed

Operator, 2026-08-28, looking at his own transcript:

> "this interface where you have sort of an ad hoc autonomous continuation ping is a
> bit strange and underdeveloped... these autonomous continuation messages show below
> the working message, and they never seem to be read. It's odd that they're here. It's
> odd that they are here in addition to the autonomous message shown above."

> "why are these autonomous? Like these just seem like responses from a subagent. Why
> is it being flagged as autonomous or shown as some special thing... I don't understand
> what the autonomous system message indicator is even supposed to be for."

> "if we have continuation pings or automated things to nudge an agent or chat along, it
> should not show up as though it's a user message, and you shouldn't just be slapping
> in some warning text inside the user message about how it's not a user message...
> shouldn't there just be clear roles for who is speaking in a chat?"

And the instruction that governs how this gets fixed:

> "Don't rush to act on them without thinking or planning... you're not actually
> designing a system and implementing it, you're just repurposing things."

---

## 2. The diagnosis: one missing field, four visible defects

Every symptom above is downstream of a single absence. **A message in this system
does not record who sent it.** There is a `role` (`user` / `assistant`), which is
the model API's field, not ours, and a `meta.autonomous` boolean bolted on beside
it. Neither answers "who put this here".

Measured on this tree today:

1. **The wake signal is stored as an operator message.**
   `chat_drain.request_continuation` (chat_drain.py:164) enqueues the seed through
   `chat_queue.enqueue(..., auto=True)`. The queue is the operator's own
   pending-message queue.
2. **The `auto` flag never reaches the browser.** `QueuedMessage`
   (chat_models.py:309) carries `client_id`, `text`, `ts`, `received_at`,
   `recorded`, `attachments`, `model`, `effort`. No sender, no `auto`. The client
   is not withholding the distinction; it was never sent one.
3. **So the client renders it in the operator's voice.** `ChatLog.tsx:212`:
   `queued.map(q => <div className="chat-msg user">)`. Every queued entry is a user
   bubble, dimmed, with an unsend control, because as far as the client knows the
   operator typed it.
4. **The disclaimer is the schema, written in prose.** `[AUTONOMOUS CONTINUATION -
   not a message from the operator]` exists because the field that would carry that
   fact does not. It is addressed to the model, and the operator reads it too,
   because a transcript has one audience per line and this line has two.

The fifth symptom, the duplicate, follows: one background agent finishing writes
**two records** into the thread. An assistant message carrying the report, and a
queued user message carrying instructions about the report. The second exists only
to make the thread take a turn.

---

## 3. `autonomous` is a guess about attendance, wearing a label

`meta.autonomous` is set by three producers (`run_report`, `question_report`,
`background_runs._deliver`) and rendered as a rail, a tint and a label
(`MessageList.tsx:218`, three strings: "Autonomous run", "Autonomous, needs you",
"Autonomous"). The comment that justifies the tier says what it actually means
(`lib/chat.ts:250`): a message the platform posted **"with nobody watching"**.

That is a producer's guess, at write time, about whether a person was attending.
The operator is right that it is not a category he needs. He is looking at a
subagent's report, in a chat he is sitting in, watching it arrive, labelled with a
word describing his own absence. The word was wrong before it was read.

`one-thread-many-senders` §2.3 already named this: attendance is an observed fact,
not a stored flag, and it decides exactly one thing, which is where a question goes
(inline versus the inbox).

---

## 4. The model: two fields, both taken from existing vocabulary

A message carries **who sent it** and **what it wants**. Neither is invented here;
both are terms of art this corpus already researched.

### 4.1 `sender` — who put this here

The field `one-thread-many-senders` §2.1 and §8.5 call for, stated there as the one
real change and the one place we are ahead of the standards rather than behind
them (A2A's `role` enum is `user | agent` where `user` means "the client", so a
human and a calling agent are indistinguishable).

    person            the operator typed it
    thread:<id>       another thread sent it
    run:<job-id>      a finished run reported into it
    agent:<slug>      a declared agent addressed it
    platform          the system itself (a lease taken, a budget hit)

`role` stays exactly as it is, because it is the model API's field and means "which
side of the conversation", not "who". A message with `sender: run:<id>` still has
`role: assistant`; a message with `sender: thread:<id>` still has `role: user`. One
is for the model, one is for the reader. Conflating them is how we got here.

### 4.2 `asks` — what it wants from a person

The human-in-the-loop taxonomy already in our own research
(`agent-interaction-model-2026-07-31.md`, from LangChain's agent inbox):

    notify     FYI. Nothing is waiting on anyone.
    question   Uncertain, and asking rather than guessing.
    review     Proposes an action, for approve / edit / reject.

This is the axis the reader actually needs, and it is the one the rail and the tint
should encode. "Does this want something from me" is a question every reader has.
"Was anybody watching when this was written" is not.

`one-fact-one-place-2026-08-22.md` §4 already states the routing rule in these
terms: a message goes inline only if its provenance names this thread AND it is
waiting on a person, and **a notify never goes inline, ever**. That rule survives
intact and gains the vocabulary to be stated in.

---

## 5. A wake signal is not a message

The continuation seed is not a message and should never have been one. Nothing
reads it but the model, once; it says nothing a reader needs; and it is written in
the operator's voice because the only door available was his queue.

> **Arming a thread is a property of the thread, not an entry in its transcript.**

A thread holds at most one pending wake reason: what armed it, when, and what it
refers to. The turn that follows begins because the thread was armed, and the
transcript shows the turn, not the arming. This is the same shape the doc's §2.2
already argues for ("what advances a thread is armed, not switched") and it removes
the duplicate outright: one background agent finishing writes ONE record, its
report, with `sender: run:<job-id>` and `asks: notify`.

The instructions currently stuffed into the seed ("read it and carry the
conversation forward, do not greet the operator") are prompt text. They belong in
the turn's assembled prompt, where every other instruction to the model lives, not
in the conversation the operator reads.

**What this also fixes, which he noticed and could not explain:** the seeds appeared
unread, sat below the running indicator, and then vanished. They vanished because a
queue entry is consumed when the turn drains, and they sat below because they were
pending sends. Both behaviours are correct for the operator's queue and nonsense for
a wake signal, which is not queued and is never "unread".

---

## 6. What the transcript shows

One rule, applied to every message:

- **`sender: person`** — the operator's bubble, right side. As today.
- **`sender: anything else`, `asks: notify`** — a quiet attributed block. It says
  who sent it ("Audit C", "the nightly sweep") and nothing else. No rail, no tint,
  no urgency, because nothing is waiting. This is where a subagent's report lands,
  and it is what the operator expected to see instead of a word about his own
  absence.
- **`asks: question` or `review`** — the emphasised treatment the current
  autonomous tier gives, because now something IS waiting on him. Inline when he is
  attending this thread, in the inbox when he is not, which is §2.3's live fact
  rather than a producer's guess.
- **Armed-thread wake** — nothing. Not a bubble, not a dimmed entry, not a system
  line. The turn it starts is the visible thing.

The word "autonomous" leaves the interface. It survives nowhere as a label, because
it never described the message; it described the writer's guess about the room.

---

## 7. Two adjacent things, in the same screenshot, that are NOT this

Kept separate deliberately so a schema change does not become a grab bag.

**The verb table.** `turnStatus.ts` maps each tool to a verb: Bash to "Running",
Read to "Reading", Agent to "Working". The operator asked once already for the
custom verbs to go and is seeing them again, because only one entry ("Delegating"
to "Working") was changed, not the table. The verbs are a per-tool switch on a
one-line indicator, while the step list under it already names every tool exactly.
Recommendation: **one word for a running turn**, and let the step list carry the
detail. That is a decision, not a bug.

**The dropped-stream block.** `useTurnStream.ts:405` emits "Lost the connection to
this turn. It is still running; reattaching." carrying `endpoint:
/api/chat/turns/<id>/stream`, which the problem block renders. Three faults: it
shows an internal path to a person who cannot act on it (the same defect as the
drift banner deleted this morning, which is app copy addressed to a developer); it
announces a state the indicator beside it is already showing; and the underlying
reconnect-budget exhaustion happens often enough that the operator saw it during
one turn, which is a real bug and not only a copy bug.

---

## 8. What to build, in order

1. **`sender` on a message, and on `QueuedMessage`.** The field, the API model, the
   generated client. Every existing message reads `person` or `assistant` by
   backfill; nothing is inferred at read time.
2. **The wake signal leaves the queue.** A thread's pending wake reason becomes
   thread state; `request_continuation` writes that instead of enqueuing; the seed's
   instruction text moves into prompt assembly. Removes the duplicate and the
   in-band disclaimer together.
3. **`asks` replaces `meta.autonomous`.** Three producers set it; the label table
   and the tier go; the inline-versus-inbox decision reads live attendance.
4. **The transcript rules of §6.**

1 and 2 are the ones that make the operator's screen make sense. 3 is the one that
makes the vocabulary honest. 4 is presentation and cannot start before 1.

**Do not build**, per `one-thread-many-senders` §10, which still holds: a new mode
enum, a conversion gesture, a live run transcript viewer, or attendance as a stored
setting.

---

## 9. Open, and genuinely his

1. **Does a subagent's report belong in the transcript at all?** §6 says yes, as a
   quiet attributed block. The alternative is that a notify never appears inline
   under any sender, and the thread simply continues, with the report reachable from
   the run. That is closer to the letter of the existing rule and further from what
   he asked for today.
2. **One verb for a running turn, or the per-tool table?** §7. He has asked twice
   for fewer custom verbs; this proposal reads that as "one word" and says so rather
   than guessing again.
