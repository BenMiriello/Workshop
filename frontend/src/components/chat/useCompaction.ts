import { useCallback, useState } from "react";
import { chatApi } from "../../lib/chat";
import type { ChatProblem } from "./chatProblem";

// /compact and /clear — the two ways to reclaim a chat's context.
//
// A CHAT'S CONTEXT LIVES IN TWO PLACES:
//
//   1. the STORED TRANSCRIPT, re-assembled whenever a fresh session starts.
//   2. the LIVE CLAUDE SESSION, whose window is what actually fills up.
//
// BOTH HALVES ARE THE SERVER'S JOB NOW, and that is the whole of what this file
// does: one POST each, and the hold that keeps a message typed during it from
// jumping the boundary.
//
// THE LIVE HALF USED TO LIVE HERE, and it never worked. This hook sent the
// CLI's own /compact as an ordinary turn, and carried a queue entry, an insert
// position, a pending bubble and a status line to keep that turn ordered
// against whatever else the operator typed. Every bit of that machinery was
// sequencing a turn that did nothing: the CLI runs here headless (`-p
// --input-format stream-json`), and in that mode a leading slash is parsed into
// a local-command block that nothing executes. The 2026-08-12T05:10:18Z request
// came back in two seconds having recorded the command and freed no window at
// all, while the meter it was supposed to move sat where it was. The endpoint
// in routes/chat.py carries the full evidence.
//
// The server reclaims the live window by DETACHING the session instead — the
// next turn mints a fresh one and re-sends the folded transcript. That is what
// /clear always did, so the two commands now want the same shape, and this file
// is them side by side. Do not reintroduce a turn for either one.
//
// NO REQUEST BUBBLE, for either. It earned its place when it was a real turn
// with a reply streaming under it; without one it is an optimistic message that
// nothing will ever reconcile, which is a bubble stuck on "sending" forever.
// The fold divider MessageList draws from the boundary event says "N earlier
// messages folded into the running summary", is read rather than guessed, and
// survives a reload. That is the report.
//
// NOTHING IS ANNOUNCED ANYWHERE ELSE. This used to report itself in the notice
// bar under the toolbar as well, which produced exactly the sequence the
// operator described: a banner pinned above the transcript for the ~30s the
// fold takes, and only then a bubble down in the chat. Two announcements of one
// action, in two places, in the wrong order. Worse, a message sent during that
// window rendered ABOVE the banner in reading order, because a bar above the log
// has no position in time at all.
//
// NEITHER IS GATED ON `streaming`. Both are server-side operations on the saved
// thread, independent of whatever turn is in flight, and refusing while a turn
// ran meant /compact was rejected almost every time it was reached for — which
// is exactly when context is under pressure.
export type Compaction = {
  // Bumped after a fold so the context meter refetches: folding does not change
  // the message count, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
  clear: () => Promise<void>;
};

export function useCompaction({
  activeIdRef, setCompacting, setErr, sync,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  // Owned by the chat, not by this hook: the composer and the message queue both
  // have to hold while a fold is in flight, and both are declared before it.
  setCompacting: (v: boolean) => void;
  setErr: (problem: ChatProblem | null) => void;
  // Pull the log after a boundary lands, so the divider draws from the event
  // rather than from an optimistic count.
  sync: () => Promise<void>;
}): Compaction {
  const [ctxNonce, setCtxNonce] = useState(0);

  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr({ text: "Nothing to compact. This chat has no messages yet.", endpoint: null }); return; }
    setErr(null);
    // Held so a message typed right after this call does not become a turn
    // against the session being dropped.
    setCompacting(true);
    try {
      const r = await chatApi.compactThread(id);
      setCtxNonce((n) => n + 1);
      // ONE THING HAPPENS NOW, so there is one thing to report. This used to also
      // weigh `will_fold`, the count of messages a server-side fold was about to
      // replace with a summary; folding was removed on 2026-08-12 and the live
      // session is the only thing /compact touches.
      if (!r.session_reset) {
        setErr({ text: "Nothing to compact. This chat has no live session to reclaim.", endpoint: null });
      }
    } catch (e) {
      setErr({
        text: `Couldn't compact: ${String(e)}. The transcript is unchanged.`,
        endpoint: `/api/chat/threads/${id}/compact`,
      });
    } finally {
      setCompacting(false);
    }
  }, [activeIdRef, setCompacting, setErr, sync]);

  const clear = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr({ text: "Nothing to clear. This chat has no messages yet.", endpoint: null }); return; }
    setErr(null);
    // Reuses the compact hold: it closes the same window either command needs
    // closed — a message typed right after this call must not become a turn
    // before the boundary actually moves, or it would land above the cut and
    // read as having vanished.
    setCompacting(true);
    try {
      const r = await chatApi.clearThreadContext(id);
      // `cleared` is about the TRANSCRIPT boundary; `session_reset` is about the
      // live session. A fully folded chat has no messages left to cut, so
      // `cleared` is false, and this reported "Nothing to clear" for a call that
      // had just reset the agent's session out from under the operator. Two
      // different things happened and only one was named. Nothing happened only
      // when NEITHER did.
      if (!r.cleared && !r.session_reset) {
        setErr({ text: "Nothing to clear. This chat has no messages yet.", endpoint: null });
        return;
      }
      // Nothing is written to the transcript: keeping it is the entire point of
      // /clear. The divider comes from the `boundary` event the clear appended.
      void sync();
      setCtxNonce((n) => n + 1);
    } catch (e) {
      setErr({
        text: `Couldn't clear: ${String(e)}. The context is unchanged.`,
        endpoint: `/api/chat/threads/${id}/clear`,
      });
    } finally {
      setCompacting(false);
    }
  }, [activeIdRef, setCompacting, setErr, sync]);

  return { ctxNonce, compact, clear };
}
