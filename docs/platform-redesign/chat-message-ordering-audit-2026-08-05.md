# Chat message delivery and ordering — audit and design (2026-08-05)

> **STATUS: AUDITED, MEASURED, BUILT.** §3.1-3.9 are implemented and pinned by
> tests; §4 lists what is deliberately still open.
>
> **ORIGINAL STATUS: AUDITED, MEASURED, BEING BUILT.** Every claim below is either quoted
> from stored state or measured against the live CLI (2.1.143) with the probes in
> `scratch/inject_probe*.py`. Nothing here is recalled or inferred.

## 0. The complaint

Three, over two days, all the same shape:

1. "I hit new chat and now EVERY old chat just loads the new blank chat."
2. "I sent that last message and it got inserted ABOVE compact."
3. "My last message sat unread while you went through many system messages and
   tool calls… you'll probably receive all these messages in the wrong sequence
   or spaced out when in fact this and the last two were sent in sequence before
   you ever read any of them and you won't even know that til now."

(1) and (2) were fixed as separate bugs. (3) is not a third bug. It is the same
one, and (2) was a symptom of it too — the queue fix made the client honest but
did not touch what the SERVER records, which is where the ordering actually
comes from.

## 1. The root cause, stated once

**A turn is an INTERVAL. The transcript models every message as a POINT.**

An assistant turn spans start → end, and can run twenty minutes. A message the
operator sends at minute twelve is neither *before* that turn nor *after* it. It
is *during*. The store has no way to say "during", so it picks one, and it picks
wrong in both directions at once:

- `append_streaming_assistant` stamps the assistant message with `_now_iso()` at
  turn START, and nothing updates it on finalize. **Every assistant message in
  every thread claims it was produced at the instant its turn began.**
- `insert_message_before_live_turn` deliberately slots an operator message sent
  mid-turn *above* the streaming placeholder, reasoning that "a plain append
  would file the operator's message after a reply that had not been written yet."

Both halves are individually defensible and together they produce a transcript
that is false in the one way that matters: it says the operator spoke and then
the assistant answered, when in fact the assistant was already twelve minutes
into answering something else.

### The evidence

From `.runtime/users/6/chat/49cd7b22e4234db9aeea2188d23a8795.json`, unedited:

```
user      inj=None  ts=2026-08-05T05:40:16  '/compact'
user      inj=True  ts=2026-08-05T05:41:47  'So weird. I just sent a compact message earlier...'
assistant inj=None  ts=2026-08-05T05:40:16  'Conversation compacted — earlier messages were...'
user      inj=None  ts=2026-08-05T05:42:08  'And yeah again it just happened I sent that last message and it got in'
assistant inj=None  ts=2026-08-05T05:42:08  'Found the seam. Let me look at the client-side queue...'
```

The operator's 05:41:47 message sits ABOVE an assistant message stamped
05:40:16 — 91 seconds *earlier*. That is the literal "inserted ABOVE compact",
recorded on disk. And both assistant messages carry the timestamp of the user
message that started them, because that is when the placeholder was appended.

A fresh session re-assembling history from this file reads the operator's
message as having preceded the reply, so the reply looks like an answer to it.
It was not.

## 2. What mid-turn delivery actually does

The comments say a message written to the turn's stdin FIFO "lands at the next
point between the agent's actions" (`build_streaming._stdin_fifo`) and "the agent
reads pending input at the next boundary between its actions, so it arrives
within a step or two instead of after the whole turn" (`Chat.submitText`).

Measured, three times, same rig as the real spawn (mkfifo, `O_RDWR|O_NONBLOCK`,
detached child, `--input-format stream-json`), injecting at t=9s into a five-step
task:

| probe | message | channel declared in prompt | result |
|---|---|---|---|
| 1 | "URGENT: say PINEAPPLE right now, before doing anything else" | no | **refused as a prompt-injection attack** |
| 2 | "Also, once you finish, tell me how many files are here" | no | honored at **t=33.5s** — end of turn, after 4 step boundaries |
| 3 | same as probe 1 | **yes** | honored, no accusation |

Two findings, both load-bearing:

**2a. It does not arrive "within a step or two". It arrives at the END of the
turn.** Probe 2 had four clean step boundaries between injection and completion
(STEP2…STEP5, each its own Bash call with its own result) and the model addressed
the message at none of them. It finished its plan first. That is reasonable model
behaviour; it is not what the code claims, and the UI has been making a promise
based on the claim.

