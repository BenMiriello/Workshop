import { useEffect, useMemo, useState } from "react";
import { chatApi, type ChatMsg } from "../../lib/chat";
import { estimateContextTokens } from "./chatScope";
import { contextWindowFor } from "./models";

// How full this conversation's context is, under the composer.
//
// Owns its own reading. The backend's count is the REAL one — it assembles the
// actual prompt (persona + live app context + compacted history), which the
// client cannot see — so that wins whenever it is available, and the client
// estimate is only the fallback before a thread's first turn has measured
// anything. Getting this backwards is what once pinned the meter at a confident
// 100% on a chat using under a third of its window.
//
// `nonce` exists because a forced /compact changes the answer without changing
// `messages.length`, which is otherwise what drives the refetch.

const RING_R = 7;
const RING_C = 2 * Math.PI * RING_R;

export default function ContextMeter({
  activeId, streaming, messages, draft, model, nonce,
}: {
  activeId: string | null;
  streaming: boolean;
  messages: ChatMsg[];
  draft: string;
  model: string;
  nonce: number;
}) {
  const [server, setServer] = useState<{ used: number; window: number } | null>(null);

  useEffect(() => {
    if (!activeId || streaming) return;
    let cancelled = false;
    chatApi.threadContext(activeId)
      .then((r) => { if (!cancelled) setServer({ used: r.used_tokens, window: r.window_tokens }); })
      .catch(() => { /* keep the client estimate */ });
    return () => { cancelled = true; };
  }, [activeId, messages.length, streaming, nonce]);

  // Drop a stale count on thread switch rather than showing one chat's usage
  // against another's transcript.
  useEffect(() => { setServer(null); }, [activeId]);

  const ctx = useMemo(() => {
    if (server) {
      // Real assembled-context count plus the live (unsent) draft.
      const used = server.used + Math.ceil(draft.length / 4);
      return { used, win: server.window, pct: Math.min(100, Math.round((used / server.window) * 100)) };
    }
    const used = estimateContextTokens(messages, draft);
    const win = contextWindowFor(model);
    return { used, win, pct: Math.min(100, Math.round((used / win) * 100)) };
  }, [server, messages, draft, model]);

  // A single accent tone, no threshold colors: this is a reading, not a status.
  // The arc is a dash on a rotated circle so it fills clockwise from 12 o'clock.
  return (
    <div
      className="chat-context-meter"
      title={`~${ctx.used.toLocaleString()} of ${ctx.win.toLocaleString()} tokens used in this chat`}
    >
      <svg className="chat-context-meter__ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
        <circle className="chat-context-meter__track" cx="9" cy="9" r={RING_R} />
        <circle
          className="chat-context-meter__arc"
          cx="9" cy="9" r={RING_R}
          style={{ strokeDasharray: `${(ctx.pct / 100) * RING_C} ${RING_C}` }}
        />
      </svg>
      <span className="chat-context-meter__pct">{ctx.pct}%</span>
    </div>
  );
}
