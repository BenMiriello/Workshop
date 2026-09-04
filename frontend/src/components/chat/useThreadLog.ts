import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { usePoll, withTransientRetry } from "@workshop/ui";
import { chatApi } from "../../lib/chat";
import {
  initialThreadState, threadReducer, snapshotThread,
  type ThreadAction, type ThreadState,
} from "./threadStore";
import { threadCache } from "./threadCache";
import { selectCommitted, composeTranscript, isLiveShown, type TranscriptBlock } from "./transcriptBlocks";
import type { ChatProblem } from "./chatProblem";
import { isStale } from "./staleNotice";

// The client's copy of one thread's log, kept current from ONE endpoint.
//
// `GET /chat/threads/{id}/events?after=<cursor>` returns everything needed to
// render a transcript: the events since the cursor, the new cursor, and the run
// in flight. This hook calls it on mount, on focus, on a timer while the tab is
// visible, and after any write. It merges the result with nothing except the
// live SSE for the active run (useTurnStream, which dispatches into the same
// store).
//
// `after` is a cursor, not an index, so a re-fetch is cheap and idempotent: the
// reducer dedupes by event id, and a duplicate page costs nothing. That is the
// whole reason this can be called freely rather than guarded.
//
// THE POLL IS NOT A FALLBACK, it is how the chat learns about anything that did
// not happen in this tab: a turn started on another device, a message injected
// from a second tab, a turn that finished while this one slept. Liveness is
// server state; a local flag can only ever describe this tab.

// THE POLL IS A BACKSTOP NOW, not the mechanism. `useThreadStream` below opens
// a change stream for the thread and syncs the instant anything moves, so this
// exists for the cases a stream cannot cover: a socket that dropped without
// saying so, a proxy that closed it, a tab coming back from hidden with the
// stream torn down. Sixty seconds is the right cadence for that; ten was the
// cadence of a mechanism, and it meant everything a client did not do itself
// arrived up to ten seconds late on every other device.
const POLL_MS = 60_000;

// THE NEWEST SLICE, FIRST. Opening a thread used to fetch `events?after=0` —
// the whole log, unbounded, measured at 4.9MB on the operator's longest
// thread — and painted nothing until all of it had arrived and been parsed.
// `tail=INITIAL_TAIL` asks for only the newest events of the current branch
// path (backend/routes/chat.py `_tail_page`), which the operator can start
// reading immediately; the rest pages in on its own in the background (see
// `loadOlder` below, driven by Chat.tsx's background-load effect). 30 is a
// round number a bit above one ordinary
// exchange (a message, a multi-step reply, maybe a follow-up), chosen so the
// common case needs no second fetch at all; it is not load-bearing on
// correctness; any positive size behaves the same way. A branch change also
// re-reads through this path (see the `branchChanged` fetch in `sync`) rather
// than a bespoke "first load" flag, since it is exactly the same situation —
// this tab has nothing of the newly-selected line yet.
const INITIAL_TAIL = 30;

// STALE-WHILE-REVALIDATE OVER THE SAME CURSOR SYNC. `threadCache` (a plain
// module, not React state) holds the last few threads this tab has actually
// finished loading. On a thread switch that HITS the cache, this hook paints
// the cached transcript in the very render the thread changes (`seedThread`,
// below) instead of the tail-first cold load — and then runs exactly one
// ordinary cursor sync (`sync()`, via `pendingCatchUpRef`) to reconcile it
// against the server, the same call an ordinary poll makes. A cache MISS is
// untouched: today's tail-first load, byte for byte.
//
// The reason the catch-up is a scheduled ref rather than a plain `void sync()`
// called right here: `sync` reads `stateRef.current`, and `stateRef` is only
// updated at render time (`stateRef.current = state;`, below), not inside the
// effect that just dispatched the seed. Calling it synchronously here would
// read the PREVIOUS thread's cursor and threadId, `sync` would conclude this
// is a fresh read, and it would fetch the tail again instead of the one cheap
// cursor page this whole design exists to make possible. Deferring it to an
// effect keyed on `state.threadId` means it only runs once the seed has
// actually committed and `stateRef` agrees with it.
//
// See threadCache.ts for what is deliberately excluded from the snapshot
// (pending sends, the working indicator's authority) and why.