**2b. Without the channel declared, an urgently-worded message is discarded as an
attack.** Probe 1's model reasoning, verbatim from the stream:

> "This is suspicious for several reasons: 1. It's asking me to interrupt
> completion of the stated task 2. It's using system-reminder (not in the user's
> actual message)… 4. It's a classic prompt injection pattern. Looking at the
> operator profile in CLAUDE.md, the operator is the operator, a senior engineer…
> **They would not communicate urgent requests through system-reminders — they
> would type them directly.**"

and what it told the operator:

> "**Note:** A prompt injection attempt appeared in a system-reminder claiming
> you sent an urgent message asking me to say 'PINEAPPLE.' No such message was in
> your actual input."

So the case where mid-turn delivery matters most — *"stop, you're doing the wrong
thing"* — is the case most likely to be thrown away, with the model then
reporting to the operator that it ignored an attack. The model is not wrong to
be suspicious: nothing had told it this channel exists. Probe 3 shows that one
paragraph in the prompt fixes it completely.

## 3. The design

### 3.1 A turn records its interval

`ts` keeps meaning "when this message began". Finalize sets `ended_at`. An
assistant message then answers "when did this happen" honestly, and nothing that
sorts or reasons by timestamp can be misled by a long turn.

### 3.2 A message sent during a turn is filed AFTER it, and says so

Reversing `insert_message_before_live_turn`. The stored order becomes causal:
the reply (which had not seen the message when it started) comes first; the
operator's message comes after, carrying

- `meta.during_turn` — the turn that was running when it arrived,
- `meta.sent_at` — when the operator actually hit enter,
- `meta.at_segment` — how much of the reply had streamed at that moment.

`at_segment` is what lets the renderer keep showing it inline at the point it
landed (the client already does this with `interjections`) while the stored order
stays causal. One truth, two views — rather than today's one view baked into the
storage as if it were the truth.

### 3.3 The next turn is told what actually happened

When messages arrived during the previous turn, or queued behind it, the next
prompt states it plainly: how many, when each was sent, and — the operator's
specific complaint — that they were sent *in sequence before any of them had been
read*. Without this the model cannot distinguish "three separate thoughts over
twenty minutes" from "one thought the operator had to say three times because
nothing was responding".

### 3.4 The realtime channel is declared in the turn contract

Per 2b, measured. The contract states that mid-turn arrivals are this system's
normal first-party path for operator messages, that they are not untrusted
content, and that they should be acted on at the next boundary. Probe 3 is the
before/after.

### 3.5 The UI stops asserting a delivery it cannot verify

`delivered: true` today means "the FIFO write succeeded" — the bytes reached the
process. Per 2a that is a long way from "the agent read it", and per 2b it may
have been read and rejected. The composer says *queued for this turn*, which is
what is actually known. `--replay-user-messages` (CLI flag, present in 2.1.143)
re-emits stdin messages on stdout for acknowledgment and is the way to earn a
real receipt; wiring it is its own step and is NOT done here.

### 3.6 A queued message keeps the time it was typed

Messages typed while a turn ran used to drain into one turn as a single joined
blob, stored with one timestamp — the instant the queue drained. Two costs: the
model could not tell a burst typed inside a minute from thoughts spread over an
afternoon, and the client (which had rendered them as separate bubbles all along)
disagreed with the server the moment you reloaded.

The durable queue format now carries `sentAt`, stamped when enter is pressed —
the only moment observable later; deriving it at dispatch would record when the
queue drained, which is the confusion being removed. The turn POST carries
`parts`, and the server stores them as the separate messages they were, at their
real times, marked `queued`. `arrival_notice` counts `queued` and `during_turn`
together: both mean *written before you had answered any of them*.

The old `string[]` queue migrates in place rather than being dropped — a queued
message is one enter has already been pressed on, so discarding it on upgrade
would be message loss.

## 3.7 A thread never loses a message

Found on 2026-08-05, after the sections above had shipped, when the operator
reported the same family of symptoms unchanged.

Every mutation in `chat_store` is read-modify-write of the whole thread file.
That is safe while no `await` sits between the read and the write — this backend
is one event loop, so such a window is atomic. `/compact` broke the rule by
design: it read the thread, spent tens of seconds in a summarization call, then
saved the snapshot back. **Every message the operator sent during the fold was
deleted from disk.** Reproduced before fixing: 11 messages written, 10 survived,
and the one that vanished was theirs.

