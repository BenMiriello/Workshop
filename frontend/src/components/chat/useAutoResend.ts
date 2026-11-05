import { useCallback, useEffect, useRef } from "react";
import {
  RESEND_IDLE, RESEND_IDLE_CHECK_MS, resendBackoff, resendInOrder, resendOrder,
  resendPassEnded, resendTriggered,
} from "./autoResend";
import { pendingSends, type ThreadState } from "./threadStore";

/** Re-send this thread's unsent messages when connectivity returns. The rule
 *  is autoResend.ts; this is its wiring. Returns the trigger for the change
 *  stream's reopen; the window's `online` event is subscribed here, and so are
 *  the page's own triggers (a clock, focus, the page being shown), which take
 *  only the sends that failed transiently. */
export function useAutoResend({
  stateRef,
  retry,
}: {
  stateRef: React.MutableRefObject<ThreadState>;
  retry: (clientId: string) => Promise<void>;
}): () => void {
  const gateRef = useRef(RESEND_IDLE);
  const retryRef = useRef(retry);
  retryRef.current = retry;
  // Whether the pass about to run was asked for by the connection coming back,
  // which re-sends every unsent message, rather than by the page on its own.
  const allRef = useRef(false);

  const pass = useCallback(async () => {
    const all = allRef.current;
    allRef.current = false;
    const send = async (clientId: string) => {
      // Gone (sent, cancelled, or the thread changed) is not a failure.
      if (stateRef.current.pending[clientId]?.status !== "failed") return true;
      await retryRef.current(clientId);
      return stateRef.current.pending[clientId]?.status !== "failed";
    };
    await resendInOrder(resendOrder(pendingSends(stateRef.current), !all), send);
  }, [stateRef]);

  const start = useCallback(() => {
    const { next, start: begin } = resendTriggered(gateRef.current);
    gateRef.current = next;
    if (!begin) return;
    const run = async () => {
      for (;;) {
        try {
          await pass();
        } catch (e) {
          console.warn("chat: resending unsent messages failed", e);
        }
        const ended = resendPassEnded(gateRef.current);
        gateRef.current = ended.next;
        if (!ended.start) return;
      }
    };
    void run();
  }, [pass]);

  const trigger = useCallback(() => {
    allRef.current = true;
    start();
  }, [start]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") start();
    };
    window.addEventListener("online", trigger);
    window.addEventListener("focus", start);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("online", trigger);
      window.removeEventListener("focus", start);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [trigger, start]);

  // THE CLOCK, for the outage nothing else announces: a backend that came back
  // under a connection that never dropped. It only reads the store until a
  // send is owed, then backs off while one stays owed.
  useEffect(() => {
    let attempt = 0;
    let timer: number | undefined;
    const tick = () => {
      const owed = resendOrder(pendingSends(stateRef.current), true).length > 0;
      if (!owed) attempt = 0;
      else if (document.visibilityState !== "hidden") {
        start();
        attempt += 1;
      }
      timer = window.setTimeout(tick, owed ? resendBackoff(attempt) : RESEND_IDLE_CHECK_MS);
    };
    timer = window.setTimeout(tick, RESEND_IDLE_CHECK_MS);
    return () => window.clearTimeout(timer);
  }, [start, stateRef]);

  return trigger;
}
