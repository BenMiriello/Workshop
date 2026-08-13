import { useEffect, useMemo, useState } from "react";
import { chatApi, type ChatMsg } from "../../lib/chat";
import { estimateContextTokens } from "./chatScope";
import { contextWindowFor } from "./models";
import { contextLabel, contextReading, contextTooltip, type ContextSource } from "./contextReading";

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
  activeId, streaming, messages, draft, model, nonce, onOpen,
}: {
  activeId: string | null;
  streaming: boolean;
  messages: ChatMsg[];
  draft: string;
  model: string;
  nonce: number;
  // Open the breakdown. The reading answers "how full"; the panel behind it
  // answers "with what", which is the question that follows it every time.
  onOpen?: () => void;
}) {
  const [server, setServer] = useState<{ used: number; window: number } | null>(null);
  // A FAILED READ IS NOT THE SAME AS NOT HAVING READ YET. This was
  // `.catch(() => {})` with a comment saying "keep the client estimate", and the
  // meter then presented that estimate exactly as it presents the server's
  // number. Both fall back to the estimate; only one of them is fine.
  const [readFailed, setReadFailed] = useState(false);

  // A live turn must NOT block the reading. It used to: the fetch returned
  // early whenever `streaming` was true, so a meter that first mounted during a
  // turn never got a number at all, and rendered a confident "0%" for the whole
  // turn. The count the server holds mid-turn is the previous turn's, which is
  // stale by one turn and correct to the character; nothing is worth showing
  // zero over. During a turn the message-count trigger is parked (the transcript
  // grows every few seconds and the answer cannot change until the turn lands);
  // the flip back to idle is itself a dependency, so a finished turn refetches.
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    chatApi.threadContext(activeId)
      .then((r) => {
        if (cancelled) return;
        setServer({ used: r.used_tokens, window: r.window_tokens });
        setReadFailed(false);
      })
      .catch(() => {
        // The estimate still renders — a meter that disappears on a failed read
        // says even less than one that lies. It renders MARKED, so the number
        // stops claiming to be the server's.
        if (!cancelled) setReadFailed(true);
      });
    return () => { cancelled = true; };
  }, [activeId, nonce, streaming, streaming ? -1 : messages.length]);

  // Drop a stale count on thread switch rather than showing one chat's usage
  // against another's transcript.
  useEffect(() => { setServer(null); setReadFailed(false); }, [activeId]);

  // THE DRAFT IS DELIBERATELY NOT A DEP OF THIS ONE. estimateContextTokens walks
  // every message AND every segment of the entire log, and `draft` changes on
  // every keystroke, so folding the two together re-scanned the whole thread
  // each time a key was pressed — on any thread without a server count (a new
  // chat, one whose first context read has not landed, or one whose read
  // failed), which is exactly when the meter falls back to the estimate.
  const baseUsed = useMemo(() => estimateContextTokens(messages, ""), [messages]);

  const ctx = useMemo(() => {
    // The draft is a plain char count either way, so it is added on top rather
    // than scanned in. This is how the server branch always worked; the estimate
    // branch now agrees with it, at a rounding cost of at most one token.
    const draftTokens = Math.ceil(draft.length / 4);
    if (server) {
      // Real assembled-context count plus the live (unsent) draft.
      return { used: server.used + draftTokens, win: server.window };
    }
    return { used: baseUsed + draftTokens, win: contextWindowFor(model) };
  }, [server, baseUsed, draft, model]);

  // No reading, or a reading too small to round to a whole percent. See
  // contextReading: neither may render as "0%".
  const reading = contextReading(ctx.used, ctx.win);
  if (!reading) return null;
  const source: ContextSource = server ? "server" : readFailed ? "unavailable" : "estimate";

  // A single accent tone, no threshold colors: this is a reading, not a status.
  // The arc is a dash on a rotated circle so it fills clockwise from 12 o'clock.
  const inner = (
    <>
      <svg className="chat-context-meter__ring" width="18" height="18" viewBox="0 0 18 18" aria-hidden>
        <circle className="chat-context-meter__track" cx="9" cy="9" r={RING_R} />
        <circle
          className="chat-context-meter__arc"
          cx="9" cy="9" r={RING_R}
          style={{ strokeDasharray: `${reading.fraction * RING_C} ${RING_C}` }}
        />
      </svg>
      <span className="chat-context-meter__pct">{contextLabel(reading)}</span>
    </>
  );
  const title = contextTooltip(ctx.used, ctx.win, source);
  // A reading with nothing behind it stays a plain label rather than pretending
  // to be a control: a blank chat has no session to break down.
  if (!onOpen || !activeId) {
    return <div className="chat-context-meter" title={title}>{inner}</div>;
  }
  return (
    <button
      type="button"
      className="chat-context-meter chat-ctx-btn"
      title={`${title}. Click for the breakdown`}
      onClick={onOpen}
    >
      {inner}
    </button>
  );
}
