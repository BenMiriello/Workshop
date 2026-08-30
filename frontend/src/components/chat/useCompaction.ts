import { useCallback, useState } from "react";
import { chatApi } from "../../lib/chat";
import type { ChatProblem } from "./chatProblem";
import type { FoldMode } from "./foldControls";

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
// THE TWO ARE NO LONGER THE SAME SHAPE, and this file is where that shows.
//
// /compact is a TURN again. The server sends the CLI's own /compact on the
// thread's live session and returns the turn it started; the boundary, the
// reply and the new meter reading arrive on the change stream. A 2026-08-12
// measurement said headless mode could not run a slash command at all, and
// this hook was rewritten around that. It was a false negative (it read the
// session transcript two seconds into an operation that takes 12 to 190
// seconds) and it was corrected on 2026-08-27. Workshop's own summarizing
// fold, which existed only to work around it, is deleted.
//
// /clear is still a server-side edit: a handful of field writes and a session
// drop, with no turn and nothing to wait for.
//
// SO ONLY /clear USES THE HOLD. `setFolding` publishes which operation is in
// flight so the log can draw FoldingNote at the foot; a compact needs none of
// it, because a running turn is already the most visible thing on the screen
// and the ordinary working indicator draws it.
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
/** Which context operation is in flight and holding the composer. Only a clear
 *  can be: compaction is the provider's own /compact, run as an ordinary turn,
 *  and a turn holds nothing. Re-exported from `foldControls`, which owns it,
 *  so the buttons and the hold cannot drift apart. */
export type { FoldMode } from "./foldControls";

// THE NOTE MUST BE ON SCREEN LONG ENOUGH TO READ. A compact whose tail is
// already short is a ~150ms round trip (no summarizer call, no fold to land),
// so `folding` used to flip on and off inside one render frame and the
// operator never saw FoldingNote at all — the same "it just disappeared"
// report the note exists to fix, just moved to the fast path instead of the
// slow one. This floors how long the line stays mounted; it does not fake how
// long the operation took, it only holds the door a beat after it is done.
const _MIN_FOLD_VISIBLE_MS = 700;

function _minFoldVisible(startedAt: number): Promise<void> {
  const remaining = _MIN_FOLD_VISIBLE_MS - (Date.now() - startedAt);
  return remaining > 0 ? new Promise((r) => setTimeout(r, remaining)) : Promise.resolve();
}

export type Compaction = {
  // Bumped after either operation so the context meter refetches: neither one
  // changes the message count, which is what otherwise drives that read.
  ctxNonce: number;
  compact: () => Promise<void>;
  clear: () => Promise<void>;
  /** Call off the clear in flight. Wired to the Stop control on FoldingNote. */
  cancelFold: () => void;
  /** The cancel request is out, or has succeeded and the clear is winding up. */
  foldStopping: boolean;
};

export function useCompaction({
  activeIdRef, setFolding, setErr, sync,
}: {
  activeIdRef: React.MutableRefObject<string | null>;
  // Owned by the chat, not by this hook: the composer, the message queue and the
  // log's in-flight line all read it, and all are declared before it. Null means
  // nothing is running.
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
    try {
      const r = await chatApi.compactThread(id);
      if (r.error) {
        setErr({
          text: `Couldn't compact: ${r.error}. The chat is unchanged.`,
          endpoint: `/api/chat/threads/${id}/compact`,
        });
        return;
      }
      // QUEUED IS NOT A FAILURE. A slash command cannot be delivered into a
      // turn that is already running, so it waits in the thread's own queue
      // and the drain sends it the moment that turn ends. Saying nothing here
      // would read as a dead button, so say the one true thing and no more.
      if (r.queued) {
        setErr({ text: "Compact is queued behind the reply in flight.", endpoint: null });
        return;
      }
      // The turn is running. Pull the log so its first segment appears without
      // waiting for the next poll, and bump the meter, which does not otherwise
      // refetch on a compaction because the message count barely moves.
      void sync();
      setCtxNonce((n) => n + 1);
    } catch (e) {
      setErr({
        text: `Couldn't compact: ${String(e)}. The chat is unchanged.`,
        endpoint: `/api/chat/threads/${id}/compact`,
      });
    }
  }, [activeIdRef, setErr, sync]);

  const clear = useCallback(async () => {
    const id = activeIdRef.current;
    if (!id) { setErr({ text: "Nothing to clear. This chat has no messages yet.", endpoint: null }); return; }
    setErr(null);
    // Reuses the compact hold: it closes the same window either command needs
    // closed — a message typed right after this call must not become a turn
    // before the boundary actually moves, or it would land above the cut and
    // read as having vanished.
    setFolding("clear");
    const startedAt = Date.now();
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
      await _minFoldVisible(startedAt);
      setFolding(null);
    }
  }, [activeIdRef, setFolding, setErr, sync]);

  // NOT FIRE AND FORGET, which is what made this read as a dead button.
  //
  // It was: the press sent the POST, discarded the answer and swallowed any
  // failure, on the reasoning that the clear's own request would report the
  // outcome. It does, but not until it lands, and `folding` is only cleared in
  // its `finally`. So the whole visible sequence was press, nothing, then the
  // line vanishing with no explanation. Measured from the backend log on
  // 2026-08-14: two cancels, both answered 200, and the operator's report was
  // "it did nothing". The cancel had almost certainly worked.
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
          text: `Could not stop the clear: ${String(e)}. It is still running.`,
          endpoint: `/api/chat/threads/${id}/operation/cancel`,
        });
      })
      .finally(() => setFoldStopping(false));
  }, [activeIdRef, setFolding, setErr]);

  return { ctxNonce, compact, clear, cancelFold, foldStopping };
}