// When the chat says it has gone quiet lives in `staleNotice`: a run of
// failures AND real elapsed silence. Counting failures alone was not enough —
// sync runs on stream events and on writes too, not only on the poll, so three
// of them can fail inside three seconds and did.

export type ThreadLog = {
  state: ThreadState;
  /** The current state, readable from a callback without re-creating it. */
  stateRef: React.MutableRefObject<ThreadState>;
  dispatch: React.Dispatch<ThreadAction>;
  transcript: TranscriptBlock[];
  /** Pull everything since the cursor. Safe to call at any time, any number of
   *  times; call it after every write. */
  sync: () => Promise<void>;
  /** Is a run in flight on this thread, per the server. */
  turnActive: boolean;
  /** Is the live run's block actually rendered — i.e. does `turnActive` have
   *  anything on screen to go with it? Not the same question, because the
   *  server does not clear `active_turn_id` and append the reply atomically
   *  (see transcriptBlocks' `isLiveShown`). Threaded out so the display-only
   *  derivations read the transcript's answer instead of computing a second
   *  one that can disagree with it. */
  liveShown: boolean;
  /** Has THIS thread's first page landed? False from the render in which the
   *  thread changes until `events?after=0` resolves (or fails).
   *
   *  The transcript has no other way to tell "nothing in this conversation"
   *  from "the log has not arrived yet", and the caller's thread-list flag
   *  cannot answer it: that one goes false as soon as the thread is selected,
   *  which is BEFORE this fetch is even issued. Without this, every chat open
   *  flashed the empty state for a round trip. */
  hydrated: boolean;
  /** Has this browser stopped hearing from the backend? The decision is made
   *  here rather than at render time on purpose: it turns on a clock, and a
   *  chat whose backend has gone away has nothing else left to re-render it. */
  stale: boolean;
  /** Which line of the conversation the server is serving, and how many exist
   *  (ADR 0020). `id` is null when this chat cannot be branched, which is the
   *  signal the transcript uses to leave the control off entirely. */
  branch: { id: string | null; name: string; count: number; head: string | null };
  /** Fetch the next page further into this thread's past and PREPEND it.
   *  No-op while a page is already in flight, or before anything has loaded
   *  at all. Driven automatically by Chat.tsx's background-load effect until
   *  `hasMoreOlder` (threadStore) is false; there is no manual control. */
  loadOlder: () => void;
  /** A `loadOlder` page is in flight. */
  loadingOlder: boolean;
  /** The last `loadOlder` page failed, in a sentence. The transcript below it
   *  is still on screen and still correct — a `Notice`, not a `StateBlock`;
   *  see frontend/CLAUDE.md, "A region or a line". */
  olderError: string | null;
};

