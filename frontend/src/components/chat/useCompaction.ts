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
// messages folded into a summary", is read rather than guessed, and survives a
// reload. That is the report.
//
// BUT THE FOLD IS VISIBLE WHILE IT RUNS, and dropping the bubble is what took
// that away. A compact is a ~30s model call, and between the operator hitting
// it and the divider appearing there was nothing on screen at all: the typed
// command vanished into a dialog and the transcript sat still. Reported
// 2026-08-12 as "it just disappeared, it didn't run". `mode` is what this hook
// publishes for that: which fold is in flight, held until the boundary lands, so
// the log can draw FoldingNote at the foot in the divider's own language and
// the real divider then takes its place.
//
// IT IS ANNOUNCED IN ONE PLACE, IN THE LOG. This used to report itself in the
// notice bar under the toolbar, which produced the sequence the operator
// described: a banner pinned above the transcript for the length of the fold,
// and only then a bubble down in the chat. Two announcements of one action, in
// two places, in the wrong order. Worse, a message sent during that window
// rendered ABOVE the banner in reading order, because a bar above the log has no
// position in time at all. One line, where the result lands, is the whole
// report.
//
// NEITHER IS GATED ON `streaming`. Both are server-side operations on the saved
// thread, independent of whatever turn is in flight, and refusing while a turn
// ran meant /compact was rejected almost every time it was reached for — which
// is exactly when context is under pressure.
/** Which fold is in flight. Both hold the composer; only the wording differs. */
export type FoldMode = "compact" | "clear";

export type Compaction = {
  // Bumped after a fold so the context meter refetches: folding does not change
  // the message count, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
  clear: () => Promise<void>;
  /** Call off the fold in flight. Wired to the Stop control on FoldingNote. */
  cancelFold: () => void;
  /** The cancel request is out, or has succeeded and the fold is winding up. */
  foldStopping: boolean;
};

export function useCompaction({
  activeIdRef, setFolding, setErr, sync,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  // Owned by the chat, not by this hook: the composer, the message queue and the
  // log's in-flight line all read it, and all are declared before it. Null means
  // no fold is running; otherwise it is WHICH one, because the two say different
  // things while they run.
  setFolding: (mode: FoldMode | null) => void;
  setErr: (problem: ChatProblem | null) => void;
  // Pull the log after a boundary lands, so the divider draws from the event
  // rather than from an optimistic count.
  sync: () => Promise<void>;
}): Compaction {
  const [ctxNonce, setCtxNonce] = useState(0);
  const [foldStopping, setFoldStopping] = useState(false);

  const compact = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr({ text: "Nothing to compact. This chat has no messages yet.", endpoint: null }); return; }
    setErr(null);
    // Held so a message typed right after this call does not become a turn
    // against the session being dropped, and shown at the foot of the log for
    // as long as the hold lasts.
    setFolding("compact");
    try {
      const r = await chatApi.compactThread(id);
      // The fold moved the cut and wrote the summary; the divider is drawn from
      // the boundary event the server appended, so pull it rather than guessing.
      void sync();
      setCtxNonce((n) => n + 1);
      // WHAT ACTUALLY HAPPENED, in the order it matters. A failed summarizer is
      // the case this exists for: the fold is a model call, it can fail, and
      // this endpoint used to answer 200 over a failure while the chat said
      // "Compacted. Earlier messages are now a summary." On 2026-08-12 that was
      // not a rare path — the call passed its whole prompt in argv, so it died
      // with E2BIG on every thread long enough to be worth compacting.
      if (r.error) {
        setErr({
          text: `Couldn't compact: ${r.error}. Nothing was folded and the chat is unchanged.`,
          endpoint: `/api/chat/threads/${id}/compact`,
        });
      } else if (r.cancelled) {
        // Stopped from the fold's own line. Say nothing: the operator asked for
        // it to stop and it stopped, the note disappears, and the transcript is
        // visibly unchanged. An error line here would report their own action
        // back to them as a problem.
        return;
      } else if (r.busy) {
        // Refused, not deferred: an operation is already in flight and THIS
        // press did nothing. Promising a landing here would be a lie.
        setErr({ text: "A compact or clear is already in progress on this chat.", endpoint: null });
      } else if (r.deferred) {
        // The fold is computed; its writes wait for the reply in flight. The
        // divider appears when it lands, via the change stream. Said in one
        // line so pressing compact mid-turn does not read as a no-op.
        setErr({ text: "Compact is waiting for the current reply to finish, then it lands.", endpoint: null });
      } else if (!r.folded && !r.session_reset) {
        setErr({ text: "Nothing to compact. This chat is already short.", endpoint: null });
      }
    } catch (e) {
      setErr({
        text: `Couldn't compact: ${String(e)}. The transcript is unchanged.`,
        endpoint: `/api/chat/threads/${id}/compact`,
      });
    } finally {
      setFolding(null);
    }
  }, [activeIdRef, setFolding, setErr, sync]);

  const clear = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr({ text: "Nothing to clear. This chat has no messages yet.", endpoint: null }); return; }
    setErr(null);
    // Reuses the compact hold: it closes the same window either command needs
    // closed — a message typed right after this call must not become a turn
    // before the boundary actually moves, or it would land above the cut and
    // read as having vanished.
    setFolding("clear");
    try {
      const r = await chatApi.clearThreadContext(id);
      // `cleared` is about the TRANSCRIPT boundary; `session_reset` is about the
      // live session. A fully folded chat has no messages left to cut, so
      // `cleared` is false, and this reported "Nothing to clear" for a call that
      // had just reset the agent's session out from under the operator. Two
      // different things happened and only one was named. Nothing happened only
      // when NEITHER did.
      if (r.cancelled) {
        return;
      } else if (r.busy) {
        setErr({ text: "A compact or clear is already in progress on this chat.", endpoint: null });
      } else if (r.deferred) {
        setErr({ text: "Clear is waiting for the current reply to finish, then it lands.", endpoint: null });
      } else if (!r.cleared && !r.session_reset) {
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
      setFolding(null);
    }
  }, [activeIdRef, setFolding, setErr, sync]);

  // NOT FIRE AND FORGET, which is what made this read as a dead button.
  //
  // It was: the press sent the POST, discarded the answer and swallowed any
  // failure, on the reasoning that the compact request would report the outcome
  // itself. It does, but not for up to forty seconds: the fold's own request is
  // still inside the summarizer, and `folding` is only cleared in its `finally`.
  // So the whole visible sequence was press, nothing, half a minute more of
  // "Compacting", then the line vanishing with no explanation. Measured from the
  // backend log on 2026-08-14: two cancels, both answered 200, and the operator's
  // report was "it did nothing". The cancel had almost certainly worked.
  //
  // So the press owns the feedback. `stopping` lands immediately (the control
  // spins, the line says Stopping), and a CONFIRMED cancel retires the note at
  // once rather than waiting for a request whose result is already known.
  const cancelFold = useCallback(() => {
    const id = activeIdRef.current;
    if (!id) return;
    setFoldStopping(true);
    void chatApi.cancelThreadOperation(id)
      .then((r) => {
        if (r.cancelled) setFolding(null);
        // `cancelled: false` means it finished while the press was in flight.
        // Nothing to say: its own request is about to return and clear the line.
      })
      .catch((e) => {
        setErr({
          text: `Could not stop the compact: ${String(e)}. It is still running.`,
          endpoint: `/api/chat/threads/${id}/operation/cancel`,
        });
      })
      .finally(() => setFoldStopping(false));
  }, [activeIdRef, setFolding, setErr]);

  return { ctxNonce, compact, clear, cancelFold, foldStopping };
}
