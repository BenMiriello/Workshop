import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { chatApi, type ChatMsg } from "../../lib/chat";
import { estimateContextTokens } from "./chatScope";
import { contextWindowFor } from "./models";
import { contextLabel, contextReading, contextTooltip } from "./contextReading";

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
  activeId, streaming, messages, draft, model, nonce, liveTokens, onOpen,
}: {
  activeId: string | null;
  streaming: boolean;
  messages: ChatMsg[];
  draft: string;
  model: string;
  nonce: number;
  /** The in-flight run's own context reading, straight off the turn stream.
   *  The stored thread reading only changes at finalize, so this is the only
   *  number that can move while a turn runs. */
  liveTokens?: number;
  // Open the breakdown. The reading answers "how full"; the panel behind it
  // answers "with what", which is the question that follows it every time.
  onOpen?: () => void;
}) {
  const [server, setServer] = useState<{ used: number; window: number } | null>(null);
  // A FAILED READ USED TO BE TRACKED HERE and marked in the tooltip, so a
  // browser estimate could not pass itself off as the server's count. The
  // tooltip is two numbers and a slash now (operator, 2026-08-14), which leaves
  // that distinction nowhere to be said, so the state is gone rather than kept
  // as a value nothing reads. If it is wanted again it belongs on the meter
  // itself, not in prose.
  // The latest live reading, HELD past the run's retirement rather than read
  // straight off the prop. The run object disappears the moment the turn is
  // retired, a beat before the post-turn refetch resolves; without the hold the
  // meter regressed to the previous turn's number for exactly that beat, then
  // jumped forward again.
  const [live, setLive] = useState<number | null>(null);
  useEffect(() => {
    if (typeof liveTokens === "number" && liveTokens > 0) setLive(liveTokens);
  }, [liveTokens]);

  // A live turn must NOT block the reading. It used to: the fetch returned
  // early whenever `streaming` was true, so a meter that first mounted during a
  // turn never got a number at all, and rendered a confident "0%" for the whole
  // turn. The count the server holds mid-turn is the previous turn's, which is
  // stale by one turn and correct to the character; nothing is worth showing
  // zero over. During a turn the message-count trigger is parked (the transcript
  // grows every few seconds and the answer cannot change until the turn lands);
  // the flip back to idle is itself a dependency, so a finished turn refetches.
  //
  // THE QUERY KEY IS THE TRIGGER LIST. It carries exactly what the effect this
  // replaces had in its dependency array: the thread, the compaction nonce,
  // the streaming flip (both edges of it), and the message count parked at
  // `-1` while a turn runs. Nothing else may fire this read.
  const ctxQuery = useQuery({
    queryKey: ["chat", "context", activeId, nonce, streaming, streaming ? -1 : messages.length],
    queryFn: () => chatApi.threadContext(activeId!),
    enabled: !!activeId,
    // A RECURRING KEY MUST NOT BE SERVED FROM CACHE. The parked `-1` key is the
    // same key on every turn, so a cache hit at the start of turn two would put
    // the reading from the start of turn ONE back on the meter — older than the
    // post-turn read that has landed since, i.e. a number that walks backwards.
    // `gcTime: 0` retires the entry as soon as the key moves on, and
    // `staleTime: 0` means what does come back is read, never reused.
    gcTime: 0,
    staleTime: 0,
    // The effect this replaces had no focus and no reconnect trigger. The app
    // default has them (it is preserving `usePoll`'s catch-up read), and here
    // they would be a new read the meter never made.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  // THE HOLD STAYS LOCAL, and that is the point of this pair of effects. The
  // cache entry is retired on every turn boundary by the key above, while
  // `server` has to survive that, and has to survive a FAILED read too: a
  // meter that drops back to the browser estimate because one read 502'd says
  // less than one that is a turn stale. So the query owns the fetch and this
  // owns what stays on screen between fetches.
  useEffect(() => {
    const r = ctxQuery.data;
    if (!r) return;
    setServer({ used: r.used_tokens, window: r.window_tokens });
  }, [ctxQuery.data]);

  // A read fired OUTSIDE a turn is the post-turn (or idle) read: the stored
  // number is now at least as fresh as anything the stream said, so the held
  // live reading retires. A read fired at turn START must not clear it; its
  // answer predates the run by definition. `streaming` is part of the key, so
  // a settled read is always the one this value fired — a flip retires that
  // entry and starts another.
  //
  // Failure retires it on the same terms: a post-turn read that FAILED must not
  // leave a dead turn's number on screen labeled as a server fact.
  const settled = ctxQuery.fetchStatus === "idle" && (ctxQuery.isSuccess || ctxQuery.isError);
  useEffect(() => {
    if (settled && !streaming) setLive(null);
  }, [settled, streaming, ctxQuery.dataUpdatedAt, ctxQuery.errorUpdatedAt]);

  // Drop a stale count on thread switch rather than showing one chat's usage
  // against another's transcript.
  useEffect(() => { setServer(null); setLive(null); }, [activeId]);

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
    if (live != null) {
      // The run's own report beats the stored reading, which cannot change
      // until the run finalizes.
      return { used: live + draftTokens, win: server ? server.window : contextWindowFor(model) };
    }
    if (server) {
      // Real assembled-context count plus the live (unsent) draft.
      return { used: server.used + draftTokens, win: server.window };
    }
    return { used: baseUsed + draftTokens, win: contextWindowFor(model) };
  }, [live, server, baseUsed, draft, model]);

  // ALWAYS A READING, and the meter always renders. See contextReading: it used
  // to return null at zero and take the whole control off the composer with it.
  const reading = contextReading(ctx.used, ctx.win);

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
  const title = contextTooltip(ctx.used, ctx.win);
  // A reading with nothing behind it stays a plain label rather than pretending
  // to be a control: a blank chat has no session to break down.
  if (!onOpen || !activeId) {
    return <div className="chat-context-meter" title={title}>{inner}</div>;
  }
  return (
    <button
      type="button"
      className="chat-context-meter chat-ctx-btn"
      title={title}
      onClick={onOpen}
    >
      {inner}
    </button>
  );
}
