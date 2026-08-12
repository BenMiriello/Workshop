import { useCallback, useRef, useState } from "react";
import { chatApi, newClientId, shortHttpError, type ThreadSummary } from "../../lib/chat";
import type { AttachmentRef } from "./attachments";
import type { ChatModeId } from "./chatModes";
import { isTransientSendFailure, sendFailureText } from "./sendFailure";
import { planSend } from "./sendPlan";
import { retiredCommand, typedCommand } from "./typedCommands";
import type { ThreadAction, ThreadState } from "./threadStore";
import { committedMessages } from "./transcriptBlocks";
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
  /** Send one composed message. THE entry point for the composer.
   *  `attachments` are already uploaded; only their ids travel. */
  submitText: (raw: string, attachments?: AttachmentRef[]) => void;
  /** Re-send a message that never reached the server, under its own id. */
  retry: (clientId: string) => void;
};

/** How a send ended. `ok` means THE SERVER HAS IT — running now, handed to the
 *  turn in flight, or on the thread's queue. Anything else is `ok: false` with
 *  the reason, and the text stays in this browser's outbox. */
export type SendOutcome = { ok: true } | { ok: false; error: string };

export function useSendPipeline({
  scope, selectedModel, selectedEffort, selectedPermissionMode,
  chatModeRef, activeIdRef, adoptThread,
  stateRef, dispatch, sync, draftKey,
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
  /** The composer key this chat is typing under, so the server can clear the
   *  right draft when it accepts a message. See services/draft_store. */
  draftKey: string;
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

  // Sends made while this chat was being CREATED. See planSend's `awaitThread`:
  // the client serializes exactly one thing, the request that mints the thread,
  // because two of those make two chats. They are already in `pending` (so they
  // are on screen and on disk); this is only the list of what still has to be
  // handed over once there is a thread to hand it to.
  const awaitingRef = useRef<{
    clientId: string; text: string; ts: string; attachments?: AttachmentRef[];
  }[]>([]);
  // Which composer the current send came out of, so the server can clear it.
  // A ref because the value is read inside the request builder, and the key
  // changes the moment a blank chat becomes a thread.
  const draftKeyRef = useRef<string>(draftKey);
  draftKeyRef.current = draftKey;
  // `sendTurn` flushes what was typed while the thread was being created, and
  // `injectIntoRun` is declared after it. One of the two has to be reached
  // through a ref; this is the one that does not change identity per send.
  const injectIntoRunRef = useRef<(
    threadId: string, text: string, clientId: string, sentAt: string,
    attachments?: AttachmentRef[],
  ) => Promise<void>>(async () => {});

  // Start a turn for `text`. The message is already in the store as a pending
  // send under `clientId`; the server records it under the same id, so the
  // optimistic copy is reconciled away by the sync rather than popped by hand.
  const sendTurn = useCallback(async (
    text: string, clientId: string, sentAt?: string, attachments?: AttachmentRef[],
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
          // WHEN THE OPERATOR HIT ENTER. Only this side was present at that
          // moment, and it matters whenever the message does not run
          // immediately: the server queues it under its own send time rather
          // than dating it at whatever later instant it went out.
          ...(sentAt ? { sent_at: sentAt } : {}),
          // WHICH COMPOSER THIS CAME OUT OF, so the server clears exactly that
          // draft on acceptance. The client cannot do it reliably: the first
          // send of a blank chat CHANGES the composer's key, so a local clear
          // lands on the new one and the blank-chat slot keeps the text — which
          // is why an already-sent message turned up in the next new chat.
          ...(draftKeyRef.current ? { draft_key: draftKeyRef.current } : {}),
          // Ids only. The bytes went up on drop, so this stays a small JSON body
          // however large the screenshot was.
          ...(attachments?.length ? { attachments } : {}),
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
      // QUEUED IS ACCEPTED. The thread was busy with something that reads
      // nothing, so the server took the message onto the thread's own queue and
      // will send it when that run ends. This browser is no longer responsible
      // for it: the optimistic copy goes, and the queue that comes back on the
      // sync is what renders from here on — on every device, not just this one.
      if (res.queued) {
        dispatch({ type: "sendRetracted", clientId });
        void sync();
        return { ok: true };
      }
      // AN OLDER BACKEND, which answers `deferred: true` and stores NOTHING —
      // it expected the browser to hold the message and send it later, which is
      // the arrangement that lost two of them. This client does not do that, so
      // treating a deferral as an acceptance would drop the message outright.
      // It stays in the outbox, marked, with a Retry, which is the honest
      // outcome: the server does not have it.
      if (res.deferred) {
        const why = "The chat is busy with a command this server cannot queue. Retry when it finishes.";
        dispatch({ type: "sendFailed", clientId, error: why });
        return { ok: false, error: why };
      }
      dispatch({ type: "sendAcked", clientId });
      // Show the run as in flight in the same commit that stops "starting".
      // The re-attach effect opens the socket; nothing here needs to know how.
      if (res.turn_id) dispatch({ type: "runStarted", runId: res.turn_id });
      void sync();
      return { ok: true };
    } catch (e) {
      // The turn never started, so the server has no copy. Mark the bubble
      // rather than leaving it looking delivered: a bubble that silently is not
      // real is exactly what "my messages keep disappearing" felt like.
      //
      // THE FAILURE BELONGS TO THIS MESSAGE, so it renders under it and nowhere
      // else. It used to also go to `setErr`, which is one slot above the
      // composer that the next error overwrites and the next send clears — so
      // the explanation for a bubble marked "Not sent" outlived the bubble by
      // about one keystroke, and while it lasted it read "TypeError: Failed to
      // fetch". See sendFailure.ts and frontend/CLAUDE.md's log-placement rule.
      //
      // ...AND IT SURVIVES THE TAB, because `pending` is mirrored to storage as
      // it changes (useSendOutbox). This is the ONLY state the browser still
      // owns on its own, and it owns it precisely because the server never
      // heard about this send.
      const reason = sendFailureText(e);
      dispatch({ type: "sendFailed", clientId, error: reason });
      return { ok: false, error: reason };
    } finally {
      setStartingBoth(false);
      // Anything typed while this chat was being created now has a thread to go
      // to. Sent one at a time, through the ordinary path: if the first turn is
      // already running, the server takes them onto its queue, which is exactly
      // where they belong and is no longer this browser's problem.
      const waiting = awaitingRef.current;
      if (waiting.length && activeIdRef.current) {
        awaitingRef.current = [];
        for (const w of waiting) {
          void injectIntoRunRef.current(
            activeIdRef.current, w.text, w.clientId, w.ts, w.attachments);
        }
      }
    }
  }, [scope, selectedModel, selectedEffort, selectedPermissionMode, chatModeRef, activeIdRef, adoptThread, dispatch, sync, setErr, setStartingBoth]);

  // Hand a message to the run already in flight. It is PERSISTED BEFORE
  // DELIVERY server-side, so `delivered: true` means the chat has it whether or
  // not the running turn took it; `delivered: false` means the endpoint did not
  // take the message at all and it must go out as a turn.
  const injectIntoRun = useCallback(async (
    threadId: string, text: string, clientId: string, sentAt: string,
    attachments?: AttachmentRef[],
  ) => {
    try {
      const r = await chatApi.injectIntoThread(threadId, text, clientId, {
        sentAt, attachments, draftKey: draftKeyRef.current,
      });
      // THE SERVER TOOK IT AND IS HOLDING IT. The run in flight reads nothing (a
      // slash command, a voice turn), so it goes out as the next turn — sent by
      // the server, from the thread's own queue, whether or not this browser is
      // still here. It is not this tab's problem any more, so the optimistic
      // copy goes and the queue the sync brings back is what renders.
      if (r.queued) {
        dispatch({ type: "sendRetracted", clientId });
        await sync();
        return;
      }
      // See sendTurn: an older backend deferring means it took nothing.
      if (r.deferred) {
        dispatch({
          type: "sendFailed", clientId,
          error: "The chat is busy with a command this server cannot queue. Retry when it finishes.",
        });
        return;
      }
      if (!r.delivered) { void sendTurn(text, clientId, sentAt, attachments); return; }
      dispatch({ type: "sendAcked", clientId });
      // Pull the log immediately: the server recorded the message with the
      // `at_segment` that decides which block it closes, and that is the only
      // place that number exists.
      await sync();
    } catch (e) {
      // The request never landed, so the server has no copy and this browser is
      // the only place the text exists. It stays in `pending`, marked, which is
      // both the bubble the operator sees and the durable outbox entry.
      dispatch({ type: "sendFailed", clientId, error: sendFailureText(e) });
    }
  }, [dispatch, sync, sendTurn]);
  injectIntoRunRef.current = injectIntoRun;

  const dispatchSend = useCallback((
    text: string, clientId: string, attachments?: AttachmentRef[],
  ) => {
    // THE BUBBLE EXISTS BEFORE THE REQUEST DOES, and it is durable from this
    // moment (useSendOutbox mirrors `pending` to storage). The composer has
    // already been cleared, so anything that can happen between here and the
    // server's answer — a crash, a closed lid, a dead network — must not be
    // able to take the text with it.
    const ts = new Date().toISOString();
    dispatch({
      type: "sendStarted", clientId, text, ts,
      ...(attachments?.length ? { attachments } : {}),
    });
    const plan = planSend(text, {
      compacting: compactingRef.current,
      starting: startingRef.current,
      runActive: !!stateRef.current.activeRun,
      hasThread: !!activeIdRef.current,
    });
    if (plan === "awaitThread") {
      // This chat is being created by a send that has not come back yet. A
      // second POST would create a second thread, so this one waits for the id
      // — the only thing the client still holds, because it is the only thing
      // the server cannot decide. It is in `pending`, so it is on disk, and
      // the flush below sends it the moment the thread exists.
      awaitingRef.current.push({ clientId, text, ts, attachments });
      return;
    }
    if (plan === "inject") {
      const threadId = activeIdRef.current;
      // Addressed by CHAT, not by run id. A client only knows about runs IT
      // started; a second tab, another device, or a run adopted after a restart
      // has no id to send, and every one of those used to fall back to queueing
      // and wait out the whole turn.
      if (threadId) { void injectIntoRun(threadId, text, clientId, ts, attachments); return; }
    }
    void sendTurn(text, clientId, ts, attachments);
  }, [dispatch, stateRef, activeIdRef, injectIntoRun, sendTurn]);

  const submitText = useCallback((raw: string, attachments?: AttachmentRef[]) => {
    const text = raw.trim();
    // Files with nothing typed is a complete message; the server stands the
    // filenames in for the text. Only a send with NEITHER is a no-op.
    if (!text && !attachments?.length) return;
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
    dispatchSend(text, newClientId(), attachments);
  }, [setErr, onSend, compact, clear, dispatchSend]);

  const retry = useCallback((clientId: string) => {
    const pending = stateRef.current.pending[clientId];
    if (!pending) return;
    setErr(null);
    // THE SAME ID. The log dedupes on it, so if the first attempt did reach the
    // server after all, this re-send appends nothing and reconciles against the
    // event that is already there. There is nothing to take back first: the
    // entry in storage IS this pending send (useSendOutbox mirrors the map), so
    // re-dispatching under the same id updates one record rather than making a
    // second one.
    dispatchSend(pending.text, clientId, pending.attachments);
  }, [stateRef, setErr, dispatchSend]);

  return { starting, turnIsCompact, submitText, retry };
}

/** Whether this turn is the first exchange in the chat, which is when the
 *  auto-namer runs and the thread list needs a second look. */
export function wasFirstExchange(state: ThreadState): boolean {
  return committedMessages(state).length <= 2;
}
