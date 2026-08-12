# Chat state ownership

**Status: current. Binding for anything under `frontend/src/components/chat/`,
`frontend/src/lib/chat*.ts`, `frontend/src/lib/draftSync.ts`,
`backend/routes/chat.py` and `backend/services/chat_*.py`.**

Written 2026-08-12, after two long messages the operator had sent ceased to
exist. This document is the rule that makes that class of failure impossible,
and the audit that says where every piece of chat state lives.

---

## The contract

**What the operator puts into a chat, and what the chat shows them, is a
promise. Whatever they can see is real, is the same on every device, and is
still there tomorrow.**

Three rules follow, and they are the whole design.

### 1. The server owns the conversation. The browser renders it.

Every fact about a conversation — what was said, in what order, what is running,
what is waiting to be sent, what is sitting unsent in the composer — lives on the
server. The browser holds a *view* of it, plus the single exception in rule 3.

A browser may render optimistically, and should: a bubble appears the instant
enter is pressed. But an optimistic copy is always visibly *in flight*, is always
reconciled against the server by `client_id`, and never becomes the only copy of
anything.

### 2. Everything in the transcript comes from the transcript.

A block on screen is derived from a log event, from the server's `active_run`,
or from this browser's own un-acknowledged sends. There is no fourth source.
A line rendered from component state is one the reader cannot verify, that a
second device does not show, and that a reload deletes. That is indistinguishable
from the application making things up.

This is pinned by `transcriptBlocks.test.ts` ("everything in the transcript comes
from the transcript").

### 3. The browser owns exactly one thing: a request that did not land.

When a POST fails — offline, backend restarting, edge 502 — the text exists
nowhere else, so this browser must hold it. That is the **outbox**
(`lib/chatSelection.ts`, `components/chat/useSendOutbox.ts`). It is written
*before* the request goes out and removed only when the server answers. It shows
as "Not sent" with a Retry, never as delivered.

Nothing else is browser-owned except view preferences: theme, scroll position,
which chat is selected, which panel is open. Losing one of those on a new device
costs nothing and surprises nobody.

---

## Where each piece of state lives

| State | Owner | Where | Notes |
|---|---|---|---|
| Messages, replies, run markers, context cuts, scope changes | server | `<thread>/log.jsonl` | append-only; nothing ever moves |
| The run in flight and its live output | server | `meta.json` + `live.json` | `active_run` on every sync |
| **Messages waiting to be sent** | **server** | `<thread>/queue.json` | was localStorage; see below |
| Queue held by Stop, and why a drain failed | server | `<thread>/queue.json` | was React state beside a localStorage queue |
| **Composer draft: text AND attached files** | **server** | `chat/drafts.json` | mirrored to localStorage for instant restore |
| Uploaded attachment bytes | server | `chat/files/` | uploaded on drop, before Send |
| In-chat card state (filed / ran) | server | `meta.card_states` | write-through; a failed write is surfaced |
| Thread title, model, effort, permission mode, scope | server | `meta.json` | |
| **A send whose request never landed** | **browser** | `ws-chat-outbox:<thread>` | the one exception, rule 3 |
| Which chat is selected, which provider | browser | `ws-chat-sel:`, `ws-chat-provider:` | a view preference |
| Scroll position | browser | `ws-chat-scroll:` (session) | a view preference |
| Theme, palette | browser | `ws-theme`, `ws-palette` | a view preference |

---

## What was wrong, and why it kept coming back

Each of these was reported separately, sometimes repeatedly, and each was fixed
as its own bug. They were one bug: **client state presented as server truth.**

**The type-ahead queue.** A chat runs one turn at a time, and some turns read no
input at all (a slash command, a voice turn), so a message sent during one has to
wait. It waited in `localStorage`, keyed by thread, drained by whichever tab
happened to be looking at that thread when the turn ended. Eleven numbered
invariants accumulated on that queue, each from a real incident.

On 2026-08-12 the operator ran `/compact` on thread `4102a2fe` at 17:43:43Z,
typed two long messages during the fold, and moved to another chat at 17:44:57Z.
The turn ended at 17:45:54Z with nothing bound to that thread to notice. He
reopened the chat later from a different device. The messages had never left the
first browser; the server had never heard of them; there was nothing to recover.
(He confirmed the diagnosis by returning to the original browser, where they were
still sitting and immediately sent themselves.)

The queue is `chat_queue.py` now. The server records the message, every device
sees it, and `chat_drain.py` sends it when the run ends — from the turn's own
completion path, not from a browser's socket handler.

**The composer draft.** Two symptoms, one cause. A draft fetched for chat A was
applied to whatever chat was on screen when the request resolved, so text
appeared in another conversation. And the draft was cleared under the key the
composer held *after* the send — but sending the first message of a blank chat
changes that key, so `::new` kept the text forever and every later blank chat
adopted it. Both are gone: async results are matched against the key they asked
about, and the server clears the draft it was told the message came from.

**Attached files.** Uploaded on arrival, so the bytes were safe, but the fact
that they belonged to *this composer* was React state. A reload restored the text
and not the screenshot it was about. Files are part of the draft now.

**Scope changes.** Rendered into the transcript from component state: visible
only in the browser that flipped the picker, gone on reload, absent on every
other device. It is a `scope` event in the log now.

---

## Rules for anyone touching this

- **A message is "sent" when the server says so, and not before.** The three
  acceptances are: a turn started for it, it was handed to the run in flight, or
  it was queued on the thread. Every one of them is the server's answer.
- **Never add a second owner of the same fact.** If a value exists on the server,
  the client reads it. A local mirror is acceptable only as a cache that is
  overwritten wholesale by the server's copy (`sameQueue` in `threadStore`), or
  as the outbox.
- **Never derive a storage key from a ref React assigns during render.** It holds
  the *outgoing* value through a switch. This destroyed the message queue on a
  thread switch once and the draft on a chat switch again; both now track the key
  explicitly and check it before applying anything asynchronous.
- **Never delete a record to clear it when ordering matters.** Write a tombstone
  with a timestamp. A deleted row makes a stale in-flight write look like the
  first thing anyone ever said.
- **A failed write is never swallowed.** Not with a comment saying it will
  re-sync later; it will not. Surface it, and leave the state un-marked so it can
  be retried.
- **Draining, batching and ordering of queued messages are the server's.** If you
  find yourself writing any of them in the browser, a message is being sent from
  a browser again.
