# Client state management: audit and recommendation (2026-08-25)

Status: PROPOSAL. Nothing here is built. The bug that prompted it IS fixed and
live (commit `2277cc60`); this doc is about why it happened and what to do so
the next one does not.

Supersedes nothing. No prior decision record covers frontend state; searched
notes, docs, tickets and chats before writing.

---

## 1. What prompted this

The operator screen-recorded sending the first message in a new chat and
counted five states in under a second:

1. the message appears (fades in), alone
2. the status line says **"Still running"** for a frame, then **"Working"**
3. everything vanishes, replaced by **"Ready to build."** (the empty state)
4. **"Working"** comes back alone, with no user message
5. the user message comes back

All five were reproduced in a real browser on the built origin with a
MutationObserver (an rAF probe samples once per frame and averages these away).
The collapse in step 3 landed in the same millisecond as a redundant history
fetch resolving, and appeared twice in some runs.

Three defects, now fixed:

- **`branchRef` was not re-seeded on a cache-miss thread change**, so it held
  the *outgoing* thread's branch id while the incoming thread loaded. Every
  thread has its own branch id, so the new thread's first response looked like a
  branch *change*. On a new chat that fires on every send, because sending is
  what creates the thread. The cache-*hit* path already did this and documented
  why; the miss path did not.
- **The branch-change path dispatched `reset` and then re-fetched**, leaving the
  store empty for a whole round trip. The reducer's `reset` drops the pending
  send along with everything else (it is preserved only for a blank chat
  becoming a thread), so the just-typed message went with it. The re-read
  already resets atomically; the early reset was redundant and was the only one
  that rendered.
- **`turnPhase` derived "detached" from `!attached`**, conflating "we gave up
  watching" with "we have not attached yet" — the 60-140ms window between
  `POST /turns` answering and `GET /turns/{id}/stream` opening. So the indicator
  showed the wording for a socket that *ended* at the top of a turn just
  started.

## 2. What the state architecture actually is

Measured, not assumed.

**There is no state management library.** Not Redux, not Zustand, not Jotai, not
MobX, not XState, not TanStack Query, not SWR. Zero, in the root workspace, in
`frontend/`, in `packages/ui/` and in `mobile/`. Everything is hand-rolled React:
`useState`, `useReducer`, `useRef`, `useEffect` and Context.

The chat module is 22,871 lines. Its state surface:

- `Chat.tsx` — 1,532 lines, 17 `useState`, 11 `useRef`, 13 `useEffect`, 8 `useMemo`
- `useThreadLog.ts` — 565 lines, 6 `useState`, 10 `useRef`, 5 `useEffect`
- `useTurnStream.ts` — 457 lines, 4 `useState`, 8 `useRef`, 2 `useEffect`
- `useChatThread.ts` — 432 lines, 10 `useState`, 3 `useRef`, 3 `useEffect`
- `threadStore.ts` — 777 lines, a **pure reducer**, no React at all

Roughly **37 `useState` and 32 `useRef` across four modules feeding one render**,
with 25 async writers (awaits, `.then`, `EventSource`, `usePoll`) landing into
them on independent schedules.

**The good part is real and should be kept.** `threadStore.ts` is a proper pure
reducer with a documented event vocabulary and its own tests. It is the one
piece of this that is well-structured, and none of today's bugs were in it.

## 3. The actual diagnosis

**The reducer owns only part of the truth.** Thread identity, branch tracking,
socket attachment, hydration, pending sends and loading flags live in
`useState`/`useRef` *beside* the store, each written by its own async callback,
with no ordering guarantee between them. The render is a pure function of a
tuple whose members update independently.

Every defect found today was one moment where that tuple was internally
inconsistent:

- a ref (`branchRef`) disagreed with a prop (`threadId`) → phantom branch change
- a reducer dispatch was not paired with its fill → an empty tuple rendered
- a socket boolean was read as a lifecycle fact → wrong wording

This is why five previous passes "fixed" the jumping and it came back. Each pass
tuned one member of the tuple. The tuple has no invariant.

**The second finding: most of this is server state being hand-synchronized.**
The thread list, the events page, the active turn, the branch, the context
reading and the work panel are all owned by the server, fetched, cached and
invalidated by hand. The codebase has therefore hand-written, in pieces:

- a cache — `threadCache.ts`, 92 lines, with its own stale-while-revalidate
- a retry policy — `withTransientRetry` in `packages/ui/src/httpError.ts`
- a poller — `usePoll` in `packages/ui/src/usePoll.ts`, used in **33 files**
- request-race cancellation — `threadIdRef` compared after every await
- request deduplication — **absent**, which is measurable: a new-chat send
  issues **three to four identical `events?after=0&tail=30` reads of the same
  thread within ~150ms**, because every caller that races the switch's reset
  commit computes `after = 0` and re-reads the whole tail. The answers are
  identical; the extra reads and their store commits are pure waste. This is
  still true after today's fix. It no longer *renders*, which is why it is a
  finding here and not a bug fix.

That is roughly a third of a server-cache library, written by hand, spread over
33+ files, and the parts that were missing are exactly where the bugs were.

**A global store would not have prevented today's bugs.** Redux in place of
these hooks still leaves someone hand-writing the reset-then-fill sequence and
the branch-change detection. What prevents them is (a) a layer that owns
fetching, deduplication, caching and invalidation, and (b) one lifecycle machine
instead of six booleans.

## 4. Constraint that removes a whole axis