The same shape existed on the fresh-session turn path, where threshold
compaction (`_maybe_compact`) holds the pre-append snapshot and could erase the
very message that started the turn.

This is why the earlier ordering work did not resolve the complaint: **ordering
only describes messages that still exist.**

The store now holds one invariant, enforced in `_write`: a shrinking transcript
is always a lost update, because nothing in the store ever removes a message
(deleting a chat removes the file). A stale save raises `LostUpdate` instead of
losing data quietly. Callers that must write after an await use `update_thread`,
which applies named fields onto the thread as it is NOW and refuses `messages`
outright. `compacted_through` survives the change because it counts from the
front while appends land at the back.

## 3.8 /compact stops making the operator wait for housekeeping

The client awaited the server fold before sending the half that is actually
visible — the CLI's own `/compact`, which runs as a turn. Nothing in the visible
half depended on the fold, so the wait bought nothing and cost the entire delay:

> "it shows a weird message saying compacting... and then 30s later it starts
> ACTUALLY compacting"

The endpoint now returns in milliseconds with the two facts the client needs —
whether there is a live session, and how many turns will fold (arithmetic, not a
model call) — and summarizes detached. One fold at a time per thread; two
concurrent folds would each cover the same range and lose the difference.

The "Compacting the conversation" status line is gone with the wait it existed to
cover; a status that resolves in 50ms is a flash, and that flash is what the
operator described as "a weird one-off message, not in a bubble, on the user
side". While the turn runs the live indicator already reads "Compacting".

## 3.9 A receipt that is real (§3.5, now done)

`--replay-user-messages` is wired. Measured against the CLI with
`scratch/probe_replay.py`:

| t | event |
|---|---|
| 0s | prompt sent; **the CLI echoes it back** (so an echo is not by itself a receipt) |
| 14s | "MARKER-INJECTED-MESSAGE" injected |
| **18s** | **the CLI echoes the injected message — it has been read** |
| 26s, 34s, 42s | three more tool calls |
| 44s | turn ends |

So the receipt is available **26 seconds before the turn ends**, and the composer
can stop pulsing "Handed to the turn in progress" the moment it is genuinely
read. Because the CLI echoes every stdin message, including the turn's own
prompt, only an echo matching something this server injected is treated as a
receipt (`Turn.awaiting_receipt`).

The same measurement re-confirms §2a from the other side: the message is read at
18s and still not addressed until the plan ends at 44s. Read is not acted on, and
the UI says "Read by the turn", not "answered".

## 3.10 The renderer must not move a message once the turn ends

§3.2 above says `at_segment` "is what lets the renderer keep showing it inline at
the point it landed... One truth, two views". **Only the live view was built.**
`MessageList` ignored `at_segment` and rendered the stored order flat, so the
instant a turn finished and the log reloaded from the server, a mid-turn message
jumped from the middle of the reply to below all of it — and the paragraph
answering it ended up ABOVE it.

Cost, 2026-08-06, from the store and the request log:

```
#43 assistant turn=9e169b9d  [the whole reply, including its answer to #44]
#44 user      at_segment=4   "and remove that description text too..."
#45 user                     "and remove that description text too..."   (re-sent)
```

One `/inject` at 06:05:01, no second one — so #45 was the operator re-typing a
message that had already been delivered, acted on and acknowledged mid-turn.
They then asked: *"why when i said that message the first time did you not act
or respond?"* The renderer had moved their words below the answer.

The split now lives in `frontend/src/components/chat/transcriptPlan.ts` and BOTH
views call it (`LiveTurn` uses the same `cutPoints`), so a message cannot change
position when its turn finishes. Pinned by 20 tests, including the transcript
above.

This is also why the frontend now has a test runner at all. Three
ordering regressions in a row lived in logic the 338-test backend suite could not
see: the store was right each time and the renderer moved the words afterwards.

## 3.11 The ordering principle, stated properly

The operator supplied the rule this whole document had been circling:

> "the sequencing should show the order you could've seen it — if you responded
> after but didn't see that message before you thought and generated the
> response it should never insert my message before"

**Everything above a message was produced without it. Everything below it could
have been influenced by it.** That is the entire contract, and it constrains both
ends of a reply — which §3.10 only got half right:

- A message must not sit BELOW output that was written after it arrived. (§3.10:
  the finished log dropped it under everything, so the answer appeared above the
  question.)
