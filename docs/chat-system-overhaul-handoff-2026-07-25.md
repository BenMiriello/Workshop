# Chat system overhaul — handoff (2026-07-25)

Status: **DONE and shipped** (commits `47c5a11`, `f39fa2f`). Typecheck, design gate, backend
import and a live browser pass are all clean. See [§8 Outcome](#8-outcome-what-was-actually-built)
for what was built against each item, what the diagnosis got wrong, and the two things
deliberately left. Sections 1 to 7 below are the ORIGINAL diagnosis, kept as written; where §8
contradicts them, §8 is right.

This document is the complete continuation context for the chat lifecycle work. It exists
because the conversation is about to be compacted. It records, in order: what the operator
said (verbatim), what was diagnosed, what has actually shipped, what is half-done, and what
remains. A reader who never saw the chat should be able to finish the work from this file
alone.

---

## 1. Operator statements, verbatim

Reproduced exactly as sent (including typos and speech-to-text artifacts). These are the
requirements. Do not paraphrase them away.

**(1)**
> Add opus 5 to the chat agent options now

**(2)**
> Also fix up the top bar of the chat since I currently cannot see the model selector on mobile. Rework and optimize it for mobile fully the top bar.

**(3)**
> I still don't see opus 5

**(4)**
> Also chat messages keep disappearing from the chat! Sometimes when I refresh the page if they're just staged messages. Another critical thing to fix after

**(5)**
> Yeah I can select opus 5 but there seems to be no functional way to actually create and use a new chat still - nothing actually gets me a new chat not even the new chat button and I just started one from within the chat ui but it still said working from the last chat and I sent a message but then came back and I can't find it anywhere. This is a mess. Research think and fully fix all of this.

**(6)**
> Yeah I just hit new chat and it took me back here

**(7)**
> So I just tried to use another old chat and clear it in order to create a new chat because I can't create an actual new one and so I ran slash clear and in the chat interface, it seemed to clear things and say that it had been cleared, but then when I refreshed and came back to it, the clearer had not happened and it was just back to how it had been so there's just a whole host of just completely misconfigured nonsense going on around here and you need to consider this like just a total review rethink over overhaul of the system because like the chat interface and functionality is just fundamentally broken if you cannot rely on messages actually executing or if the browser state does not reliably and actually usually does not at all reflect the actual state in the back end and I have no idea if past chat messages I sent our executing somewhere, but I can't access them so I don't know if I should restart those chat messages or if I did if it would cause like a if it would cause us to have two different chats to be working on the same thing at the same time and then I wouldn't be able to access either of them or whoever knows what so these are just the things I am catching, but you should take this as a signal that there is just a fundamental miss design and bad design of all this and you need to take an overview look of the Chat functionality and UI and how they are synchronized and how they display and how they work and just make sure they work the normal expected way as advertised I'm not asking for or expecting anything weird here honestly, just fix it. Take your time research get all the information. Get all the code. Make a plan. implement the plan don't wait don't stop. Don't ask for approval just do it. I'm only asking for like the obvious normal things that we want in a chat here.

**(8)**
> OK, so there are just a myriad of other issues that just came up now just by trying to execute that background task. First of all I tried to go to the run page to view it and it didn't really show much info and then I refreshed after a while and it just showed that it had errored but not really any info there about what it was. There's also no link back to the chat where it was going on from the run page or the job page like there's not much info anywhere on any of those pages as to what the hell is going on, but there is like a bunch of stuff happening and then when I come back to the chat interface here I see the same message again that I had already hit yes or like run and queue for and it had run, but it's still sitting here as though I had not clicked it before so this whole state management issue is still going on and is making it impossible for us to even fix things so you need to do this in chat without calling the sort of job Q system and basically don't create a job. Don't run anything just do it in the chat main thread here because the system doesn't work well enough for that to work and you need to fix the chat and then we can consider doing this other stuff you also need to just make sure there's some basic accountability or status or information or linkage back to the chat and other places from these other views because it's just fucking bewildering and you just get lost and things happen and like then it just fails. You don't know if it failed. You don't know if they ran. You can't look into it. You can't recover it. You can't follow up on it. there's just none of this stuff. None of these basic things are here and you need to start with fixing the chat and then also research and to think about all of these other issues in appointing to you too, and recognize that they are just like the first initial impression of the issue right like it is not the entire issue. I am telling you just the first things I am seeing and you need to take that as a starting point to go find the actual route, disconnect and issue and design flaws and centralize the design come up with a cohesive design that addresses all of the pages and issues and does not leave a bunch of junk lying around that doesn't work because it's gonna cause a huge headache for me and I need you to be the one that finds this stuff and solves it so go ahead and do all of the stuff now research think

**(9)**
> Or I think better yet would be if you documented everything that I'm saying and all of these issues and all of the things we've been working on and that need that we need to still work on just document that all of that verbatim completely and also everything else you know and need to know in order to continue this conversation because we're going to compact and then continue it later based off of what you write it needs to be complete specific and extensive

**(10)**
> I need a report to you that you were not in the middle of it in the chat interface there was no working indicator so it looked like you had just stopped and nothing had happened for a minute, but yet you're telling me that you're still working on it so I don't know what the fuck that disconnect is and that sort of fits in with everything else in terms of things just not making sense or being accurate or or correct also there is a great inconsistency in terms of when a message IQ or send actually gets read because sometimes it takes a long time for it to get red and you do like hundreds of steps before actually reading my message and sometimes whether or not that happens if you were doing work and then I send a message sometimes it does pick up right away or sooner and then sometimes when that happens, the whatever record of the past work you were doing just disappears like your system chat message or history or the log of what you did disappears and you just jump ahead to what I was saying more recently and all of this is kind of a mess. Where like it's if I send a new message like you should pick up all of the new messages on the next turn. It should not interrupt the current work you're doing unless I hit the stop button, but you should pick up all of the new messages if I send new messages after your current turn or step is over but it also should not just delete or remove whatever you were working on last that should sort of stay in the history log obviously.  So these are more just basic interface things that are not working and are all over the place and you need to document these in that as well as things to be fixed.

**(11)**
> And while we're added, I need to document another chat interface issue, which is the way that whenever new chat messages come in. It's sort of jumps right down to the very bottom of the chat to show the latest message so if I scroll back up in the chat to you an early message and I'm reading it and you're working on something lower down in the chat thread. It's impossible to read the message that I'm trying to read because it's constantly jumping back down so this is just another sort of basic mess issue that needs to be fixed and addressed and then on top of that if I am working on mobile and I'm trying to use the app and I'm trying to type in the input here that I am typing in currently in the chat whether I'm typing or dictating or whatever for some reason it's sort of this is Firefox but I think it's an issue with other browsers as well, but there will be this huge, big blank chin, completely blank, blocking most of the usable window that the application should be in above the keyboard and this is on iPhone iOS and so it's like really huge and kind of blocking the bottom of the page and sort of clipping off the bottom of the text input and I can just barely see the text being typed here, but everything is actually squeezed into a tiny space up in the top and then once I stop typing or I get rid of the keyboard then this goes away so that's another mess that needs to be addressed and fixed

(Speech-to-text artifacts: "a report to you" = "to report to you"; "message IQ" = "message I queue";
"get red" = "get read"; "while we're added" = "while we're at it"; "scroll back up in the chat to
you an early message" = "scroll back up in the chat to an earlier message".)

### 1a. Standing instructions extracted from the above

- **Do this work in the chat main thread. Do NOT file it as a queue ticket, do NOT create a
  job, do NOT run a background worker for it.** (Statement 8, explicit.) An earlier
  task-proposal card was filed and run; it errored and produced the confusion in statement 8.
  That path is closed for this work until chat itself is fixed.
- Do not stop to ask for approval mid-way (statements 7 and 8).
- Treat the reported symptoms as the *first* symptoms, not the whole defect. Find the root
  design flaw, fix it centrally, and do not leave partial/dead code lying around.
- "I'm only asking for like the obvious normal things that we want in a chat here." The bar is
  ordinary, correct chat behavior, not novel features.
- **Turn/message semantics the operator explicitly specified** (statement 10), treat as the spec:
  1. A new message must **never interrupt the running turn** — only the Stop button does that.
  2. When the current turn/step ends, the next turn must **pick up ALL messages queued since**,
     not one at a time.
  3. Sending a new message must **never delete or hide the record of the previous work**. The
     prior turn's output stays in the transcript history.
  4. The UI must show a truthful working indicator whenever a turn is actually running.

---

## 2. What has actually shipped (done and verified)

### 2a. Opus 5 added to the model options — DONE, LIVE

Three coordinated edits plus a runtime restart:

| Location | Change |
|---|---|
| `backend/services/llm_router.py` | added `claude-opus-5` to the tier/model table |
| `backend/services/chat_context.py` | added `claude-opus-5` to `PICKABLE_MODELS` and `MODEL_ALIASES` |
| `frontend/src/components/Chat.tsx` | `MODEL_LABELS["claude-opus-5"] = "Opus 5"`; `CONTEXT_WINDOW["claude-opus-5"] = 200_000` |

Key fact discovered while debugging "I still don't see opus 5": the dropdown prefers the
**live backend** provider list (`activeProviderObj?.models` from `GET /chat/providers`) and
only falls back to the frontend `MODEL_LABELS`. So a source edit alone changes nothing.

Two runtime steps were required and were performed:
1. **Backend restart.** The running uvicorn (pid 2699) had started 2026-07-21 with no
   `--reload`, so it kept serving the pre-edit model list. Restarted via
   `scripts/restart_backend.sh`; new pid 1497447 on port 8000. Verified opus-5 is now first in
   every provider's list.
2. **Frontend rebuild.** The public site (workshop.benmiriello.com) serves the static
   `frontend/dist`, **not** live Vite. Rebuilt with `scripts/build_workshop_public.sh`.

Operator confirmed in statement 5: "Yeah I can select opus 5". **This item is closed.**

Gotcha to remember: *any* backend or frontend change needs the restart + rebuild pair before
the operator can see it. This has already burned this task once.

### 2b. Chat toolbar mobile rework — CODE DONE, SHIPPED, NOT YET OPERATOR-CONFIRMED

Problem: `.chat-toolbar` was `flex-wrap: nowrap` with ~10 controls in one row. On a narrow
phone the row overflowed and the rightmost items (the model select, sitting after a spacer,
and the expand button) were clipped off-screen entirely. That is why the model selector was
invisible on mobile.

Fix applied in `frontend/src/components/Chat.tsx` + `frontend/src/styles.css`:
- Moved the model `<select>` **up beside the conversation picker** (the two dropdowns you
  actually change now sit adjacent, so the model can never be pushed off the end).
- Moved `<span className="chat-tb__spacer" />` to after the model select; it now grows to
  right-align the action buttons on desktop.
- Toolbar wraps instead of overflowing.
- Mobile media query drops the text labels and breaks the action buttons onto their own row,
  following the codebase's existing `.birds-label { display:none }` idiom.

`bash scripts/check_design.sh` passed clean and `npx tsc --noEmit` passed **at the time these
edits were made**. (It fails now for an unrelated reason — see §4.) Shipped to `dist`.

---

## 3. Root-cause diagnosis of the chat lifecycle (the real work)

The whole lifecycle was read end to end: `backend/services/chat_store.py`,
`backend/routes/chat.py`, `frontend/src/components/Chat.tsx`, `frontend/src/lib/chat.ts`.

### 3a. The backend is sound. Do not "fix" it.

- The user message is persisted **immediately** on receipt (`backend/routes/chat.py:1012`,
  `append_message`), before the model turn runs. So a sent message is on disk even if the
  turn dies.
- Threads are per-owner JSON files, listed newest-first. Delete works.
- Therefore: **messages were never actually lost.** They were unreachable because the client
  navigated away from the thread holding them. This matters for the operator's worry in
  statement 7 about whether past messages are "executing somewhere" — the transcripts exist
  on disk under the owner's thread files and can be recovered.

### 3b. Every reported bug is one frontend defect

**Root cause: the client has no durable notion of "which thread I am in." It never
authoritatively reconciles its state with the backend's threads.**

Concretely, the mount/scope effect (`Chat.tsx:885`, formerly ~856) unconditionally adopted
`list[0]` — the most recently touched thread — every single time it ran. From that one flaw:

1. **"New chat" does nothing durable.** `newChat()` (~`Chat.tsx:965` pre-edit) only clears
   React state (`activeId=null`, `messages=[]`). It creates nothing server-side and records no
   intent. The next time the mount effect runs it re-adopts `list[0]` — the thread you just
   left. → statements 5 and 6 ("I just hit new chat and it took me back here").
2. **`/clear` is a client-only illusion.** Same mechanism: the UI says it cleared, then the
   next mount re-adopts the same thread and the transcript is back. → statement 7.
3. **Provider/scope changes strand chats.** The effect is keyed on `scope`, which is driven by
   the Assistant-provider dropdown. Changing provider (or any remount) re-runs the effect and
   drops the thread you were in, including in-flight/streaming messages. Threads started under
   one provider become invisible under another.
4. **Optimistic messages appear to vanish.** Reconciliation happens only in the stream's
   `finish()` (~`Chat.tsx:781`). If a turn is interrupted, or the adopt logic lands you on a
   different thread first, the optimistic user bubble is never reconciled and disappears from
   view — even though the backend saved it. → statement 4, and "I sent a message but then came
   back and I can't find it anywhere" in statement 5.
5. **A newly created thread is not inserted into the local `threads` list**, so the
   conversation picker does not show it and you cannot navigate back to it.

### 3c. The intended fix (design)

Give the client an explicit, **persisted** selection, and make the loader honor it:

- **Separate the two concerns the mount effect conflated.** Listing threads is safe to re-run
  on scope change. *Choosing* the active thread is a distinct, guarded decision.
- **Persist the selection per scope**, including a first-class `NEW_CHAT` sentinel. "I asked
  for a blank chat" must survive a reload exactly as much as "I am in thread X" does.
- **Priority order when choosing the active thread** (this is already written into the
  effect's comment at `Chat.tsx:894-900`):
  1. the thread we are already on → re-hydrate it, never snap to newest
  2. an explicit new-chat intent → stay blank
  3. an `initialThreadId` link → resume a specific thread
  4. the last thread open in this scope → restore across refresh
  5. otherwise the most-recent thread
- **Persist the provider choice per contextKey**, so the chat reopens on the provider you left
  it on instead of snapping to the default and hiding threads.
- **Use `activeIdRef`** (a live mirror of `activeId`) so the loader can read the current thread
  *without* listing `activeId` in its deps, which would re-run it on every switch.
- **Insert a freshly created thread into the local list immediately** so the picker shows it.
- **Re-hydrate the active thread on mount/return** rather than relying solely on stream
  `finish()`, so interrupted turns reconcile.

---

## 4. Current tree state (READ BEFORE YOU TOUCH ANYTHING)

`git status --short` at time of writing:

```
 M .tasks.json
 M TODO.md
 M backend/services/chat_context.py      # opus-5 (done)
 M backend/services/llm_router.py        # opus-5 (done)
 M frontend/src/components/Chat.tsx      # opus-5 labels + toolbar rework + HALF-DONE lifecycle fix
 M frontend/src/styles.css               # toolbar mobile CSS (done)
?? frontend/src/lib/chatSelection.ts     # untracked, UNUSED
```

Nothing here is committed yet.

### 4a. THE IMMEDIATE PROBLEM: two competing implementations of the same fix

The lifecycle fix exists **twice**, and neither copy is fully wired:

| | `frontend/src/lib/chatSelection.ts` (untracked) | inline in `Chat.tsx:104-125` |
|---|---|---|
| sentinel | `NEW_CHAT = "new"` | `ACTIVE_NEW = "__new__"` |
| storage key | `ws-chat-sel:<scope>` | `ws-chat-active:<scope>` |
| provider memory | yes (`ws-chat-provider:<contextKey>`) | no |
| API | `readSelection` / `writeSelection` / `readProvider` / `writeProvider` | `readActiveSelection` / `writeActiveSelection` |
| used anywhere? | **no** — imported at `Chat.tsx:22`, zero call sites | yes, by the loader at `Chat.tsx:902/914` only |

`Chat.tsx:22` imports all four `chatSelection` symbols and uses none of them. That is exactly
why the typecheck fails:

```
src/components/Chat.tsx(22,1): error TS6192: All imports in import declaration are unused.
```

This is the duplicate-writer race that is already recorded in memory
(`duplicate-agent-file-race`): two Claude turns in the same session wrote overlapping fixes.
`chatSelection.ts` is the better module (it also solves the stranded-provider problem, defect
3b.3, which the inline version does not).

**Resolution to apply: keep `frontend/src/lib/chatSelection.ts`, delete the inline block at
`Chat.tsx:104-125`, and rewrite the loader's call sites to use `readSelection` /
`writeSelection` / `NEW_CHAT`.** Do not keep both. Leaving both is precisely the "bunch of
junk lying around that doesn't work" the operator warned about in statement 8.

### 4b. What is already correct in `Chat.tsx`

- `activeIdRef` mirror added (~`Chat.tsx:380`), correct.
- The loader at `Chat.tsx:885-935` already implements the full 5-step priority order and no
  longer blindly adopts `list[0]`. Its logic is right; only its storage helpers need to be
  repointed at `chatSelection.ts`.

### 4c. What is NOT done yet

- `newChat()` does not write the `NEW_CHAT` sentinel. **Without this the headline bug is still
  live** — the loader honors the sentinel but nothing ever sets it.
- `/clear` and `/new` slash handlers (~`Chat.tsx:1100`) do not write the sentinel either, and
  `/clear` still does not tell the backend to clear or delete anything. Decide and implement:
  does `/clear` delete the thread server-side, or start a new one and leave the old intact?
  (Recommended: start a new one; destructive delete belongs behind `ConfirmDialog` per the
  design system.)
- `openThread()` (~`Chat.tsx:994`) does not persist the selection.
- Provider choice is not persisted (`readProvider`/`writeProvider` unused).
- Newly created threads are not inserted into the local `threads` list.
- Optimistic-message re-hydration on mount/return is not implemented.
- Nothing has been re-typechecked, design-checked, rebuilt, or committed.

---

## 5. Remaining work, in order

1. **Unbreak the tree.** Resolve the duplicate implementation per §4a. Get
   `cd frontend && npx tsc --noEmit` clean.
2. **Wire the sentinel.** `newChat()`, `/new`, and `/clear` write `NEW_CHAT`; `openThread()`
   and the loader write the thread id. This is what actually fixes statements 5, 6, 7.
3. **Persist provider** per `contextKey` via `readProvider`/`writeProvider`.
4. **Insert new threads into the picker list** on creation.
5. **Re-hydrate the active thread on mount/return** so interrupted turns reconcile and
   optimistic bubbles stop vanishing (statement 4).
6. **Walk the flows manually** and confirm client state matches the backend at each step:
   create → switch → clear → refresh → send-then-refresh → change provider → interrupt a turn
   mid-stream → refresh.
7. **Gate:** `bash scripts/check_design.sh` and `cd frontend && npx tsc --noEmit`, both clean.
8. **Ship:** `scripts/build_workshop_public.sh` (the site serves `dist`, not Vite), then
   commit. Stage only the chat-related files; unrelated WIP from other agents may be in the
   tree.

### 5a-bis. Turn liveness, message pickup, and transcript retention (statement 10), NOT yet started

Same root disease as §3b — **live turn state lives only in ephemeral React state and is never
derived from or reconciled with the server** — but a different set of state than the thread
selection. Do this **after** the §5 lifecycle fix and **before** §5b. Four distinct defects,
each grounded in the code:

**A. The "Working…" indicator is client-local and lies when the socket is gone.**
`Chat.tsx:1502-1520` renders the indicator gated purely on the local `streaming` flag, which is
only ever set inside `attachStream` (`Chat.tsx:748`). Nothing derives liveness from the server.
So a turn that is genuinely running server-side shows **no indicator at all** whenever the
client never attached or lost the socket: after a refresh that lands on a different thread, in
a second tab, on another device, or after `handleDrop` (`Chat.tsx:836-850`) exhausts
`MAX_RECONNECTS` and calls `finish()` — which clears `streaming` even though the turn has not
ended. That is exactly what the operator saw: an idle-looking UI while work was in flight.
*Fix:* make liveness server-derived. The backend already marks it — `handleDrop` itself reads
`last.role === "assistant" && last.meta?.streaming`. Use that same signal on hydrate (the
re-attach at `Chat.tsx:869` already checks it) **and** on a low-frequency poll, so a client with
no socket still shows "Working" when the server says the turn is live. After `MAX_RECONNECTS`,
do not silently drop to idle: show a degraded-but-honest state and keep polling.

**B. The type-ahead queue is pure client state and loses messages.**
`queued` is `useState<string[]>([])` with a `queuedRef` mirror (`Chat.tsx:467-468`). It is never
persisted and never sent to the server. Close the tab or refresh while messages sit queued and
they are **silently destroyed, never delivered**. This is a real message-loss path, distinct
from the optimistic-bubble bug in §3b.
*Fix:* the queue must be durable. Preferred: POST queued text to the server so delivery does not
depend on the tab staying open. Minimum: persist it alongside the per-scope active-thread
selection so a refresh restores it.

**C. Pickup latency and one-at-a-time dispatch (violates spec items 1 and 2).**
`submitText` (`Chat.tsx:1076-1085`) queues instead of sending whenever `streaming` is true, and
`dispatchNext` (`Chat.tsx:736-744`) shifts exactly **one** message off the queue per completed
turn. Consequences the operator reported verbatim: pickup latency equals the entire remaining
duration of the current turn ("you do like hundreds of steps before actually reading my
message"), and N queued messages become N sequential turns instead of one turn that sees all of
them. The non-interrupting half of the behavior is correct and must be preserved.
*Fix:* `dispatchNext` should **drain the whole queue into a single next turn**, not shift one.
That satisfies "you should pick up all of the new messages" while keeping "it should not
interrupt the current work."

**D. Starting the next turn wipes the previous turn's visible work (violates spec item 3).**
The live turn's content accumulates in `pendingSegments`, and `attachStream` unconditionally
does `setPendingSegments([])` at `Chat.tsx:749`. `dispatchNext` appends the user bubble and
immediately calls `sendBuild`, so the previous turn's segments are cleared from the live view.
They only survive if `finish()` already reconciled them into `messages` from the server. When a
turn is stopped, interrupted, or reconciliation did not run, that work **disappears from the
transcript** — the operator's "the log of what you did disappears and you just jump ahead."
*Fix:* never clear `pendingSegments` without first re-hydrating the thread from the server, so
the finished turn is in `messages` before the next turn starts. Apply this on all three exits:
`finish()`, Stop, and interrupt-by-dispatch. `pendingSegments` should be treated as a
render-ahead cache of server state, never as the sole home of a turn's output.

**Common principle across A-D (and §3b, and the stale card in §5b):** any state the operator can
see must be reconciled with the server, and no code path may clear a visible record before the
server copy of it is confirmed present. Fix it as one rule, not four patches.

### 5a-ter. Scroll anchoring and mobile keyboard viewport (statement 11), NOT yet started

Two more transcript/viewport defects. Both are in `Chat.tsx` / the chat CSS, both are
self-contained, and neither depends on the lifecycle fix — but do them AFTER §5a so the
lifecycle work is not competing for the same file.

**E. Autoscroll yanks you to the bottom while you are reading history.**

Grounded in the code. There are three scroll effects and the first one is unconditional:

```
Chat.tsx:937  scrollToBottom()  -> el.scrollTo({ top: el.scrollHeight })   // instant, no behavior
Chat.tsx:941  onLogScroll()     -> atBottomRef.current = (scrollHeight - scrollTop - clientHeight) < 80
Chat.tsx:946  useEffect(() => { scrollToBottom(); atBottomRef.current = true; },
                [messages, pendingSegments, queued, streaming, vsnap.*, scrollToBottom])
Chat.tsx:957  focus/visualViewport effect — correctly guarded by atBottomRef
Chat.tsx:966  input-growth effect      — correctly guarded by atBottomRef
```

The bug is entirely at `Chat.tsx:946-949`. That effect fires on **every** change to `messages`,
`pendingSegments` and the voice snapshot — i.e. on every streamed token — and it does two wrong
things:

1. It calls `scrollToBottom()` with **no `atBottomRef` guard**, unlike the two effects below it.
   So it hard-scrolls even when the operator has deliberately scrolled up.
2. Worse, line 948 then sets `atBottomRef.current = true`, **destroying** the position tracking
   that `onLogScroll` maintains. So even the guards on the other two effects are defeated: any
   token arriving re-asserts "the user is at the bottom" when they are not.

The result is exactly what was reported: scroll up to read an earlier message while a turn is
streaming, and you are dragged back down on every token, continuously, and cannot read anything.

*Fix:*
- Guard the effect: `if (atBottomRef.current) scrollToBottom();` and **delete** the
  `atBottomRef.current = true` line. `onLogScroll` is the only thing that should write that ref.
- Keep one exception: when the operator **sends** a message, always scroll to bottom regardless
  of position (their own send is an intent to follow the conversation). Do that in the send path
  (`submitText` / `dispatchNext`), not in the render-driven effect.
- Add a "jump to latest" affordance so leaving the bottom is not a trap: when
  `!atBottomRef.current` and new content arrives, show a small button that scrolls down and
  clears itself. Build it from `src/ui` primitives (`Button`, lucide `ArrowDown`), tokens only.
- `onLogScroll` must be wired to the scroller's `onScroll` (verify it still is) and the 80px
  bottom threshold is fine; keep it.
- Anchoring nicety: the scroller should hold position when content grows above the viewport.
  `overflow-anchor: auto` (the default) is defeated by the JS scroll; once the unconditional
  scroll is gone, verify browser anchoring does the right thing before adding more JS.

**F. iOS mobile keyboard leaves a huge blank chin and squeezes the app into the top.**

Reported on Firefox iOS, suspected on other iOS browsers. Note the machinery for this
**already exists and is deliberate** — do not rip it out, it is close to right:

- `frontend/src/pages/FocusedChat.tsx:52-78` reads `window.visualViewport` and writes
  `--app-vh` (= `vv.height`) and `--app-top` (= `vv.offsetTop`) onto `<html>`, listening to
  `vv resize`, `vv scroll`, `window resize`, `orientationchange`, and setting
  `document.body.style.overflow = "hidden"`.
- `frontend/src/styles/components.css:1112-1122` consumes them:
  `.ws-app.ws-app--chatfull { position: fixed; top: var(--app-top, 0px); height: var(--app-vh, 100dvh); overflow: hidden; }`
- `App.tsx:88` applies `chatFull` when `pathname.startsWith("/chat/")`, i.e. only the
  `/chat/:contextKey` route (`App.tsx:162` → `FocusedChat`).

So the design is sound and the symptom means the measurement is wrong or stale at the moment
the keyboard animates. Candidate causes, in the order worth testing (these are hypotheses — iOS
Safari/Firefox cannot be tested from this box, they need a real device):

1. **The chin is the layout viewport showing through below a correctly-sized fixed shell.**
   `--app-vh` shrinks to the keyboard-reduced height, but the area below the shell is still the
   page background, and `body { overflow: hidden }` does not stop iOS from scrolling the
   *layout* viewport to reveal the focused input. If `--app-top` is not tracking that scroll
   exactly, the shell floats up and the gap opens below. Try also pinning
   `html, body { position: fixed; inset: 0; overflow: hidden; }` while `chatFull` is active,
   and painting the area with the app background so any residual gap is not blank white.
2. **`resize` fires before/without the keyboard animation settling.** Add a settle pass: re-run
   `set()` on `focusin`, on `transitionend`, and via a couple of `requestAnimationFrame` /
   short `setTimeout` ticks after the last resize event. iOS commonly reports an intermediate
   height mid-animation and never fires again.
3. **Firefox iOS `visualViewport.offsetTop` semantics differ** from Safari. Cross-check against
   `window.scrollY` and clamp: `top = Math.max(0, vv.offsetTop)`; consider
   `window.scrollTo(0, 0)` on focus so `offsetTop` and `scrollY` cannot disagree.
4. **Only `/chat/:contextKey` gets the treatment.** `ChatDock` (`components/ChatDock.tsx:54`)
   and the agent chat (`pages/AgentBuild.tsx:701`, route `/agents/:slug/chat`, which
   `App.tsx:84` classes as `wide`, not `chatFull`) have **no** visual-viewport handling at all.
   If the operator hit this outside `/chat/...`, that is the whole explanation. Confirm which
   URL was open when it happened.

*Fix approach:* extract the visualViewport measurement out of `FocusedChat` into one shared hook
(e.g. `frontend/src/lib/useVisualViewport.ts`) so there is a single implementation, then apply it
to every full-height chat surface rather than only the focused route. That is the "centralize the
design, don't leave junk lying around" instruction applied here. Verify on a real iPhone in both
Firefox and Safari, portrait and landscape, typing and dictating (dictation resizes differently).

### 5b. Second workstream — run/job/chat linkage (statement 8), NOT yet started

The operator's second cluster of complaints is a separate, real defect set. Findings so far:

- Pages are `frontend/src/pages/Jobs.tsx` and `frontend/src/pages/JobDetail.tsx`. There is no
  Run page distinct from these.
- `JobDetail.tsx` is the only page referencing a chat id at all; `Jobs.tsx` has **no back-link
  to the originating chat**. A run that started from chat is a dead end.
- A failed run shows that it errored but surfaces no error text, no log tail, no reason.
- The task-proposal card re-renders as un-clicked after it has been filed and run. The card's
  acted-on state is not persisted — the **same class of bug as the chat lifecycle**: UI state
  that was never reconciled with the server. Fix it with the same principle, not a one-off.

Requirements from statement 8, to design centrally rather than patch per-page: every run/job
view must show what it is, what it did, whether it failed and **why**, and link back to the
chat/ticket it came from; and you must be able to recover or follow up from there.

**Do not start this until the chat lifecycle is fixed and confirmed by the operator.** That
ordering is explicit in statement 8.

---

## 6. Facts and gotchas worth carrying forward

- **Restart + rebuild are mandatory.** Backend changes need `scripts/restart_backend.sh` (the
  running uvicorn has no `--reload`). Frontend changes need
  `scripts/build_workshop_public.sh` (the public site serves static `frontend/dist`).
- **Reproduce as app user 6** (`benmiriello@gmail.com`), not Linux UID 1003. Thread files are
  per-owner.
- **`GET /chat/providers`** is the live source of the model dropdown, and it requires auth.
  `/providers` (no prefix) 404s.
- **Another clawd-a Claude is running** (the Discord bridge, on opus-4-7) and can write to this
  tree. "File modified since read" errors mean a racing writer — re-read before editing. See
  the `duplicate-agent-file-race` memory.
- **Design system is binding** for anything under `frontend/`: `src/ui` primitives, tokens not
  hex, lucide not emoji, `ConfirmDialog` for destructive actions. `scripts/check_design.sh`
  is part of the completion gate.
- **No stubs.** If a piece is too big, mark the task `[~] BLOCKED: <reason>` and propose
  sub-tasks; do not commit `TODO`/`NotImplementedError`/`...` bodies.

---

## 7. One-paragraph summary for a cold start

Opus 5 is added and live. The chat toolbar is reworked for mobile and shipped. The real
outstanding work is the chat lifecycle: the frontend never persisted which thread the operator
was in, so it re-adopted the newest thread on every mount, which made "New chat" and "/clear"
evaporate on refresh and made sent messages unreachable (the backend saved them fine). The fix
is designed and half-applied — the loader in `Chat.tsx:885-935` is already correct, but the
tree currently holds two rival copies of the persistence helper, the typecheck is red, and
nothing yet writes the `NEW_CHAT` sentinel. Delete the inline copy in `Chat.tsx:104-125`, keep
`lib/chatSelection.ts`, wire the sentinel into `newChat`/`/new`/`/clear`/`openThread`, then
typecheck, design-check, rebuild, and commit. Do this in the chat main thread — the operator
has explicitly forbidden routing it through the job queue. Afterwards, and only afterwards,
take on the run/job/chat linkage work in §5b.

---

## 8. Outcome: what was actually built

Shipped in `47c5a11` and `f39fa2f`. Restarted, rebuilt to `dist`, verified live.

### 8a. The diagnosis in §3 was half right, and the missing half was the important one

§3a says "the backend is sound, do not fix it." That was wrong, and it is why the operator
kept saying compaction did not work and that chats started from scratch and errored out. Three
backend defects were doing most of the damage:

1. **Compaction never reached the context that was actually full.** A chat's context lives in
   two places: the stored transcript, and the live claude session. `_compact_thread` only ever
   folded the stored transcript. For a chat with a session, that changes nothing the model
   sees, so `/compact` legitimately did nothing.
2. **Only build-agent chats had a persistent session.** General, app and project chats spawned
   **stateless** every turn and re-derived the situation from a text transcript capped at
   `MAX_HISTORY = 24` messages. That is the literal mechanism behind "why are you starting
   from scratch every time I tell you to continue."
3. **The turn budget was 8 steps and 300 seconds.** Any real piece of work was cut off
   mid-task, and the CLI's `error_max_turns` result was never read, so a guillotined turn
   rendered exactly like a finished one. That is "you just stopped without finishing or
   reporting and WHY?"

Also: `_summarize_sync` called `call_cloud` without `tools_disabled`, so writing a one-paragraph
summary ran in full agentic mode and spun up every MCP server. `/compact` took minutes and read
as a dead button.

### 8b. The single rule

Any state the operator can see is reconciled with the server, and no code path clears a visible
record before the server's copy of it is confirmed. Applied to thread selection, the type-ahead
queue, live-turn segments, turn liveness, the compaction marker, and in-chat card state.

The second rule, new here: **a chat's context has ONE owner.** Every chat now gets one
persistent claude session per thread. Continuity is a property of the session, not something
re-derived from a truncated transcript each turn.

### 8c. Item by item

| Item | Status |
|---|---|
| §5.1 unbreak the tree | done (unclosed `Fragment`, plus the missing import) |
| §5.2 wire the NEW_CHAT sentinel | done (`newChat`, `/new`, `/clear`, `openThread`) |
| §5.3 persist provider per contextKey | done |
| §5.4 new thread into the picker | done, plus recorded as the selection |
| §5.5 re-hydrate on mount/return | done, and extended: focus/visibilitychange re-sync |
| §5.6 walk the flows | done: backend-level tests + live browser at 1280 and 390 |
| §5.7 gates | typecheck, `check_design.sh`, backend import: all clean |
| §5.8 ship | restarted, rebuilt, bundle hash confirmed served, committed |
| §5a-bis A truthful working indicator | done: server-derived liveness poll; reconnect exhaustion no longer fakes idle |
| §5a-bis B durable queue | done via persisted storage (the doc's "minimum", not the server-side option) |
| §5a-bis C drain the whole queue | done |
| §5a-bis D never wipe the previous turn | done: Stop reconciles before handing off |
| §5a-ter E scroll anchoring | done: guarded, `atBottomRef` has one writer, jump-to-latest added |
| §5a-ter F iOS chin | done for fixed chat shells; see §8d |
| §5b run/job/chat linkage | mostly already existed; the live defects fixed, see §8d |

### 8d. Two things deliberately not done, and why

1. **The agent workbench (`/agents/:slug/chat`) does not get the visual-viewport treatment.**
   The shared hook pins `html`/`body` to defeat iOS layout-viewport scrolling. That is correct
   for a fixed full-height chat shell and would break the workbench, which is a scrolling
   multi-pane page. It stays a normal scrolling surface. The focused chat and the dock both
   have the treatment. **None of the iOS work is device-verified** — it cannot be tested from
   this box and needs a real iPhone, Firefox and Safari, portrait and landscape, typing and
   dictating.
2. **Most of §5b was already built.** `JobsFeed` already carried the chat back-link, the error
   text and the task text; `JobDetail` already linked chat, agent and ticket. Those findings
   were stale. What was actually broken and is now fixed: a failed run with no error string
   rendered no Error section at all, and a failed run offered nowhere to go next. There is no
   re-run endpoint and one was not invented; the follow-up path is back to the originating chat
   or the linked ticket.

### 8e. Evidence

- Compaction, on the operator's real 80-message thread: 47 messages folded, context meter
  36,020 to 4,660 tokens. The fold divider renders after reload ("76 earlier messages folded
  into the running summary").
- Session continuity, across separate processes: turn 1 was given a codeword and returned a 529;
  turn 2 resumed the same session id and answered `PELICAN-7`.
- Card state: POST then GET round-trips on the thread.
- Live browser: no console errors at 1280 or 390; at 390 every toolbar control is inside the
  viewport (max x 378 of 390), which was the original clipping complaint.

---

## 9. Second pass, 2026-07-30: what §8 missed

The 07-25 work was real and it shipped. It did not finish the job. Five more defects were
found by auditing the chat surface state-by-state against the rule §8b states. Shipped in
`52d4349` and `b837d53`, built from a clean worktree and confirmed served.

### 9a. Where the reports actually live

Answering "did we document this?" — yes, better than it felt from the operator's seat:

| Source | What it holds |
|---|---|
| §1 of this file | all eleven operator statements, verbatim |
| `.runtime/users/6/chat/b3a9f09f…json` | the 07-25 thread — 40 operator messages, the full arc |
| `.runtime/users/6/chat/d18e3b6d…json` | the 07-18 thread — the run/job/card dead-end reports |
| `.runtime/users/6/chat/47b637e7…json` | the 07-25→30 thread — voice, and the compaction evidence |

The gap was not documentation. It was that the 07-25 pass declared the lifecycle fixed and
stopped auditing, so the remaining defects were never looked for.

### 9b. The five defects

1. **Switching chats destroyed the queue of the chat you left.** `setQueue` derived its
   storage key from `activeIdRef`, which is assigned *during render*, so through a switch it
   still held the OUTGOING thread. `hydrate()` read the incoming thread's queue and wrote it
   back under the outgoing thread's key — normally empty, so the messages waiting there were
   removed. §8c marks the durable queue "done"; it was durable and pointed at the wrong thread.
2. **Messages typed while a blank chat's first turn was starting were stranded** under the
   blank-chat key and never migrated when the thread was created.
3. **`/compact` typed during a turn was queued as ordinary text**, then drained into the next
   turn joined to whatever else was waiting. Backend half: a drained queue arrives as one
   blank-line-joined blob, and anything starting with `/` went RAW to the CLI, which read the
   whole blob as a command name and failed the turn.
4. **`/compact` read as a no-op even when it worked.** A native in-session compact empties the
   live window but the CLI does not always report usage on that turn, and the stored
   measurement was dropped only when the STORED transcript folded — which needs more than
   `_COMPACT_KEEP_TAIL` messages. Below that `_compact_thread` returns early. Live evidence:
   thread `47b637e7` sat at **142,183 / 200,000 tokens on four messages**. That number was on
   screen while the operator was writing "is the chat memory back down to starting point?"
5. **In-chat card state diverged silently.** `recordCardState` patched local state and
   swallowed a failed write with a note claiming it would "re-sync on the next action". It
   never did. The card read as acted-on, the server had no record, and the next hydrate
   brought back an un-acted card offering to file and run work that had already run. This is
   statement 8's "it's still sitting here as though I had not clicked it before" — §8c did not
   list it, and §5b assumed it was covered.

Also: the model/assistant list fetch was caught and discarded as "non-essential", falling back
to hardcoded `MODEL_LABELS` — the same divergence that made Opus 5 invisible in §2a. And a
voice-adopted thread never recorded itself as the selection, so it came back blank on remount.

### 9c. The thing that would have blocked any fix from reaching the operator

`frontend/package.json` runs `tsc -b && vite build`. **Uncommitted voice WIP in the tree
(`frontend/src/lib/voiceSession.ts`, an unused `lastSttMs` field, TS6133) has been failing that
typecheck since 07-27, so `npm run build` — and therefore every ship — has been broken.** The
last successful build was 07-25 14:47. Anything committed to the frontend after that had not
reached `dist`, which is what the public site serves.

This pass shipped by building from a detached worktree at HEAD, so the stale voice WIP was not
dragged into the bundle. **That WIP is still uncommitted and still breaks the build.** Resolve
it before the next frontend change, or repeat the worktree trick.

### 9d. Audited and found honest

The server-derived liveness poll behind the working indicator, the queued-message bubbles, the
fold divider, `compacted_through`, participants, and the thread picker's active selection all
reconcile with the server correctly. No change needed.

### 9e. Evidence

- `_is_raw_command` over 8 cases including `"/compact\n\nalso please fix the picker"` → not a
  command; `clear_thread_context_tokens` set/clear/idempotent-re-clear round trip.
- Backend import clean, restarted via systemd with `WORKSHOP_CHAT_CWD` confirmed preserved
  (losing it would strand every chat's claude session), `/api/chat/providers` → 401 not 500.
- `tsc` and `check_design.sh` clean; bundle `index-pk4sbrTY.js` confirmed served by the edge
  with all three fixes present in it.
- Thread `47b637e7`'s stale 142,183-token reading was cleared in place, so the meter is honest
  now and not only for future compactions.

---

## 10. Mid-turn message delivery (2026-07-30, commit `3c0871a`)

Closes the half of statement 10 that §8c marked done but only half-satisfied. The spec was
"it should not interrupt the current work... but you should pick up all of the new messages
if I send new messages after your current turn **or step** is over." §5a-bis C delivered the
*whole-queue drain*, which fixed batching but left pickup latency at the full remaining
duration of the turn. The operator restated it: a message should enter "the next time there
is a point between actions of the agent."

### 10a. Mechanism

Turns run with `--input-format stream-json`, so the CLI's stdin stays open for the life of
the run and it reads pending input at each boundary between steps. The opening prompt goes in
the same way instead of as an argv positional.

The channel is a **FIFO** in the turn's journal dir, not a pipe, because turn processes are
deliberately detached to survive a backend restart — a pipe dies with the parent. It is opened
**O_RDWR**: an fd that is its own writer never reports EOF, so the CLI keeps reading after an
injector closes its handle. Opened O_RDONLY the run would end the first time a message landed.

`POST /api/chat/turns/{turn_id}/inject`. `delivered: false` is not an error — it means the
turn ended between the operator hitting enter and the request landing, and the client queues
the message as the next turn. The message is never dropped.

### 10b. Two things that would have broken every turn

- **The CLI does not exit when a turn finishes** under stream-json input; it idles waiting for
  more stdin. The `result` line is the end of the turn, so the process is retired there:
  SIGTERM to the whole group first (it owns MCP servers and tool subprocesses, and this now
  runs on *every* turn rather than only on a timeout), SIGKILL as fallback.
- **Its exit status then reports our signal, not the turn's outcome.** Read naively that
  renders every successful turn as "ended unexpectedly (exit -15)" — the exact string the
  operator was drowning in. A turn that produced a result line is `rc=0` by definition now.

An injected message is stored above the live assistant placeholder, so the thread does not
read out of order and a later fresh session re-assembling history still sees it.

### 10c. Evidence

- Agent mid-way through five `sleep 4` calls took an injected instruction at the next step
  boundary and acted on it: 2 tool calls instead of 5, final text `Sleep 1 finished.PELICAN`,
  no error, nothing about ending unexpectedly.
- A first probe run showed the same delivery with the model *declining* to switch (the prompt
  had said "do not skip any") and saying so mid-turn — which is itself proof of receipt, and
  a useful reminder that delivery is the engine's job and compliance is the model's.
- `--resume` still carries session context across the new stdin path (answered `PELICAN` on a
  later turn in a fresh process).
- All three spawn paths pass: full-agentic, `tools_disabled`, narrow `allowed_tools`.
- No claude processes leaked. Injecting into an ended turn returns false in 0.000s rather than
  hanging on a FIFO open.

### 10d. Note for whoever is next

`a49d5f3` committed the voice WIP that had been failing `tsc -b` since 07-27, so the frontend
build is unblocked and §9c's warning is now historical. Two other sessions committed to this
tree during this work (`263ed66`, `a49d5f3`, `f19a9fd` — the last a chat header restructure
touching the same file). Nothing was clobbered, but re-read before editing and stage only your
own files; see the `duplicate-agent-file-race` memory.
