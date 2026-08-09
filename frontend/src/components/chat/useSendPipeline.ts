import { useCallback, useRef, useState } from "react";
import { chatApi, newClientId, shortHttpError, type ThreadSummary } from "../../lib/chat";
import type { QueuedMessage } from "../../lib/chatSelection";
import type { ChatModeId } from "./chatModes";
import { planSend } from "./sendPlan";
import type { ThreadAction, ThreadState } from "./threadStore";
import { committedMessages } from "./transcriptBlocks";
import type { MessageQueue } from "./useMessageQueue";

// SENDING, as one decision.
//
// This was a four-branch guard ladder in the middle of a 1,360-line component
// (compacting / logBusy && isSlashCommand / streaming && threadId / logBusy)
// plus a 250ms timer in the queue's drain. Every branch came from a real
// incident and each was right on its own; together they asked two different
// questions — "is a turn streaming" and "does a turn own the tail of the log" —
// whose answers diverged for one network round trip on every single turn.
//
// Now there is one plan (sendPlan.planSend) computed from the store's state,
// with an explicit `starting` in place of the timer: a turn has been asked for
// and the POST has not come back yet. Setting it synchronously means there is no
// interval in which the chat is honestly idle but a turn is on its way, which is
// the race the timer was guessing at.
//
// EVERY SEND CARRIES A CLIENT ID, minted here and reused verbatim on retry. It
// is what the optimistic bubble is reconciled against when the committed event
// arrives, and it is what makes a retry after a dropped response land as the
// same message rather than a second one (the log dedupes on it).

export type SendPipeline = {
  /** A turn has been requested and not confirmed yet. */
  starting: boolean;
  /** The running turn IS the CLI's own /compact — the indicator says so. */
  turnIsCompact: boolean;
  /** Send one composed message. THE entry point for the composer. */
  submitText: (raw: string) => void;
  /** Re-send a message that never reached the server, under its own id. */
  retry: (clientId: string) => void;
  /** What the queue calls when it drains. */
  sendBatch: (text: string, parts: QueuedMessage[]) => void;
};