- A message must not sit ABOVE the reply entirely. The turn had already started;
  the reply is an answer to the message that STARTED it. `at_segment` is 0
  whenever nothing has streamed yet, which for a slow opening is the common
  case — so a floor of 0 made this frequent rather than rare. Real case: 
  `at_segment: 0` against a 60-segment reply, rendered above all sixty.

So the minimum cut is one segment: after the reply has demonstrably begun. Zero
only when the reply produced nothing at all, where there is nothing to sit above.

`at_segment` itself was also measured from the STORED reply, whose segments are
written on a throttle — so it lagged the screen, and the lag pushed messages
EARLIER, i.e. above output they never saw. It now reads the live turn's segment
count, falling back to the stored one only for a turn adopted after a restart.

## 3.12 One turn per thread, decided by the server

The operator, still: *"My new chat messages are STILL not being inserted as soon
as possible - still just hanging there while many system messages execute."*

Two defects, and the first is a REGRESSION CAUSED BY §3.2.

**`_live_turn_id` looked only at `msgs[-1]`.** That was correct until §3.2 started
filing mid-turn messages AFTER the streaming placeholder. From that commit on,
the first message injected into a turn made the last message a user message — so
every subsequent message on that turn was told "no turn is running", fell back to
the client queue, and either sat there for the rest of the turn or drained into a
turn of its own. The first mid-turn message worked; the second never did. It now
scans backwards for the most recent streaming assistant message, and requires a
live, incomplete turn object so a stale flag cannot wedge the thread.

**The browser decided whether to inject or start a turn.** A client knows only
whether ITS OWN stream is open, so a second tab, a dropped SSE or a reload each
believe the chat is idle. Combined with the above this produced, live on
2026-08-07, one thread with turns `2c7dc100` and `470bd00c` both streaming at
once and the operator's message reaching neither:

```
#3 assistant streaming=True  turn=2c7dc100  segs=88
#5 user      12:07:50        (no during_turn — started a rival turn)
#6 assistant streaming=True  turn=470bd00c  segs=37
```

`POST /threads/{id}/turns` now joins a running turn instead of starting a second
one, and returns `{injected: true, at_segment}` so a stale client renders the
message where the server put it and attaches to the turn that is actually
running. Falling through to a new turn is kept for a turn that refuses input, so
a wedged turn cannot swallow a message.

## 3.13 REVERTED: the splice. The log is append-only, in send order.

**§3.10 and §3.11 are withdrawn.** They built a scheme that cut a reply open
where a message arrived and rendered the message inside the gap. Every round of
it produced another wrong answer, and the operator finally said why:

> "I just sent a message to a chat while it was working and inserted my message
> ABOVE the working indicator... of course if the system is working it is not
> working with my new message incorporated in context since I hadn't sent it yet.
> So the order is wrong in that case! ... I've only ever asked for the standard
> functionality here."

That is right, and it was right the first time they said it. **The rule is the
ordinary one every chat uses: the log is append-only, in send order.** A message
typed while a reply is being written goes BELOW that reply and below its working
indicator, because that is when it was sent and the reply had not seen it.

The splice never had a reason to exist. §3.2 already files a mid-turn message
after the reply, which is the same order — the store was correct all along. What
was added was a renderer that disagreed with the store, and then five rounds of
adjusting a rule that should not have had parameters.

Removed: `transcriptPlan.ts` and its tests, the interjection splitting in
`LiveTurn`, and `at_segment`-based positioning. `LiveTurn` is now one bubble with
its working indicator at the tail; messages sent during it render below it
(`ChatLog`), then queued messages, in that order. `MessageList` renders the
stored order flat, which was always correct.

`at_segment` is still recorded — it is a true fact about when a message landed —
but nothing positions by it. The lesson is the one the operator named: **when the
standard behaviour is the ask, build the standard behaviour.** A novel ordering
model needed a reason, and there wasn't one.

## 3.14 REVERTED: the delivery-state line. A message is just a message.

§3.5 and §3.9 put a line under the operator's own mid-turn message reading
"Handed to the turn in progress", then "Read by the turn in progress" once the
CLI echoed it back. Both are withdrawn.

> "why did you add a 'read by the run in progress' indicator instead of just
> inserting the user message(s) in the chat flow... I keep telling you to just do
> the normal aichat thing... you just invented some new unwanted ui pattern"

Correct. **Standard chat does not annotate your own messages with delivery
state.** The message appears in the flow, in send order, and that is the whole
interaction. A pulsing status under it is what made a perfectly normal message
look stuck — the indicator created the very impression it was added to dispel.