**Mobile is a WebView over the web app** (`mobile/app/index.tsx`: "The whole
product surface is the Workshop web app, hosted in this WebView"). Native
capabilities reach the page through an injected bridge. So there is no second
UI to keep in sync and no React Native store requirement: **whatever is chosen
for web *is* mobile.** Any recommendation that weighs "does it work in React
Native" is answering a question this architecture does not ask.

## 5. Options considered

**Redux Toolkit.** The standard answer, excellent devtools, time-travel
debugging that would have made today's five-state sequence visible immediately.
Rejected as the primary move: it is a *client* store, and the problem is
*server* synchronization. Adopting it would relocate the hand-written fetching
rather than delete it. RTK Query would address that, but it arrives bundled with
a store migration nobody needs yet.

**Zustand.** Minimal, unopinionated, ~1KB, no provider ceremony. Good fit for
genuine client state. Does not address server caching at all. Keep as a later
stage, not the first one.

**XState.** Real statecharts. Correct shape for the turn lifecycle, which is
genuinely a machine (idle → starting → attached → reconnecting → detached →
stopping → done). Rejected as a dependency: the same guarantee is available from
a discriminated union in ~40 lines, and this repo's own history is that an
adopted abstraction it does not fully own becomes a parallel system.

**TanStack Query.** Owns exactly the failure modes measured above: request
deduplication (the 3-4x tail read), stale-while-revalidate (already hand-rolled
in `threadCache`), race cancellation (already hand-rolled via `threadIdRef`),
retry with backoff (already hand-rolled in `withTransientRetry`), polling
(already hand-rolled in `usePoll`, 33 files), and structural sharing so an
identical response does not re-render. It replaces code this repo already wrote
and already got wrong.

## 6. Recommendation

**Not a big-bang migration, and not a global store first.** Three stages, each
independently valuable and independently abandonable.

### Stage 1 — one turn lifecycle, no library

Replace the boolean tuple with a single discriminated union. Today there are
**seven independent answers** to "is a turn running?": `store.activeTurn`,
`turnActive`, `pipeline.starting`, `stream.attached`, `stream.reconnecting`,
`stream.detached` (added today) and the composer's `is-streaming` class. They
are derived from four different sources and can disagree, and today's third
defect was exactly that disagreement.

One type, one reducer, one place that decides the label and the indicator.
Illegal states (attached *and* detached, starting *and* done) stop being
representable rather than being avoided by convention. Cheapest stage, highest
share of the pain, no dependency, no migration.

### Stage 2 — adopt TanStack Query for server reads, chat first

Chat is the right first surface: it is where the pain is, where the proof is,
and it exercises every hard case (streaming beside polling, cursor pagination,
optimistic writes, cache-seeded switches). Move the thread list, the events
page, context and work onto it. Delete `threadCache`, the manual race guards and
the redundant tail reads. `threadStore` **stays** — the reducer remains the
place streaming events fold into a transcript; Query owns the fetching around
it, not the merging inside it.

Success is measurable before committing further: the identical-tail-read count
per new-chat send should go from 3-4 to 1, and `useThreadLog` should lose most
of its 10 refs.

### Stage 3 — the rest of the polling surface, then client state

`usePoll` has 33 call sites: runs, tickets, deploys, agents, notifications. They
have the same duplication and the same missing invalidation, with lower stakes
and simpler shapes. Move them once chat has proven the pattern. Only then, if
genuine cross-surface client state exists (drawers, notification badges,
composer drafts shared between routes), add Zustand for that and nothing else.

**Answering the staging question directly:** yes, chat first, then ongoing
work/runs/notifications, in that order. Not because chat is easiest — it is the
hardest — but because it is the only surface that will falsify the choice. If
TanStack Query cannot hold streaming beside polling, that must be discovered on
the surface that does it, before 33 files depend on it.

## 7. Where state and cache should live

- **Source of truth: the server.** Postgres, reached over the existing REST API.
  Unchanged, and not in question.
- **Server cache: in-memory on the client**, owned by the query layer, keyed by
  resource. Not `localStorage`: it goes stale invisibly and there is no
  invalidation story.
- **Offline / resume: deliberately deferred.** Query supports persisting its
  cache to IndexedDB, which would make a phone resume a thread instantly and
  survive a WebView eviction. Worth doing, worth doing *after* stage 2, and
  worth a decision of its own, because a persisted cache is a second copy of the
  truth with its own staleness rules.
- **Client-only UI state**: whatever is genuinely local stays local. What must
  outlive a route already has a home in the kit's namespaced `storage.ts`; it
  should not gain a second one.
- **"Memory" in the product sense** (notes, knowledge, context) is server state
  like everything else and is not affected by this document.

## 8. What this does not claim

- It does not claim the current code is bad work. `threadStore` is good, the
  comments throughout the chat module are unusually load-bearing, and every
  hand-rolled piece exists because something real needed it.
- It does not claim a library would have caught all three defects. The
  `branchRef` bug is a plain omission and could survive any architecture. It
  claims that deduplication and atomic cache updates would have made two of the
  three unrepresentable, and that a lifecycle union makes the third
  unrepresentable.
- It does not estimate effort. Stage 1 is small. Stage 2 is not, and should be
  scoped against the real file list before anyone commits to it.

## 9. Open question for the operator

Stage 1 is worth doing regardless of what is decided about stages 2 and 3, and
it is small enough to do without a migration plan. Stages 2 and 3 add a
dependency to a codebase that has deliberately owned its primitives, and that is
a judgement call about direction rather than a technical finding. This document
recommends taking it, and does not act on it.
