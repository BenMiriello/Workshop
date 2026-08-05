# Chat message delivery and ordering — audit and design (2026-08-05)

> **STATUS: AUDITED, MEASURED, BEING BUILT.** Every claim below is either quoted
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
> operator profile in CLAUDE.md, the operator is benmiriello, a senior engineer…
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

## 4. What this does not fix

- **Latency.** A message sent mid-turn still waits for the agent to reach the end
  of its plan (2a). The fix makes that honest and legible, not instant. Making it
  instant means interrupting the turn, which is a different feature with a
  different cost, and the operator has not asked for work in flight to be killed.
- **A real receipt.** `delivered` still means "the bytes reached the process".
  `--replay-user-messages` (present in CLI 2.1.143) re-emits stdin messages on
  stdout for acknowledgment and is how this earns a true one; not wired.
- **SEC-A1.** Unrelated, still open.

## 5. Built

| § | Change | Verified by |
|---|---|---|
| 3.1 | `ended_at` on every finalized assistant message | test, both branches |
| 3.2 | mid-turn messages filed AFTER, with `during_turn`/`at_segment` | test |
| 3.3 | `arrival_notice` states the sequence and times | test + live helper run |
| 3.4 | realtime channel declared in `turn_contract` | probes 1 vs 3 |
| 3.5 | composer says "Handed to the turn in progress" | — |
| 3.6 | queued parts keep their send times | test + live store round trip |

Two defects were found in this work by re-auditing it rather than by running it:
`finalize`'s no-placeholder branch set no `ended_at`, and the arrival notice was
built from the route's PRE-append thread snapshot — which cannot contain the
queued messages just written, so it silently never fired for the case it exists
for. Both fixed and pinned.
