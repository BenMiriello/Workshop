# Chat state and feedback surfaces — audit, 2026-07-31

> Written after `/compact` reported itself as **"Filed as a ticket in general"**.
> That sentence was not a typo. It was four independent defects meeting in one
> place, and the reason they could meet is structural. This is the audit of how
> the chat's state and feedback actually work, what is now fixed, and what is
> still wrong.
>
> Companion: [`../platform-redesign/continuation-2026-07-31.md`](../platform-redesign/continuation-2026-07-31.md) (phase status, autonomy).

---

## 1. The reported symptom, decomposed

The operator pressed `/compact` and saw:

1. A banner at the top of the chat: *"Filed as a ticket in **general**. View in
   Work. Compacting the conversation…"* — for a compaction they never filed.
2. Roughly **30 seconds of nothing**.
3. Then the `/compact` bubble appeared in the log, the turn started, and the
   banner vanished — so feedback disappeared exactly when visible work began.

Four defects:

| # | Defect | Cause |
|---|---|---|
| D1 | Compaction rendered as ticket filing | one banner shape reused as a general status channel |
| D2 | ~30s dead wait | distillation awaited on the foreground path |
| D3 | Request bubble arrived after replies it preceded | bubble pushed after the round trip |
| D4 | Panels stack without bound | inbox and tasks were independent booleans |

### D1 — the renderer inferred the noun

The banner state was:

```ts
{ ok: true; text: string; scope: string; ran: boolean; runError?: string }
```

Nothing in that shape says *what kind of event it is*. The renderer worked it
out from which fields were empty:

```
ran           -> "Filed and running in <scope>"
runError      -> "Filed in <scope>, but could not run now"
neither       -> "Filed as a ticket in <scope>"      <-- the default
```

"Filed as a ticket" was the **fallback for an under-specified note**. Compaction
set the same shape to report progress, matched neither branch, and inherited the
ticket sentence plus a `scope` it had no business carrying and a "View in Work"
link pointing at a ticket that does not exist.

This is the whole failure in miniature: **a surface that infers meaning from
absent fields will describe every new caller as whatever its oldest caller was.**

### D2 — the foreground paid for background work

`_compact_thread` ended with `await _distill(to_fold, scope_key)`. Distillation
is a second Haiku call plus a knowledge-note read, dedupe and write. So a request
the operator is actively waiting on paid for **summarize + distill + dedupe**.

Nothing in the response depends on the distilled facts — they land in the scope's
`chat-distilled` note for *later* turns to recall. The caller was paying for work
whose result the caller never sees.

### D3 — the log lied about ordering

The `/compact` bubble was pushed only *after* the server returned. With D2 making
that 30 seconds, anything streaming in the meantime landed **above** it, so the
transcript claimed the operator asked to compact *after* output they had already
read.

### D4 — two drawers, no arbitration

`inboxOpen` and `tasksOpen` were independent booleans, both rendering bounded
panels between the toolbar and the log: 40vh of inbox stacked on 30vh of tasks
leaves a sliver of conversation on a phone. Nothing had ever decided whether they
were alternatives or layers.

---

## 2. Inventory: every feedback surface in the chat

This is the part that was never written down, and not having it written down is
why each new feature got its own surface.

| Surface | Class | Shows | Appears when | Clears when |
|---|---|---|---|---|
| Toolbar badges | count | inbox / task counts | count > 0 | count = 0 |
| Note banner | transient | ticket filed, compaction | after an action | dismiss, **or next send** (new) |
| Inbox drawer | standing | agent questions/reviews | toolbar toggle | toggle / Esc |
| Task drawer | standing | tickets in scope | toolbar toggle | toggle |
| Working indicator | live | Working / Reconnecting / Compacting + elapsed | turn streaming | turn ends |
| Queued bubbles | live | messages waiting for the turn | queue non-empty | drained |
| In-chat cards | embedded | ticket proposals + their filed/run state | model emits a card | server-backed card state |
| `.chat-note` | embedded | per-message action confirmations | message has `meta.notes` | never (part of transcript) |
| Voice strip | live | listening / heard / error | voice session active | session ends |
| Egress banner | error | blocked external network | error matches egress shape | error clears |
| `.chat-err` | error | everything else that failed | `err` set | next send |
| Jump-to-latest | nav | scrolled away from bottom | not at bottom | scroll to bottom |