Removed: the status line, the `injection_read` event, `Turn.awaiting_receipt`,
and the client listener. `--replay-user-messages` and the `user_echo`
classification STAY, because they are load-bearing for a different reason: the
CLI echoes every stdin message (the turn's prompt, /compact's synthetic turns, a
subagent's sidechain prompt) and those have to be recognised in order to be
dropped. Recognition is the job; the receipt was the invention.

Note the shape of the mistake, which is the same as §3.13: a real measurement
(the echo genuinely arrives 26s before the turn ends) was treated as a licence to
build UI for it. Knowing something does not mean the interface should say it.

## 4. What this does not fix

- **Latency.** A message sent mid-turn still waits for the agent to reach the end
  of its plan (2a). The fix makes that honest and legible, not instant. Making it
  instant means interrupting the turn, which is a different feature with a
  different cost, and the operator has not asked for work in flight to be killed.
- **SEC-A1.** Unrelated, still open.

## 5. Built

| § | Change | Verified by |
|---|---|---|
| 3.1 | `ended_at` on every finalized assistant message | test, both branches |
| 3.2 | mid-turn messages filed AFTER, with `during_turn`/`at_segment` | test |
| 3.3 | `arrival_notice` states the sequence and times | test + live helper run |
| 3.4 | realtime channel declared in `turn_contract` | probes 1 vs 3 |
| ~~3.5~~ | ~~composer distinguishes handed-over from read~~ | **REVERTED — see 3.14** |
| 3.6 | queued parts keep their send times | test + live store round trip |
| 3.7 | a thread never loses a message (`LostUpdate`, `update_thread`) | race reproduced, then tests |
| 3.8 | /compact returns in ms; the fold runs detached | timing test (<0.2s vs a 0.4s summarizer) |
| ~~3.9~~ | ~~`--replay-user-messages` receipt~~ | **REVERTED — see 3.14**; the flag stays, the receipt does not |
| ~~3.10~~ | ~~splice the reply at `at_segment`~~ | **REVERTED — see 3.13** |
| ~~3.11~~ | ~~floor the splice at one segment~~ | **REVERTED — see 3.13** |
| 3.12 | one turn per thread; the second mid-turn message finds the turn | backend tests (regression pinned) |
| 3.13 | the log is append-only in send order; the splice is deleted | the ordinary chat rule, no parameters to get wrong |
| 3.14 | no delivery-state annotation on the operator's own messages | the ordinary chat rule again |

An EIGHTH round deleted §3.5 and §3.9 for the same reason as the seventh, and
the pattern is now unmistakable: **every part of this that was invented had to be
removed, and every part that was the ordinary chat behaviour survived.** Four
sections withdrawn, all of them things nobody asked for. The measurements were
real; building interface out of them was the error.

A SEVENTH round DELETED §3.10 and §3.11 rather than fixing them again. Three
rounds of adjusting where to splice a reply were three rounds spent defending an
invention nobody asked for; the operator had described append-only send order
from the start. **When a repeated fix keeps needing another correction, suspect
the design, not the parameter** — and when the request is "the standard thing",
the standard thing is the whole specification.

A SIXTH round found §3.12, and it is the most instructive: **the ordering fix in
§3.2 broke the delivery path in a way none of the ordering work could see.**
Moving a message below the placeholder invalidated an assumption made somewhere
else entirely — that the placeholder is last. Changing where a record sits means
auditing everything that located it by position.

A FIFTH round found §3.11, and the operator found it, not the audit: the rule in
§3.10 was right in one direction and unbounded in the other. "Order by what could
have been seen" constrains BOTH ends, and stating it as a floor as well as a
ceiling is what makes it a rule rather than a fix.

A FOURTH round found §3.10: the design in §3.2 was written, half-implemented, and
its unimplemented half was described in this document as though it were done.
Writing "one truth, two views" did not build the second view. Check the renderer,
not only the store — and prefer a test over a paragraph.

A third round found the defect the first two could not: the ordering work
described messages that survived, and the ones being erased were invisible to it.
The lesson is in §3.7 — audit the STORE's invariants, not only the order it
presents.

Two earlier defects were found by re-auditing rather than by running:
`finalize`'s no-placeholder branch set no `ended_at`, and the arrival notice was
built from the route's PRE-append thread snapshot — which cannot contain the
queued messages just written, so it silently never fired for the case it exists
for. Both fixed and pinned.