export function useSendPipeline({
  scope, selectedModel, chatModeRef, activeIdRef, adoptThread,
  stateRef, dispatch, sync, queue,
  compacting, compact, clear, setErr, onSend,
}: {
  scope: string;
  selectedModel: string;
  chatModeRef: React.MutableRefObject<ChatModeId>;
  activeIdRef: React.MutableRefObject<string | null>;
  adoptThread: (t: ThreadSummary) => void;
  stateRef: React.MutableRefObject<ThreadState>;
  dispatch: React.Dispatch<ThreadAction>;
  sync: () => Promise<void>;
  queue: MessageQueue;
  compacting: boolean;
  compact: () => Promise<void>;
  clear: () => Promise<void>;
  setErr: (m: string | null) => void;
  /** Housekeeping the chat does on every send: follow the log, drop the note. */
  onSend: () => void;
}): SendPipeline {
  const [starting, setStarting] = useState(false);
  const [turnIsCompact, setTurnIsCompact] = useState(false);
  const startingRef = useRef(false);
  const compactingRef = useRef(compacting);
  compactingRef.current = compacting;

  const setStartingBoth = useCallback((v: boolean) => {
    startingRef.current = v;
    setStarting(v);
  }, []);

  // Start a turn for `text`. The message is already in the store as a pending
  // send under `clientId`; the server records it under the same id, so the
  // optimistic copy is reconciled away by the sync rather than popped by hand.
  const sendTurn = useCallback(async (
    text: string, clientId: string, parts?: QueuedMessage[],
  ) => {
    setStartingBoth(true);
    setTurnIsCompact(text.trim() === "/compact");
    try {
      let id = activeIdRef.current;
      if (!id) {
        // Carry the mode chosen on the blank chat into the thread it becomes,
        // so the very first turn runs in the mode the toolbar was showing.
        const t = await chatApi.createThread(scope, "", chatModeRef.current);
        id = t.id;
        // Put the new thread in the picker and record the selection: a chat you
        // just started used to be unreachable the moment you navigated away.
        adoptThread(t);
      }
      const r = await fetch(`/api/chat/threads/${id}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          client_id: clientId,
          ...(selectedModel ? { model: selectedModel } : {}),
          // `parts` is present only when the queue drained several messages into
          // this one turn. It carries WHEN EACH WAS TYPED and its own id, so the
          // server stores them as the separate messages they were, at their real
          // times, rather than one blob stamped with the moment the queue
          // happened to drain.
          ...(parts && parts.length > 1
            ? { parts: parts.map((m) => ({ text: m.text, sent_at: m.sentAt, client_id: m.clientId })) }
            : {}),
        }),
      });
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      const res = await r.json();
      dispatch({ type: "sendAcked", clientId });
      for (const p of parts || []) dispatch({ type: "sendAcked", clientId: p.clientId });
      // Show the run as in flight in the same commit that stops "starting".
      // The re-attach effect opens the socket; nothing here needs to know how.
      if (res.turn_id) dispatch({ type: "runStarted", runId: res.turn_id });
      void sync();
    } catch (e) {
      setErr(String(e));
      // The turn never started, so the server has no copy. Mark the bubble
      // rather than leaving it looking delivered: a bubble that silently is not
      // real is exactly what "my messages keep disappearing" felt like.
      dispatch({ type: "sendFailed", clientId });
      for (const p of parts || []) dispatch({ type: "sendFailed", clientId: p.clientId });
    } finally {
      setStartingBoth(false);
    }
  }, [scope, selectedModel, chatModeRef, activeIdRef, adoptThread, dispatch, sync, setErr, setStartingBoth]);

  // Hand a message to the run already in flight. It is PERSISTED BEFORE
  // DELIVERY server-side, so `delivered: true` means the chat has it whether or
  // not the running turn took it; `delivered: false` means the endpoint did not
  // take the message at all and it must go out as a turn.
  const injectIntoRun = useCallback(async (
    threadId: string, text: string, clientId: string,
  ) => {
    try {
      const r = await chatApi.injectIntoThread(threadId, text, clientId);
      if (!r.delivered) { void sendTurn(text, clientId); return; }
      dispatch({ type: "sendAcked", clientId });
      // Pull the log immediately: the server recorded the message with the
      // `at_segment` that decides which block it closes, and that is the only
      // place that number exists.
      await sync();
    } catch {
      // The request never landed, so nothing was recorded. Queue it rather than
      // dropping it: the operator hit enter, delivery is owed.
      dispatch({ type: "sendRetracted", clientId });
      queue.push(text, clientId);
    }
  }, [dispatch, sync, sendTurn, queue]);

  const sendBatch = useCallback((text: string, parts: QueuedMessage[]) => {
    // Everything in a drained batch is already the operator's; each part shows
    // as its own pending bubble under the id it will be committed with.
    const ts = new Date().toISOString();
    for (const p of parts) {
      dispatch({ type: "sendStarted", clientId: p.clientId, text: p.text, ts: p.sentAt || ts });
    }
    void sendTurn(text, parts[0]?.clientId || newClientId(), parts.length > 1 ? parts : undefined);
  }, [dispatch, sendTurn]);

  const dispatchSend = useCallback((text: string, clientId: string) => {
    const plan = planSend(text, {
      compacting: compactingRef.current,
      starting: startingRef.current,
      runActive: !!stateRef.current.activeRun,
    });
    if (plan === "queue") {
      queue.push(text, clientId);
      return;
    }
    dispatch({ type: "sendStarted", clientId, text, ts: new Date().toISOString() });
    if (plan === "inject") {
      const threadId = activeIdRef.current;
      // Addressed by CHAT, not by run id. A client only knows about runs IT
      // started; a second tab, another device, or a run adopted after a restart
      // has no id to send, and every one of those used to fall back to queueing
      // and wait out the whole turn.
      if (threadId) { void injectIntoRun(threadId, text, clientId); return; }
    }
    void sendTurn(text, clientId);
  }, [queue, dispatch, stateRef, activeIdRef, injectIntoRun, sendTurn]);

  const submitText = useCallback((raw: string) => {
    const text = raw.trim();
    if (!text) return;
    setErr(null);
    onSend();
    // /compact and /clear are Workshop actions, not messages. Intercepted here
    // as well as in the autocomplete, so typing one and hitting enter folds the
    // transcript instead of sending the literal string to the model. This has to
    // come BEFORE the plan: queued behind a running turn it would be treated as
    // ordinary text and drained into the next turn joined to whatever else was
    // waiting, so it would neither compact nor read as a message.
    if (text === "/compact") { void compact(); return; }
    if (text === "/clear") { void clear(); return; }
    dispatchSend(text, newClientId());
  }, [setErr, onSend, compact, clear, dispatchSend]);

  const retry = useCallback((clientId: string) => {
    const pending = stateRef.current.pending[clientId];
    if (!pending) return;
    setErr(null);
    // THE SAME ID. The log dedupes on it, so if the first attempt did reach the
    // server after all, this re-send appends nothing and reconciles against the
    // event that is already there.
    dispatchSend(pending.text, clientId);
  }, [stateRef, setErr, dispatchSend]);

  return { starting, turnIsCompact, submitText, retry, sendBatch };
}

/** Whether this turn is the first exchange in the chat, which is when the
 *  auto-namer runs and the thread list needs a second look. */
export function wasFirstExchange(state: ThreadState): boolean {
  return committedMessages(state).length <= 2;
}