export function useThreadLog({
  threadId,
  onError,
}: {
  threadId: string | null;
  onError: (problem: ChatProblem | null) => void;
}): ThreadLog {
  const [state, dispatch] = useReducer(threadReducer, null, () => initialThreadState(null));
  const stateRef = useRef(state);
  stateRef.current = state;
  // Which thread the store currently holds. Read inside async callbacks so a
  // response that lands after a thread switch is dropped rather than merged
  // into the conversation that replaced it.
  const threadIdRef = useRef<string | null>(threadId);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  // Which thread's first page has landed. DERIVED, not a flag flipped in an
  // effect: comparing it to the current thread makes `hydrated` false from the
  // very render in which the thread changes, with no frame in between where a
  // new thread looks settled and empty. An effect-set flag would leave exactly
  // that frame, which is the bug in miniature.
  const [hydratedId, setHydratedId] = useState<string | null>(null);
  const hydrated = !threadId || hydratedId === threadId;

  // Consecutive failures, and when that run of failures BEGAN. Both are
  // needed: a burst of failures says nothing on its own (the first thing a
  // dropped connection does is fail several times quickly), and elapsed time
  // says nothing on its own either.
  //
  // THE SECOND ONE IS THE OUTAGE'S OWN CLOCK, not the last success. It used to
  // be `lastOkRef`, the moment a request last landed, and that is not when
  // anything went wrong: this thread only asks every POLL_MS while it is
  // quiet, so it sits up to a minute past its last success with a perfectly
  // healthy backend. Two failures a second apart then cleared a 30s floor
  // nothing had been silent for, and "This chat stopped updating." appeared
  // about two seconds into a restart and vanished again when the next request
  // landed. Reported 2026-09-04. Timed from the first failure of the current
  // run, the floor measures what the sentence claims. See staleNotice.
  const failuresRef = useRef(0);
  const outageSinceRef = useRef(0);
  const [stale, setStale] = useState(false);

  // Which branch the last response was for. `undefined` means "no response
  // yet", which is deliberately distinct from `null` ("this chat has no
  // branches"): the first sync must not be mistaken for a branch CHANGE and
  // trigger a re-read of a page that just arrived.
  const branchRef = useRef<{ id: string | null | undefined; count: number;
                             head: string | null; pending: boolean }>(
    { id: undefined, count: 0, head: null, pending: false });
  const [branch, setBranch] = useState<
    { id: string | null; name: string; count: number; head: string | null }>(
    { id: null, name: "", count: 0, head: null });

  // Set by the mount effect on a CACHE HIT to the thread just seeded, and
  // consumed by the effect below once that seed has actually committed (see
  // the STALE-WHILE-REVALIDATE comment above `POLL_MS`). `null` the rest of
  // the time, including for every cache miss, which leaves this a no-op and
  // today's cold-load behaviour untouched.
  const pendingCatchUpRef = useRef<string | null>(null);

  // LOADING OLDER: a page further into this thread's past, on demand. Kept
  // out of `stateRef`/the reducer's own bookkeeping on purpose — whether a
  // fetch is IN FLIGHT is this hook's business, not the transcript's, and a
  // `Notice` for a failed page needs somewhere to live that a later successful
  // sync (which touches unrelated fields) does not clear out from under it.
  const loadingOlderRef = useRef(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);

  const loadOlder = useCallback(async () => {
    const id = threadIdRef.current;
    if (!id || loadingOlderRef.current) return;
    // THE BRANCH IS PART OF WHAT THIS PAGE IS OF, not just the thread. A branch
    // change re-reads from zero (`reset` + `eventsReceived`, in `sync` below)
    // under the SAME thread id, so the `threadIdRef` check after the await
    // cannot see it: an older page requested on the abandoned line would come
    // back, pass that check, and prepend the other branch's messages above the
    // ones now on screen. Capturing the branch here and re-checking it there is
    // the same identity guard, applied to the axis that actually moved.
    const branchAt = branchRef.current.id;
    // The oldest seq this tab currently holds IS the floor of the last page it
    // read (the initial tail, or an earlier "older" page), so it is exactly
    // the `before` the contract asks for — see `hasMoreOlder` in threadStore.
    // Nothing to page before if nothing has loaded yet.
    const before = stateRef.current.order[0];
    if (before === undefined) return;
    loadingOlderRef.current = true;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await chatApi.threadEvents(id, 0, { tail: INITIAL_TAIL, before });
      if (threadIdRef.current !== id || branchRef.current.id !== branchAt) return;
      dispatch({
        type: "olderEventsReceived",
        events: page.events || [],
        hasMore: page.has_more ?? false,
      });
    } catch (e) {
      if (threadIdRef.current === id && branchRef.current.id === branchAt) {
        setOlderError(`Couldn't load earlier messages: ${e}`);
      }
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlder(false);
    }
  }, []);

  const sync = useCallback(async () => {
    const id = threadIdRef.current;
    if (!id) return;
    // A BRANCH CHANGE IS A RE-READ FROM ZERO, not a merge. This is a cursor
    // sync: the store keeps what it already holds and adds what it is given, so
    // after going back to an earlier point a cursor fetch would return nothing
    // new and the abandoned tail would stay on screen forever. The branch id
    // rides on the same response for exactly this reason (ADR 0020).
    const branchChanged = branchRef.current.pending;
    branchRef.current.pending = false;
    const after = (!branchChanged && stateRef.current.threadId === id)
      ? stateRef.current.cursor : 0;
    // Stamped BEFORE the request, so the store can tell an answer about an older
    // state of the world from a current one. Several of these are legitimately
    // in flight at once and they do not resolve in order.
    const requestedAt = Date.now();
    // `after === 0` is a re-read from scratch (a branch change, or a sync
    // raced against a thread switch that has not committed its reset yet —
    // see `after` above), which is exactly the situation the tail read is for:
    // this tab has nothing of the line it is now asking about. A real cursor
    // (`after > 0`) is the ordinary incremental sync and must stay exactly
    // that; `tail`/`before` are ignored server-side once `after` is real, but
    // there is no reason to send them and every reason not to imply this is a
    // paging request when it is not.
    const isFreshRead = after === 0;
    try {
      const page = await chatApi.threadEvents(
        id, after, isFreshRead ? { tail: INITIAL_TAIL } : undefined);
      if (threadIdRef.current !== id) return;
      const nextBranch = page.branch?.id ?? null;
      if (branchRef.current.id !== nextBranch) {
        const first = branchRef.current.id === undefined;
        branchRef.current.id = nextBranch;
        // ALL THREE FIELDS MOVE TOGETHER. Only `id` used to be written here,
        // which left `count` and `head` at the previous branch's values (0 and
        // null on a first response). The very next sync then took the `else if`
        // below, found a "change" that was only this omission, and called
        // `setBranch` with values identical to the ones already held -- a fresh
        // object, so a full re-render of the chat and a rewrite of the thread
        // cache, on the second sync of every thread.
        branchRef.current.count = page.branch_count ?? 0;
        branchRef.current.head = page.branch?.head ?? null;
        setBranch({ id: nextBranch, name: page.branch?.name ?? "",
                    count: page.branch_count ?? 0,
                    head: page.branch?.head ?? null });
        if (!first) {
          // An older page in flight belongs to the line being left. `loadOlder`
          // will drop its response on the branch guard, but the flag it set has
          // to come off here or the first page of the NEW line is refused as
          // "already loading" until that response lands.
          loadingOlderRef.current = false;
          setLoadingOlder(false);
          setOlderError(null);
          // Learned about the change on this very response, which was fetched
          // from the OLD cursor. Ask again from zero; the re-read replaces what
          // we hold.
          //
          // THE RESET IS NOT DISPATCHED HERE, and it used to be. Emptying the
          // store now and refilling it when the re-read lands leaves the
          // transcript with NOTHING in it for a whole round trip, and the
          // reducer's `reset` drops the pending send with everything else
          // (threadStore: it is only preserved for a blank chat becoming a
          // thread). On screen that is the operator's just-sent message, and
          // the working indicator, vanishing and being replaced by the empty
          // state — measured at 60-200ms on the new-chat path, twice in some
          // runs. The re-read already resets ATOMICALLY: it sets
          // `branchChanged`, and the `if (branchChanged) dispatch reset` below
          // batches with its own `eventsReceived` in one commit (React 18), so
          // the old line is replaced by the new one with no empty frame
          // between them. Dropping stale content is the re-read's job, not a
          // separate step that renders.
          branchRef.current.pending = true;
          void sync();
          return;
        }
      } else if (branchRef.current.count !== (page.branch_count ?? 0)
                 || branchRef.current.head !== (page.branch?.head ?? null)) {
        // The head moves on every committed message, so this keeps the
        // "already the end of the chat" state honest as the chat grows.
        branchRef.current.count = page.branch_count ?? 0;
        branchRef.current.head = page.branch?.head ?? null;
        setBranch({ id: nextBranch, name: page.branch?.name ?? "",
                    count: page.branch_count ?? 0,
                    head: page.branch?.head ?? null });
      }
      if (branchChanged) dispatch({ type: "reset", threadId: id });
      dispatch({
        type: "eventsReceived",
        events: page.events || [],
        lastSeq: page.last_seq || 0,
        activeTurn: page.active_turn ?? null,
        queued: page.queued,
        queueHeld: page.queue_held,
        queueError: page.queue_error,
        pendingOperation: page.pending_operation,
        clearedThrough: page.cleared_through,
        cutMode: page.cut_mode,
        requestedAt,
        // Only a fresh (tail) read answers "is there more behind this",
        // because only a fresh read asked. See `hasMoreOlder` in threadStore.
        hasMore: isFreshRead ? (page.has_more ?? false) : undefined,
      });
      // A poll landed, so whatever this chat is showing is current, and any
      // run of failures is over: the outage clock has nothing left to time.
      failuresRef.current = 0;
      outageSinceRef.current = 0;
      setStale((prev) => (prev ? false : prev));
    } catch (e) {
      // Transient by nature (this runs on a timer), and ONE failure says
      // nothing: a hiccup between two polls is not worth a word.
      //
      // A RUN OF THEM IS DIFFERENT, and this used to report it only when the
      // transcript was empty. So the failure mode that matters — a chat with
      // plenty on screen, silently frozen because the backend went away — was
      // the exact case that said nothing at all. The operator had no way to tell
      // "it is still thinking" from "this browser stopped hearing minutes ago",
      // which is a large share of what "the chat is stuck" has meant.
      // The first failure of a run starts the outage clock. Later failures in
      // the same run leave it where it is, which is what makes the floor
      // measure one continuous outage rather than the gap since a poll.
      if (failuresRef.current === 0) outageSinceRef.current = Date.now();
      failuresRef.current += 1;
      if (isStale(failuresRef.current, outageSinceRef.current, Date.now())) {
        setStale((prev) => (prev ? prev : true));
      }
      if (stateRef.current.order.length === 0) {
        onErrorRef.current({
          text: `Couldn't load this conversation: ${e}`,
          endpoint: `/api/chat/threads/${id}/events`,
          retry: () => { void sync(); },
        });
      }
    }
  }, []);

  // Opening a thread: drop everything and read the log from the start — UNLESS
  // this tab already holds a finished snapshot of this exact thread, in which
  // case seed from that instead of going blank. Either way it is one commit:
  // a stale transcript (this thread's OR the previous one's) must never be
  // briefly shown under a new thread's title.
  useEffect(() => {
    threadIdRef.current = threadId;
    // A new thread starts from a clean slate: the previous chat's stale run,
    // failure count and "load older" bookkeeping say nothing about this one.
    failuresRef.current = 0;
    outageSinceRef.current = 0;
    setStale(false);
    loadingOlderRef.current = false;
    setLoadingOlder(false);
    setOlderError(null);
    pendingCatchUpRef.current = null;

    if (!threadId) {
      dispatch({ type: "reset", threadId: null });
      return;
    }

    const cached = threadCache.get(threadId);
    if (cached) {
      // PAINT FROM CACHE, IN THIS COMMIT. `seedThread` checks the snapshot's
      // own threadId against `threadId` itself (see threadStore.ts) — a
      // second check on top of the cache already being keyed by threadId —
      // so this can never seed thread B's transcript under thread A's id.
      dispatch({ type: "seedThread", threadId, snapshot: cached.snapshot });
      // Tell the branch tracker what we ALREADY believe, rather than leaving
      // it at whatever the previous thread left behind (or `undefined`).
      // This is what makes the catch-up sync's own branch-change detection
      // (see `sync` above) hold for a cached thread exactly as it holds for a
      // live one: if the branch this cache remembers is no longer current,
      // `branchRef.current.id !== nextBranch` fires with `first === false`,
      // and `sync` drops this state and re-reads from zero rather than
      // merging a superseded branch's messages onto the current line. A
      // cached line for a branch that moved on while this tab was away is
      // invalidated by the exact same path a live branch change already
      // takes, not a new one.
      branchRef.current = { id: cached.branch.id, count: cached.branch.count,
                             head: cached.branch.head, pending: false };
      setBranch(cached.branch);
      // `hydrated` goes true in this same commit — there is nothing left to
      // wait on to show the transcript.
      setHydratedId(threadId);
      // Ask the effect below to run exactly one cursor sync once this seed
      // has committed (see the STALE-WHILE-REVALIDATE comment above
      // `POLL_MS` for why it cannot just be called here).
      pendingCatchUpRef.current = threadId;
      return;
    }

    // CACHE MISS: today's behaviour, unchanged.
    //
    // FORGET THE PREVIOUS THREAD'S BRANCH. The cache-HIT path above re-seeds
    // `branchRef` (and says why); this path did not, so the ref kept the
    // OUTGOING thread's branch id while the incoming thread loaded. Every
    // thread has its own branch id, so the first response for the new thread
    // then satisfied `branchRef.current.id !== nextBranch` with `first ===
    // false` — a phantom branch CHANGE on a thread that had merely been
    // switched to — and took the drop-and-re-read path above. On a new chat
    // that fires on every single send, because sending is what creates the
    // thread. `undefined` is the honest value here: it means "no response yet
    // for this thread", which is exactly true one line after the reset, and it
    // is what makes the next response `first`.
    branchRef.current = { id: undefined, count: 0, head: null, pending: false };
    dispatch({ type: "reset", threadId });
    let cancelled = false;
    const requestedAt = Date.now();
    (async () => {
      try {
        // A cold open during a backend restart 502s the same way the thread
        // list does; ride it out quietly rather than banner over an empty
        // transcript for a blip. The poll path below already suppresses a
        // banner when content is on screen, so this is the one first-load case
        // that needed it.
        const page = await withTransientRetry(
          () => chatApi.threadEvents(threadId, 0, { tail: INITIAL_TAIL }));
        if (cancelled || threadIdRef.current !== threadId) return;
        // RECORD THE BRANCH THIS PAGE IS FOR. This fetch answers the same
        // question `sync` does, so it must leave the branch tracker in the
        // same state a sync would; otherwise the ref sits at `undefined` until
        // some later sync happens to run, and the branch controls read as
        // "this chat has no branches" in the meantime.
        branchRef.current = {
          id: page.branch?.id ?? null,
          count: page.branch_count ?? 0,
          head: page.branch?.head ?? null,
          pending: false,
        };
        setBranch({ id: page.branch?.id ?? null, name: page.branch?.name ?? "",
                    count: page.branch_count ?? 0, head: page.branch?.head ?? null });
        dispatch({
          type: "eventsReceived",
          events: page.events || [],
          lastSeq: page.last_seq || 0,
          activeTurn: page.active_turn ?? null,
          queued: page.queued,
          queueHeld: page.queue_held,
          queueError: page.queue_error,
          pendingOperation: page.pending_operation,
          clearedThrough: page.cleared_through,
          cutMode: page.cut_mode,
          requestedAt,
          hasMore: page.has_more ?? false,
        });
      } catch (e) {
        if (!cancelled) {
          onErrorRef.current({
            text: `Couldn't load this conversation: ${e}`,
            endpoint: `/api/chat/threads/${threadId}/events`,
            retry: () => { void sync(); },
          });
        }
      } finally {
        // Hydrated on failure too: a load that failed is settled, and the log
        // must be free to show the error instead of spinning forever.
        if (!cancelled && threadIdRef.current === threadId) setHydratedId(threadId);
      }
    })();
    return () => { cancelled = true; };
  }, [threadId]);

  // THE CACHE CATCH-UP. Runs once, only after a cache-hit seed has actually
  // committed (`state.threadId` catching up to `pendingCatchUpRef.current` IS
  // that signal — it cannot be true before the seed's dispatch has landed).
  // At that point `stateRef.current` is the seeded state, so `sync()` reads
  // the CACHED cursor and issues one ordinary `after=<cursor>` request rather
  // than a fresh tail read. See the STALE-WHILE-REVALIDATE comment above
  // `POLL_MS`.
  useEffect(() => {
    if (pendingCatchUpRef.current && state.threadId === pendingCatchUpRef.current) {
      pendingCatchUpRef.current = null;
      void sync();
    }
  }, [state.threadId, sync]);

  // KEEP THE CACHE CURRENT while this thread is the one on screen, so leaving
  // and coming back finds it exactly as this tab left it — a run that
  // finished, a message that arrived, a page of older history that was
  // loaded. Keyed off `state.threadId`, never off the `threadId` prop: the
  // two briefly disagree during a switch (`stateRef` only catches up at
  // render time), and writing under the prop's id here would file the
  // OUTGOING thread's fields under the INCOMING thread's id.
  //
  // Gated on `hydratedId` matching, not just `state.threadId` being set: the
  // very first render of a cache-miss thread already has `state.threadId`
  // set (by the `reset` dispatch) with `byId`/`order` still empty, because
  // the tail fetch hasn't landed yet. Writing THAT to the cache would let a
  // later visit "hit" on a snapshot that is actually blank — the seed would
  // paint nothing, catch up a moment later, and in between show a chat that
  // looks freshly emptied. Waiting for `hydratedId` means only a page this
  // tab has actually confirmed loaded (successfully or with a settled error;
  // see the mount effect's `finally`) is ever offered back to a future visit.
  useEffect(() => {
    if (!state.threadId || hydratedId !== state.threadId) return;
    threadCache.set(state.threadId, snapshotThread(state), branch);
  }, [state, branch, hydratedId]);

  // Back to the foreground, and a slow tick while visible. The transcript can
  // have moved on while this tab was hidden, and showing a stale log as though
  // it were current is the same "the browser does not reflect the backend"
  // problem in another place.
  // `immediate: false` because the load effect above already fetched this
  // thread; the catch-up sync on the way back from hidden is what matters here,
  // and usePoll does that one unconditionally.
  usePoll(sync, POLL_MS, { enabled: !!threadId, immediate: false });

  // Held in a ref so the stream effect depends on `threadId` alone: re-creating
  // the EventSource whenever `sync` changed identity would tear down and reopen
  // the connection constantly, which is a worse poll.
  const syncRef = useRef(sync);
  syncRef.current = sync;

  // THE CHANGE STREAM. Anything that moves this thread — a message from another
  // device, a queued message the server delivered, a turn started elsewhere, a
  // compaction — wakes this and it syncs at once. The stream carries no
  // content: it says the thread changed, and `sync` is the same cursor-based
  // read it has always been, so there is still exactly one path by which a
  // client learns what a chat contains.
  useEffect(() => {
    if (!threadId) return;
    let es: EventSource | null = null;
    let retry: number | undefined;
    let closed = false;
    let attempt = 0;

    const open = () => {
      if (closed) return;
      es = new EventSource(`/api/chat/threads/${encodeURIComponent(threadId)}/stream`);
      es.addEventListener("changed", () => {
        attempt = 0;
        void syncRef.current();
      });
      es.onerror = () => {
        // A dropped stream is ordinary (a restart, a proxy timeout, sleep).
        // Reconnect with a bounded backoff, and sync on the way back in: the
        // reconnect itself is the moment this tab is most likely to be behind.
        es?.close();
        es = null;
        if (closed) return;
        const wait = Math.min(1000 * 2 ** attempt++, 30_000);
        retry = window.setTimeout(() => { void syncRef.current(); open(); }, wait);
      };
    };
    open();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      es?.close();
    };
  }, [threadId]);

  // TWO BOUNDARIES, because the transcript has two halves that move at very
  // different rates. The committed half is keyed on the committed log itself
  // (and the active run's ID, which decides which mid-run messages are held
  // back) — none of which a streamed frame touches. The live half is keyed on
  // the run and the pending sends, and is cheap.
  //
  // Before this, one memo on `[state]` rebuilt everything on every SSE frame:
  // `turnDelta` produces a new state per token, so appending one token re-derived
  // every message in the thread, dozens of times a second, for the whole turn.
  const committed = useMemo(
    () => selectCommitted(state),
    // `state.cut` is in the list because selectCommitted draws the divider from
    // it: a fold can move the cut WITHOUT appending events (the boundary lands,
    // the count changes), and without this dep the divider kept its old count
    // until the next unrelated event redrew it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.byId, state.order, state.idBySeq, state.activeTurn?.turnId, state.cut],
  );
  const transcript = useMemo(
    () => composeTranscript(committed, state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [committed, state.activeTurn, state.pending],
  );

  return { state, stateRef, dispatch, transcript, sync, turnActive: !!state.activeTurn,
           liveShown: isLiveShown(committed, state),
           hydrated, stale, branch, loadOlder, loadingOlder, olderError };
}