**Twelve surfaces. Three of them are "an error"** (`.chat-err`, the egress
`StateBlock`, and the note banner's error kind) — plus a fourth if you count
`meta.error` on a message in the log. Each looks different.

**Two of them are "a ticket was filed"** — the note banner and the in-chat card.
They *were* produced by different code paths against different endpoints
(`POST /api/tasks` from `chat/uiCardActions.ts`, `POST
/api/chat/threads/:id/spawn-task` from `TicketDraftsDialog`), which is the
operator's "multiple different ways of showing that a ticket is filed". Both now
go through `spawn-task` (`6743aec`); the two surfaces differ in *trigger* only.

---

## 3. Why it happened

`Chat.tsx` is **2,560 lines with 41 `useState`, 22 `useRef` and 23 `useEffect`**
in a single component. There is no model of "chat activity" — there are forty-one
independent variables, and every feature added one or two more plus a place to
render them.

With no shared vocabulary for *what kinds of thing the chat can tell you*, the
cheapest way to ship any feature is to reuse the nearest existing banner or add
another one. Both happened. D1 is the first; D4 is the second.

The failure is not carelessness in any single change. Each individual reuse was
locally reasonable. **The system had no place to say "these are the kinds of
feedback that exist, and this is where each goes", so no change could be wrong.**

---

## 4. The rules this should follow

Three classes, distinguished by **lifetime**, not by feature:

1. **Live** — true only while something runs. Working indicator, queued bubbles,
   voice strip. Owned by the thing running; disappears when it stops. Never
   dismissible (dismissing a fact is meaningless).
2. **Transient** — the outcome of the last thing the operator did. One at a time,
   dismissible, and **superseded by the next action**. The note banner.
3. **Standing** — state that exists whether or not you look. Inbox, tasks. Behind
   a toolbar toggle with a live badge; **one open at a time**; bounded height.

And two invariants:

- **A note states its kind.** Never infer the sentence from which fields are
  empty. This is what made D1 possible and it will make the next D1 possible.
- **One verb, one path.** Filing a ticket should be one implementation with one
  result shape, whatever triggered it.

---

## 5. Fixed in this pass (commit `380190a` + follow-up)

- **`ChatNotice` is a discriminated union** — `ticket | compaction | error`. Only
  `ticket` carries a scope or links to Work. Compaction cannot inherit ticket
  language, because it names itself.
- **Distillation scheduled out of band** (`_schedule_distill`, mirroring
  `chat_naming.schedule`). Compact returns as soon as the fold it was asked for
  is saved. Applies to the automatic threshold path too, which was also making
  ordinary turns wait on it.
- **The `/compact` bubble is pushed when the request is made**, and taken back out
  on the paths where nothing happened (no live session and nothing folded, an
  error, or a turn starting mid-fold so the queue renders it instead).
- **One drawer at a time** — `drawer: "inbox" | "tasks" | null`. Both components
  stay mounted when unselected so badges stay live. `/inbox` joins `/tasks`.
- **Errors are no longer swallowed on empty chats.** `.chat-err` required
  `messages.length > 0`, which hid every error on a fresh chat — including the
  model-list load failure, which happens exactly there, and compact's own
  "this chat has no messages yet", a message that by construction could never be
  seen.
- **The note clears on send**, matching the error beside it. Previously it never
  expired, so a "Filed as a ticket" banner outlived the conversation by hours.

---

## 6. What is still wrong, in priority order

1. ~~**Two ticket-filing implementations**~~ **Done** (`6743aec`). Both callers
   now use the chat's `spawn-task`, which takes an explicit scope. The
   duplication was the visible half; the functional half was that only
   spawn-task recorded `source: {kind: chat, ref}`, so a ticket filed from a
   CARD had no provenance back to its conversation — and Ticket → chat could
   never resolve for those. Filing and running is also one request now, closing
   the window where a ticket filed but the separate run launch failed.
2. **Three error idioms.** Pick one presentation; egress stays special only
   because it carries a distinct remedy.
3. ~~**`Chat.tsx` is 2,560 lines / 41 `useState`.**~~ **Done** (`111923c`,
   `c0dace6`, `4a1c65b`): 2,560 → 1,823 lines, `useState` 41 → 31, `useRef`
   22 → 6, `useEffect` 23 → 14. Extracted, in rising order of entanglement:
   `chat/models.ts`, `chat/chatScope.ts`, `chat/Transcript.tsx`,
   `chat/ChatNotice.tsx` (type and renderer together, so a new kind cannot be
   added without deciding how it reads), `chat/useMessageQueue.ts`,
   `chat/useLogScroll.ts`, `chat/useVoiceSettle.ts`, `chat/useTurnStream.ts`.
   Each hook carries the rules it enforces, because in every case those rules
   were the accumulated residue of specific reported bugs and were previously
   discoverable only by reading the handler nesting.

   What is left of `Chat.tsx` is composition plus the toolbar/log/composer
   markup. The remaining seam, if it is ever worth taking, is the transcript
   render (`log`) — it is the largest single block left.
4. **P5/P6/P7** remain as recorded in the continuation brief. P7 (Runs and Agents
   detail pages, bidirectional chat links) is the operator's most-repeated
   outstanding complaint and should come first.

Do not enable background autonomy as part of any of the above.
