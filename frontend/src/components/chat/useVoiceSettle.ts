import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { chatApi, type ChatMsg } from "../../lib/chat";
import type { VoiceSnapshot } from "../../lib/voiceSession";

// Holding a finished spoken exchange on screen until its persisted messages land.
//
// THE PROBLEM. A spoken turn renders through the live voice overlay, which only
// exists while the session is thinking or speaking. When the turn ends, the
// overlay unmounts on the SAME commit that flips the state — but the messages it
// was showing are not in the log yet, because the voice worker persists them a
// beat after playback and the refetch is async. So the just-finished exchange
// vanished and popped back a moment later.
//
// THE FIX, and why each part is the way it is:
//
//  - On the finish transition, snapshot the exchange into `settling` and keep
//    rendering it. Swap it for the real messages in ONE commit when they land.
//  - This runs in a LAYOUT effect, not a passive one: the overlay unmounts on
//    the commit that flips the state, so `settling` must be set BEFORE the
//    browser paints or the one-frame gap is still there.
//  - The poll is REF-driven rather than tied to the effect's cleanup. The next
//    turn starting is a benign listening -> thinking transition, and tying the
//    poll to the effect let that cancel an in-flight settle. Turn N stays on
//    screen (as the settling overlay, above turn N+1's live overlay) until its
//    own messages land.
//  - Voice off, a thread switch, or unmount cancels the poll, so a stale settle
//    cannot overwrite the log of another thread or flash back on re-entry.

const MAX_TRIES = 8;
const POLL_MS = 250;

export function useVoiceSettle({
  voiceOn,
  activeId,
  vsnap,
  messagesRef,
  setMessages,
  refreshThreads,
}: {
  voiceOn: boolean;
  activeId: string | null;
  vsnap: VoiceSnapshot;
  messagesRef: React.MutableRefObject<ChatMsg[]>;
  setMessages: (msgs: ChatMsg[]) => void;
  refreshThreads: () => void;
}): { settling: { user: string; assistant: string } | null } {
  const [settling, setSettling] = useState<{ user: string; assistant: string } | null>(null);
  const pollRef = useRef<{ cancelled: boolean } | null>(null);

  const startSettle = useCallback((id: string, user: string, assistant: string, baseLen: number) => {
    const handle = { cancelled: false };
    pollRef.current = handle;
    setSettling({ user, assistant });
    let tries = 0;
    const finishSettle = (msgs: ChatMsg[] | null) => {
      if (handle.cancelled) return;
      if (msgs) setMessages(msgs);
      // Only clear if this settle is still the active one (a newer turn may have
      // taken over) — the swap into the log is a single commit either way.
      setSettling((s) => (pollRef.current === handle ? null : s));
      if (pollRef.current === handle) pollRef.current = null;
      refreshThreads();
    };
    const poll = () => {
      if (handle.cancelled) return;
      chatApi.getThread(id)
        .then((t) => {
          if (handle.cancelled) return;
          if (t.messages.length > baseLen) finishSettle(t.messages);   // the new turn persisted
          else if (++tries >= MAX_TRIES) finishSettle(t.messages);     // give up: swap what we have
          else window.setTimeout(poll, POLL_MS);
        })
        .catch(() => {
          if (handle.cancelled) return;
          if (++tries >= MAX_TRIES) finishSettle(null);
          else window.setTimeout(poll, POLL_MS);
        });
    };
    poll();
  }, [refreshThreads, setMessages]);

  const prevVoiceState = useRef(vsnap.state);
  useLayoutEffect(() => {
    const prev = prevVoiceState.current;
    prevVoiceState.current = vsnap.state;
    if (!voiceOn) return;
    const finished = (prev === "speaking" || prev === "thinking")
      && (vsnap.state === "listening" || vsnap.state === "idle");
    if (!finished || !activeId) return;
    const user = vsnap.lastUser;
    const assistant = vsnap.sentences.map((s) => s.text).join(" ").trim();
    if (!user && !assistant) { refreshThreads(); return; }
    startSettle(activeId, user, assistant, messagesRef.current.length);
    // vsnap.lastUser/sentences are read AT the finish transition; state is the
    // trigger, and listing them would re-run this mid-turn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vsnap.state, voiceOn, activeId, startSettle]);

  useEffect(() => {
    if (!voiceOn) {
      if (pollRef.current) pollRef.current.cancelled = true;
      setSettling(null);
    }
    return () => { if (pollRef.current) pollRef.current.cancelled = true; };
  }, [voiceOn, activeId]);

  return { settling };
}
