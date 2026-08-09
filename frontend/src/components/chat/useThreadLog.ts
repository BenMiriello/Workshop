import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { chatApi } from "../../lib/chat";
import {
  initialThreadState, threadReducer, type ThreadAction, type ThreadState,
} from "./threadStore";
import { selectTranscript, type TranscriptBlock } from "./transcriptBlocks";

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

const POLL_MS = 10_000;

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
};

export function useThreadLog({
  threadId,
  onError,
}: {
  threadId: string | null;
  onError: (msg: string) => void;
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

  const sync = useCallback(async () => {
    const id = threadIdRef.current;
    if (!id) return;
    const after = stateRef.current.threadId === id ? stateRef.current.cursor : 0;
    try {
      const page = await chatApi.threadEvents(id, after);
      if (threadIdRef.current !== id) return;
      dispatch({
        type: "eventsReceived",
        events: page.events || [],
        lastSeq: page.last_seq || 0,
        activeRun: page.active_run ?? null,
      });
    } catch (e) {
      // Transient by nature (this runs on a timer). Only the initial load has an
      // empty screen to explain, and that path reports its own failure.
      if (stateRef.current.order.length === 0) {
        onErrorRef.current(`Couldn't load this conversation: ${e}`);
      }
    }
  }, []);

  // Opening a thread: drop everything and read the log from the start. One
  // commit for the reset so a stale transcript is never briefly shown under a
  // new thread's title.
  useEffect(() => {
    threadIdRef.current = threadId;
    dispatch({ type: "reset", threadId });
    if (!threadId) return;
    let cancelled = false;
    (async () => {
      try {
        const page = await chatApi.threadEvents(threadId, 0);
        if (cancelled || threadIdRef.current !== threadId) return;
        dispatch({
          type: "eventsReceived",
          events: page.events || [],
          lastSeq: page.last_seq || 0,
          activeRun: page.active_run ?? null,
        });
      } catch (e) {
        if (!cancelled) onErrorRef.current(`Couldn't load this conversation: ${e}`);
      }
    })();
    return () => { cancelled = true; };
  }, [threadId]);

  // Back to the foreground, and a slow tick while visible. The transcript can
  // have moved on while this tab was hidden, and showing a stale log as though
  // it were current is the same "the browser does not reflect the backend"
  // problem in another place.
  useEffect(() => {
    if (!threadId) return;
    const tick = () => { if (!document.hidden) void sync(); };
    const onVis = () => { if (!document.hidden) void sync(); };
    window.addEventListener("focus", onVis);
    document.addEventListener("visibilitychange", onVis);
    const timer = window.setInterval(tick, POLL_MS);
    return () => {
      window.removeEventListener("focus", onVis);
      document.removeEventListener("visibilitychange", onVis);
      window.clearInterval(timer);
    };
  }, [threadId, sync]);

  const transcript = useMemo(() => selectTranscript(state), [state]);

  return { state, stateRef, dispatch, transcript, sync, runActive: !!state.activeRun };
}
