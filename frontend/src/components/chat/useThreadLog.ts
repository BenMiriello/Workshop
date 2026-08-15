import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { usePoll } from "../../lib/usePoll";
import { chatApi } from "../../lib/chat";
import {
  initialThreadState, threadReducer, type ThreadAction, type ThreadState,
} from "./threadStore";
import { selectCommitted, composeTranscript, type TranscriptBlock } from "./transcriptBlocks";
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
// reading immediately; the rest loads outward on demand (see `loadOlder`
// below and LoadOlderNote). 30 is a round number a bit above one ordinary
// exchange (a message, a multi-step reply, maybe a follow-up), chosen so the
// common case needs no second fetch at all; it is not load-bearing on
// correctness; any positive size behaves the same way. A branch change also
// re-reads through this path (see the `branchChanged` fetch in `sync`) rather
// than a bespoke "first load" flag, since it is exactly the same situation —
// this tab has nothing of the newly-selected line yet.
const INITIAL_TAIL = 30;

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
  runActive: boolean;
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
   *  at all. See LoadOlderNote and threadStore's `hasMoreOlder`. */
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

  // Consecutive failures, and when a request last landed. Both are needed: a
  // burst of failures says nothing on its own (the first thing a dropped
  // connection does is fail several times quickly), and elapsed time says
  // nothing on its own either, since a quiet chat legitimately goes a minute
  // between polls. See staleNotice.
  const failuresRef = useRef(0);
  const lastOkRef = useRef(Date.now());
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
      if (threadIdRef.current !== id) return;
      dispatch({
        type: "olderEventsReceived",
        events: page.events || [],
        hasMore: page.has_more ?? false,
      });
    } catch (e) {
      if (threadIdRef.current === id) setOlderError(`Couldn't load earlier messages: ${e}`);
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
        setBranch({ id: nextBranch, name: page.branch?.name ?? "",
                    count: page.branch_count ?? 0,
                    head: page.branch?.head ?? null });
        if (!first) {
          // Learned about the change on this very response, which was fetched
          // from the OLD cursor. Drop what we have and ask again from zero.
          branchRef.current.pending = true;
          dispatch({ type: "reset", threadId: id });
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
        activeRun: page.active_run ?? null,
        queued: page.queued,
        queueHeld: page.queue_held,
        queueError: page.queue_error,
        clearedThrough: page.cleared_through,
        cutMode: page.cut_mode,
        requestedAt,
        // Only a fresh (tail) read answers "is there more behind this",
        // because only a fresh read asked. See `hasMoreOlder` in threadStore.
        hasMore: isFreshRead ? (page.has_more ?? false) : undefined,
      });
      // A poll landed, so whatever this chat is showing is current.
      failuresRef.current = 0;
      lastOkRef.current = Date.now();
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
      failuresRef.current += 1;
      if (isStale(failuresRef.current, lastOkRef.current, Date.now())) {
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

  // Opening a thread: drop everything and read the log from the start. One
  // commit for the reset so a stale transcript is never briefly shown under a
  // new thread's title.
  useEffect(() => {
    threadIdRef.current = threadId;
    dispatch({ type: "reset", threadId });
    // A new thread starts from a clean slate: the previous chat's stale run says
    // nothing about this one.
    failuresRef.current = 0;
    lastOkRef.current = Date.now();
    setStale(false);
    if (!threadId) return;
    let cancelled = false;
    const requestedAt = Date.now();
    // Clean slate for the older-page state too: a stale error or a stray
    // "there's more" from the thread just left must not bleed into this one.
    loadingOlderRef.current = false;
    setLoadingOlder(false);
    setOlderError(null);
    (async () => {
      try {
        const page = await chatApi.threadEvents(threadId, 0, { tail: INITIAL_TAIL });
        if (cancelled || threadIdRef.current !== threadId) return;
        dispatch({
          type: "eventsReceived",
          events: page.events || [],
          lastSeq: page.last_seq || 0,
          activeRun: page.active_run ?? null,
          queued: page.queued,
          queueHeld: page.queue_held,
          queueError: page.queue_error,
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
  // `runDelta` produces a new state per token, so appending one token re-derived
  // every message in the thread, dozens of times a second, for the whole turn.
  const committed = useMemo(
    () => selectCommitted(state),
    // `state.cut` is in the list because selectCommitted draws the divider from
    // it: a fold can move the cut WITHOUT appending events (the boundary lands,
    // the count changes), and without this dep the divider kept its old count
    // until the next unrelated event redrew it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.byId, state.order, state.idBySeq, state.activeRun?.runId, state.cut],
  );
  const transcript = useMemo(
    () => composeTranscript(committed, state),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [committed, state.activeRun, state.pending],
  );

  return { state, stateRef, dispatch, transcript, sync, runActive: !!state.activeRun,
           hydrated, stale, branch, loadOlder, loadingOlder, olderError };
}
