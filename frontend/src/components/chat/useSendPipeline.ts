import { useCallback, useRef, useState } from "react";
import { chatApi, newClientId, shortHttpError, type ThreadSummary } from "../../lib/chat";
import type { QueuedMessage } from "../../lib/chatSelection";
import type { ChatModeId } from "./chatModes";
import { isTransientSendFailure, sendFailureText } from "./sendFailure";
import { planSend } from "./sendPlan";
import { retiredCommand, typedCommand } from "./typedCommands";
import type { ThreadAction, ThreadState } from "./threadStore";
import { committedMessages } from "./transcriptBlocks";
import type { MessageQueue, SendOutcome } from "./useMessageQueue";
import type { ChatProblem } from "./chatProblem";

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

// How long to wait before the one silent retry of a transient failure. Long
// enough to clear a backend restart (the unit is back inside a second) and short
// enough that the operator reads it as the send being slow, not as a send that
// failed. See isTransientSendFailure.
const RETRY_DELAY_MS = 900;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export type SendPipeline = {
  /** A turn has been requested and not confirmed yet. */
  starting: boolean;
  /** The running turn IS the CLI's own /compact — the indicator says so. */
  turnIsCompact: boolean;
  /** Send one composed message. THE entry point for the composer. */
  submitText: (raw: string) => void;
  /** Re-send a message that never reached the server, under its own id. */
  retry: (clientId: string) => void;
  /** What the queue calls when it drains. Resolves ok once the server has the
   *  batch; `fromQueue` says the caller will take it back on a refusal, so the
   *  bubbles are retracted rather than left on screen as N failed sends. */
  sendBatch: (
    text: string, parts: QueuedMessage[], opts?: { fromQueue?: boolean },
  ) => Promise<SendOutcome>;
};

