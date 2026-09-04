import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { chatApi, type ChatMsg } from "../../lib/chat";
import { queryKeys } from "../../lib/queryKeys";
import { estimateContextTokens } from "./chatScope";
import { contextWindowFor } from "./models";
import { contextTooltip } from "./contextReading";
import { ContextRing } from "./ContextRing";

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
  // The server's count is NOT held here. It is the query's data -- see below.
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
  // THE KEY IS THE THREAD. IT USED TO BE THE TRIGGER LIST, and that is the
  // difference between using a cache and using a cache's key as a dependency
  // array. The key carried the nonce, the streaming flag and the message count,
  // so it changed on every message: a new cache entry per message, each one
  // retired immediately (`gcTime: 0`) because a recurring key would otherwise
  // serve turn one's reading at the start of turn two -- a number that walks
  // backwards. Every one of those consequences came from the key churning.
  //
  // With the thread as the key there is one entry per conversation, the value
  // in it is always the newest read, and "walks backwards" is unrepresentable.
  // The triggers become what they always were, an ordinary dependency array, on
  // the effect just below.
  //
  // What this REMOVES is the `server` useState and the effect that copied the
  // response into it. That copy existed because the entry was retired on every
  // turn boundary while the number on screen had to survive one; a stable entry
  // survives on its own, including across a FAILED refetch, which is the other
  // thing the copy was for (react-query holds the last good data on error).
  const ctxQuery = useQuery({
    queryKey: queryKeys.chat.context(activeId),
    queryFn: () => chatApi.threadContext(activeId!),
    // NOTHING FIRES THIS READ EXCEPT THE EFFECT BELOW. The triggers are not the
    // key any more, so the query must not fetch on its own or mounting would be
    // one read and the trigger effect another.
    enabled: false,
    // The effect this replaces had no focus and no reconnect trigger. The app
    // default has them (it is preserving `usePoll`'s catch-up read), and here
    // they would be a new read the meter never made.
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    // AND IT DID NOT RETRY. The effect made exactly one request and swallowed
    // the failure. Leaving the app's transient-retry default on would not only
    // add requests this read never made, it would hold `settled` below across
    // the backoff — so the live reading would clear seconds after a failure
    // instead of on it. The meter falls back to the browser estimate, which is
    // what it already did.
    retry: false,
  });

  // THE TRIGGER LIST, as a dependency array, which is what it always was: the
  // thread, the compaction nonce, the streaming flip (both edges of it), and
  // the message count parked at `-1` while a turn runs -- the transcript grows
  // every few seconds during a turn, and the SIZE of it is not what this reads.
  // Nothing else may fire this read except the interval below.
  const trigger = streaming ? -1 : messages.length;
  const refetchCtx = ctxQuery.refetch;
  useEffect(() => {
    if (activeId) void refetchCtx();
  }, [activeId, nonce, streaming, trigger, refetchCtx]);

  // AND WHILE A TURN RUNS, ON A TIMER.
  //
  // Parking the read for the length of a turn rested on "the answer cannot
  // change until it lands", which was true of the server and is not any more:
  // GET /context now prefers the running turn's own reported prompt size over
  // the last committed one. The stream already carries that number as a
  // `context` event, but only to a tab attached at the instant it is pushed, so
  // a reload, a second tab, or a reconnect after a backend restart saw a meter
  // frozen at whatever the previous turn ended on.
  //
  // Measured 2026-09-03: 2% on screen for a 90-minute turn that finished at
  // 24%. A turn here routinely runs for an hour, so "until it lands" is not a
  // short wait, and a confidently wrong number is worse than a coarse one.
  //
  // 20s because this is a progress reading, not a control: it costs one small
  // request that answers from the live snapshot without assembling a prompt,
  // and it is well under the rate at which the number visibly moves.
  useEffect(() => {
    if (!activeId || !streaming) return;
    const id = window.setInterval(() => { void refetchCtx(); }, 20_000);
    return () => window.clearInterval(id);
  }, [activeId, streaming, refetchCtx]);

  // The reading on screen IS the cache entry. No copy, so there is nothing to
  // keep in step with it.
  const r = ctxQuery.data;
  const server = useMemo(
    () => (r ? { used: r.used_tokens, window: r.window_tokens } : null),
    [r],
  );

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

  // Drop the held LIVE reading on a thread switch: it came off the previous
  // chat's stream. The server count needs no such reset any more -- the key is
  // the thread, so what a switch reveals is that thread's own last reading (and
  // the effect above refetches it immediately), never another chat's.
  useEffect(() => { setLive(null); }, [activeId]);

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
  // The drawing itself is ContextRing, shared with the dashboard.
  const inner = <ContextRing used={ctx.used} win={ctx.win} />;
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