export function useSendPipeline({
  scope, selectedModel, selectedEffort, selectedPermissionMode,
  chatModeRef, activeIdRef, adoptThread,
  stateRef, dispatch, sync, queue,
  compacting, compact, clear, setErr, onSend, onNewChat,
}: {
  scope: string;
  selectedModel: string;
  selectedEffort: string;
  selectedPermissionMode: string;
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
  setErr: (problem: ChatProblem | null) => void;
  /** Housekeeping the chat does on every send: follow the log, drop the note. */
  onSend: () => void;
  /** What `/new` does: open the new-chat dialog, not start one silently. */
  onNewChat: () => void;
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
    opts?: { fromQueue?: boolean },
  ): Promise<SendOutcome> => {
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
      const post = () => fetch(`/api/chat/threads/${id}/turns`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: text,
          client_id: clientId,
          ...(selectedModel ? { model: selectedModel } : {}),
          // Sent ONLY when the operator has actually picked a level. Omitting it
          // is what makes an untouched chat spawn today's argv exactly, and
          // effort is part of the rendered prompt, so emitting a level nobody
          // chose would cost every such chat its prompt-cache prefix.
          ...(selectedEffort ? { effort: selectedEffort } : {}),
          // Same rule as effort: sent only when chosen, so a chat nobody has
          // touched keeps running exactly as it did before the picker existed.
          ...(selectedPermissionMode ? { permission_mode: selectedPermissionMode } : {}),
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
      const attempt = async () => {
        const r = await post();
        if (!r.ok) {
          throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        }
        return r.json();
      };
      // ONE SILENT RETRY FOR A FAULT THAT IS ALREADY OVER.
      //
      // The deployment produces two of these on its own — the edge's pooled
      // connection to the backend expiring on the same 5s clock the backend
      // closes it on, and the ~1s window of a backend restart — and both are
      // finished by the time the operator could read about them. Retrying under
      // THE SAME CLIENT ID is not a second message: the log dedupes on it
      // (chat_log.find_message_by_client_id), which is the property the retry
      // button already relies on. See isTransientSendFailure for what does not
      // qualify.
      let res;
      try {
        res = await attempt();
      } catch (first) {
        if (!isTransientSendFailure(first)) throw first;
        await sleep(RETRY_DELAY_MS);
        res = await attempt();
      }
      dispatch({ type: "sendAcked", clientId });
      for (const p of parts || []) dispatch({ type: "sendAcked", clientId: p.clientId });
      // Show the run as in flight in the same commit that stops "starting".
      // The re-attach effect opens the socket; nothing here needs to know how.
      if (res.turn_id) dispatch({ type: "runStarted", runId: res.turn_id });
      void sync();
      return { ok: true };
    } catch (e) {
      const ids = parts?.length ? parts.map((p) => p.clientId) : [clientId];
      // The turn never started, so the server has no copy. Mark the bubble
      // rather than leaving it looking delivered: a bubble that silently is not
      // real is exactly what "my messages keep disappearing" felt like.
      //
      // ...unless this came from the QUEUE, which still holds these messages and
      // is about to put them back. Marking them failed there drew them twice —
      // once queued, once as a dead bubble — and each dead bubble retried as its
      // own turn, so a refused batch of four came back as four turns instead of
      // the one turn it was sent as (invariant 4).
      //
      // THE FAILURE BELONGS TO THIS MESSAGE, so it renders under it and nowhere
      // else. It used to also go to `setErr`, which is one slot above the
      // composer that the next error overwrites and the next send clears — so
      // the explanation for a bubble marked "Not sent" outlived the bubble by
      // about one keystroke, and while it lasted it read "TypeError: Failed to
      // fetch". See sendFailure.ts and frontend/CLAUDE.md's log-placement rule.
      const reason = sendFailureText(e);
      for (const id of ids) {
        dispatch(
          opts?.fromQueue
            ? { type: "sendRetracted", clientId: id }
            : { type: "sendFailed", clientId: id, error: reason },
        );
      }
      // ...AND IT SURVIVES THE TAB. `pending` is reducer state, the composer was
      // cleared synchronously before the POST, and `reset` drops pending on a
      // thread switch: a 500 left a bubble with a Retry that a reload erased
      // outright, text and all. The durable queue holds it, marked and held, so
      // Retry is still there afterwards. A batch from the queue is skipped
      // because the queue already holds those and is putting them back itself.
      if (!opts?.fromQueue) queue.pushFailed(text, clientId, undefined, reason);
      return { ok: false, error: reason };
    } finally {
      setStartingBoth(false);
    }
  }, [scope, selectedModel, selectedEffort, selectedPermissionMode, chatModeRef, activeIdRef, adoptThread, dispatch, sync, queue, setErr, setStartingBoth]);

  // Hand a message to the run already in flight. It is PERSISTED BEFORE
  // DELIVERY server-side, so `delivered: true` means the chat has it whether or
  // not the running turn took it; `delivered: false` means the endpoint did not
  // take the message at all and it must go out as a turn.
  const injectIntoRun = useCallback(async (
    threadId: string, text: string, clientId: string,
  ) => {
    try {
      const r = await chatApi.injectIntoThread(threadId, text, clientId);
      // The chat is running a command, which reads nothing. The server did NOT
      // record this message, so there is nothing to reconcile and nothing to
      // re-send now: it goes back on the queue and leaves as its own turn when
      // the command finishes. Sending it as a turn here would only be refused
      // again, by the run the command still holds.
      if (r.deferred) {
        dispatch({ type: "sendRetracted", clientId });
        queue.push(text, clientId);
        return;
      }
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

  const sendBatch = useCallback((
    text: string, parts: QueuedMessage[], opts?: { fromQueue?: boolean },
  ) => {
    // Everything in a drained batch is already the operator's; each part shows
    // as its own pending bubble under the id it will be committed with.
    const ts = new Date().toISOString();
    for (const p of parts) {
      dispatch({ type: "sendStarted", clientId: p.clientId, text: p.text, ts: p.sentAt || ts });
    }
    // `parts` rides in the POST only when there are several: the server needs it
    // to record them as the separate messages they were. A batch of one is
    // already fully described by its client id.
    return sendTurn(
      text, parts[0]?.clientId || newClientId(),
      parts.length > 1 ? parts : undefined, opts,
    );
  }, [dispatch, sendTurn]);

  const dispatchSend = useCallback((text: string, clientId: string) => {
    const plan = planSend(text, {
      compacting: compactingRef.current,
      starting: startingRef.current,
      runActive: !!stateRef.current.activeRun,
      runAcceptsInput: stateRef.current.activeRun?.acceptsInput,
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
    // The three that still work. Typed only — nothing suggests them, and there
    // is no picker any more (see typedCommands).
    const cmd = typedCommand(text);
    if (cmd === "compact") { void compact(); return; }
    if (cmd === "clear") { void clear(); return; }
    if (cmd === "new") { onNewChat(); return; }
    // A command that used to exist here is answered LOCALLY, for free, with
    // where its job went. Sending it spawned a process and spent a full prompt
    // so the CLI could reply that it does not work.
    const retired = retiredCommand(text);
    // Not a failure of anything, so it names no endpoint: the command was
    // answered here, locally, and the sentence is the whole answer.
    if (retired) { setErr({ text: retired, endpoint: null }); return; }
    dispatchSend(text, newClientId());
  }, [setErr, onSend, compact, clear, dispatchSend]);

  const retry = useCallback((clientId: string) => {
    const pending = stateRef.current.pending[clientId];
    if (!pending) return;
    setErr(null);
    // Take the durable copy back first. It IS this send — written so the text
    // survives a reload — and this retry replaces it; leaving it there would
    // draw the message a second time the moment the optimistic bubble
    // reconciles away, and a failure would then queue a third under the same id.
    queue.drop(clientId);
    // THE SAME ID. The log dedupes on it, so if the first attempt did reach the
    // server after all, this re-send appends nothing and reconciles against the
    // event that is already there.
    dispatchSend(pending.text, clientId);
  }, [stateRef, setErr, dispatchSend, queue]);

  return { starting, turnIsCompact, submitText, retry, sendBatch };
}

/** Whether this turn is the first exchange in the chat, which is when the
 *  auto-namer runs and the thread list needs a second look. */
export function wasFirstExchange(state: ThreadState): boolean {
  return committedMessages(state).length <= 2;
}
