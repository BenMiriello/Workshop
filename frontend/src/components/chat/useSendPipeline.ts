import { useCallback, useRef, useState } from "react";
import { HttpError } from "@workshop/ui";
import {
  chatApi, newClientId, shortHttpError, type SendRule, type Thread, type ThreadSummary,
} from "../../lib/chat";
import { SEND_TIMEOUT_MS, withDeadline } from "../../lib/requestTimeout";
import type { AttachmentRef } from "./attachments";
import type { ChatModeId } from "./chatModes";
import { isTransientSendFailure, sendFailureText } from "./sendFailure";
import { planSend } from "./sendPlan";
import { LANDED, sendWithRetry, syncUntilRead } from "./sendRetry";
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

// HOW LONG A RESTART IS WAITED OUT, AND IT IS NOT THIS FILE'S NUMBER.
//
// It was: one retry, 900ms, on the reasoning that a restart is "back inside a
// second". It is not. `TRANSIENT_BACKOFF_MS` in the kit is the measured window
// -- three waits, 4.1s in total -- and every other read in the app already
// rides a restart out on it (packages/ui/httpError.ts says why the schedule is
// exported rather than being a private default). The SEND, the one request the
// operator cannot afford to lose, was the one thing waiting a fifth as long,
// and it is what produced "Not sent: the server is briefly unavailable. Press
// Retry in a moment." on 2026-09-04 -- a message the operator had to re-send by
// hand for an outage that was over before he finished reading the sentence.
//
// A restated number is also how a poll and a send came to disagree about the
// same outage. There is one schedule; sendRetry.ts reads it.

export type SendPipeline = {
  /** A turn has been requested and not confirmed yet. */
  starting: boolean;
  /** Send one composed message. THE entry point for the composer.
   *  `attachments` are already uploaded; only their ids travel. `keepDraft`
   *  is for a message that did not come out of the box (a page's attach and
   *  send): the box's saved draft is not cleared by it. */
  submitText: (raw: string, attachments?: AttachmentRef[],
               opts?: { keepDraft?: boolean; when?: SendRule }) => SubmittedSend | null;
  /** Re-send a message that never reached the server, under its own id.
   *  Settles when that attempt has (sent, queued, or failed again). */
  retry: (clientId: string) => Promise<void>;
  /** Give up on a still-local send (sending or failed) and drop it from view. */
  cancel: (clientId: string) => void;
};

/** How a send ended. `ok` means THE SERVER HAS IT — running now, handed to the
 *  turn in flight, or on the thread's queue. Anything else is `ok: false` with
 *  the reason, and the text stays in this browser's outbox. */
export type SendOutcome = { ok: true } | { ok: false; error: string };

/** A message handed to the pipeline: its id, and how its send ended once it
 *  has (a cancel spoken over a send still in flight waits on `done`). Null
 *  from `submitText` when nothing was sent as a message (an empty box, a typed
 *  command). */
export type SubmittedSend = { clientId: string; done: Promise<SendOutcome> };

export function useSendPipeline({
  scope, selectedModel, selectedEffort, selectedPermissionMode,
  chatModeRef, activeIdRef, adoptThread, scheduleAutoNameRefetch,
  stateRef, dispatch, sync, draftKey,
  compacting, compact, clear, setErr, onSend, onNewChat, beforeFirstTurn,
}: {
  scope: string;
  selectedModel: string;
  selectedEffort: string;
  selectedPermissionMode: string;
  chatModeRef: React.MutableRefObject<ChatModeId>;
  activeIdRef: React.MutableRefObject<string | null>;
  adoptThread: (t: ThreadSummary | Thread) => void;
  /** Start the picker's backed-off look for a landed auto-name. Called at
   *  SEND time for a brand-new thread's first message, not only at turn end
   *  (see the call site in sendTurn for why). */
  scheduleAutoNameRefetch: (wasFirstExchange: boolean) => void;
  stateRef: React.MutableRefObject<ThreadState>;
  dispatch: React.Dispatch<ThreadAction>;
  /** Read the log since the cursor; false when the read failed. */
  sync: () => Promise<boolean>;
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
  /** Awaited after a send creates the conversation and before its first turn. */
  beforeFirstTurn?: (threadId: string) => Promise<void>;
}): SendPipeline {
  const [starting, setStarting] = useState(false);
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
    settle: (o: SendOutcome) => void;
  }[]>([]);
  // Which composer the current send came out of, so the server can clear it.
  // A ref because the value is read inside the request builder, and the key
  // changes the moment a blank chat becomes a thread.
  const draftKeyRef = useRef<string>(draftKey);
  draftKeyRef.current = draftKey;
  // Sends that leave the draft alone, by client id, so a retry under the same
  // id does too.
  const keepDraftRef = useRef(new Set<string>());
  // THE KEY IS FIXED WHEN THE OPERATOR HITS ENTER. A retry builds its request
  // body after the first reply has made this chat a thread, by which time the
  // live key is `<scope>::<id>`; the server would clear that and leave the blank
  // chat's draft holding the sent text.
  const sentFromKeyRef = useRef<Map<string, string>>(new Map());
  const draftKeyFor = (clientId: string) =>
    (keepDraftRef.current.has(clientId)
      ? undefined
      : sentFromKeyRef.current.get(clientId) ?? draftKeyRef.current);
  // Sends that go out under a rule (Send later), by client id, so a retry
  // carries the same rule.
  const whenRef = useRef(new Map<string, SendRule>());
  // `sendTurn` flushes what was typed while the thread was being created, and
  // `injectIntoRun` is declared after it. One of the two has to be reached
  // through a ref; this is the one that does not change identity per send.
  const injectIntoRunRef = useRef<(
    threadId: string, text: string, clientId: string, sentAt: string,
    attachments?: AttachmentRef[],
  ) => Promise<SendOutcome>>(async () => ({ ok: true }));

  // DID A SEND THAT TIMED OUT LAND ANYWAY? Asked of the log and the queue, by
  // client id, before anything is posted again. See sendRetry.ts for why a
  // re-post is not free once a turn is running.
  const landedAlready = useCallback(async (threadId: string, clientId: string) => {
    const cur = stateRef.current;
    const after = cur.threadId === threadId ? cur.cursor : 0;
    const page = await chatApi.threadEvents(threadId, after, after === 0 ? { tail: 50 } : undefined);
    return (page.events || []).some((ev) => ev.kind === "message" && ev.client_id === clientId)
      || (page.queued || []).some((q) => q.client_id === clientId);
  }, [stateRef]);

  // Start a turn for `text`. The message is already in the store as a pending
  // send under `clientId`; the server records it under the same id, so the
  // optimistic copy is reconciled away by the sync rather than popped by hand.
  const sendTurn = useCallback(async (
    text: string, clientId: string, sentAt?: string, attachments?: AttachmentRef[],
  ): Promise<SendOutcome> => {
    setStartingBoth(true);
    try {
      let id = activeIdRef.current;
      const isNewThread = !id;
      if (!id) {
        // Carry the mode chosen on the blank chat into the thread it becomes,
        // so the very first turn runs in the mode the toolbar was showing.
        const t = await chatApi.createThread(scope, "", chatModeRef.current);
        id = t.id;
        // A host that has to know the conversation before it runs (the drawer
        // beside a page binds its page to it) is told here, before the turn.
        if (beforeFirstTurn) await beforeFirstTurn(t.id).catch(() => {});
        // Put the new thread in the picker and record the selection: a chat you
        // just started used to be unreachable the moment you navigated away.
        adoptThread(t);
      }
      const threadId = id;
      const post = () => withDeadline((signal) => fetch(`/api/chat/threads/${threadId}/turns`, {
        method: "POST",
        signal,
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
          ...(draftKeyFor(clientId) ? { draft_key: draftKeyFor(clientId) } : {}),
          // Ids only. The bytes went up on drop, so this stays a small JSON body
          // however large the screenshot was.
          ...(attachments?.length ? { attachments } : {}),
          // Send later: the server queues it under this rule.
          ...(whenRef.current.get(clientId) ? { when: whenRef.current.get(clientId) } : {}),
        }),
      }), SEND_TIMEOUT_MS);
      const attempt = async () => {
        const r = await post();
        if (!r.ok) {
          throw new HttpError(r.status, shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
        }
        return r.json();
      };
      // SILENT RETRIES FOR A FAULT THAT IS ALREADY OVER.
      //
      // The deployment produces two of these on its own — the edge's pooled
      // connection to the backend expiring on the same 5s clock the backend
      // closes it on, and the ~1s window of a backend restart — and both are
      // finished by the time the operator could read about them. Retrying under
      // THE SAME CLIENT ID is not a second message: the log dedupes on it
      // (chat_log.find_message_by_client_id), which is the property the retry
      // button already relies on. See isTransientSendFailure for what does not
      // qualify, and sendRetry.ts for the one case that is checked first.
      const res = await sendWithRetry(attempt, { landed: () => landedAlready(threadId, clientId) });
      // IT LANDED; ONLY THE ANSWER WAS LOST. The log has it, so it is accepted,
      // and the read below brings back the turn it started.
      if (res === LANDED) {
        dispatch({ type: "sendAcked", clientId });
        if (isNewThread) scheduleAutoNameRefetch(true);
        void syncUntilRead(sync);
        return { ok: true };
      }
      // QUEUED IS ACCEPTED. The thread was busy with something that reads
      // nothing, so the server took the message onto the thread's own queue and
      // will send it when that run ends. This browser is no longer responsible
      // for it: the optimistic copy goes, and the queue that comes back on the
      // sync is what renders from here on — on every device, not just this one.
      if (res.queued) {
        dispatch({ type: "sendRetracted", clientId });
        void syncUntilRead(sync);
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
      if (res.turn_id) dispatch({ type: "turnStarted", turnId: res.turn_id });
      // THE NAMER ALSO RUNS RIGHT NOW, server-side, not only at turn end. It is
      // scheduled the moment this message lands (chat_naming.schedule, from
      // chat_store.append_message) so a chat can be named while its first turn
      // is still running, which for a real conversation here is minutes. The
      // ONLY other place this refetch fired was onTurnEnd, so a title that
      // landed early sat unseen in the picker for the turn's entire length:
      // "New chat" through the whole first reply, on a chat that was already
      // correctly named seconds in. Firing it here too means the picker looks
      // again on the same backed-off schedule regardless of how long the reply
      // takes.
      if (isNewThread) scheduleAutoNameRefetch(true);
      // THE READ THAT TURNS THE BUBBLE INTO THE MESSAGE. Asked again if it
      // fails: the next poll is a minute away.
      void syncUntilRead(sync);
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
      const transient = isTransientSendFailure(e);
      dispatch({ type: "sendFailed", clientId, error: reason, transient });
      // ORPHANED TYPE-AHEAD. Messages typed while this creation was in flight
      // were parked in `awaitingRef` waiting for a thread id that never came.
      // Left there they sit as forever-in-flight bubbles with no failure state
      // and no recovery of their own. Fail them too, so each shows its state and
      // its own Retry, and CLEAR the ref so retry is the single owner of
      // recovery. Retrying any of them routes through planSend exactly as a
      // fresh send does: with no thread yet the FIRST one retried creates it
      // (`newTurn`), and `starting` is set synchronously (setStartingBoth) so a
      // second retry racing it HOLDS (`awaitThread`) rather than forking a rival
      // thread; once a thread exists `sendTurn` reuses that id, so every later
      // retry just addresses it. The batch recovers as one thread, in whatever
      // order the operator retries, and never becomes two. Same client ids, so
      // the log dedupes if a parked send had in fact reached the server.
      const orphaned = awaitingRef.current;
      awaitingRef.current = [];
      for (const w of orphaned) {
        dispatch({ type: "sendFailed", clientId: w.clientId, error: reason, transient });
        w.settle({ ok: false, error: reason });
      }
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
            activeIdRef.current, w.text, w.clientId, w.ts, w.attachments).then(w.settle);
        }
      }
    }
  }, [scope, selectedModel, selectedEffort, selectedPermissionMode, chatModeRef, activeIdRef, adoptThread, scheduleAutoNameRefetch, dispatch, sync, setErr, setStartingBoth, landedAlready, beforeFirstTurn]);

  // Hand a message to the run already in flight. It is PERSISTED BEFORE
  // DELIVERY server-side, so `delivered: true` means the chat has it whether or
  // not the running turn took it; `delivered: false` means the endpoint did not
  // take the message at all and it must go out as a turn.
  const injectIntoRun = useCallback(async (
    threadId: string, text: string, clientId: string, sentAt: string,
    attachments?: AttachmentRef[],
  ): Promise<SendOutcome> => {
    try {
      // Same client id on every attempt, same landed check after a timeout, as
      // a turn's POST (sendRetry.ts).
      const r = await sendWithRetry(
        () => chatApi.injectIntoThread(threadId, text, clientId, {
          sentAt, attachments, draftKey: draftKeyFor(clientId),
        }),
        { landed: () => landedAlready(threadId, clientId) },
      );
      // ANSWERED FOR A THREAD NO LONGER ON SCREEN. The store now holds another
      // conversation; dispatching into it would mark the wrong thread's sends,
      // and the !delivered fallback below would start a turn on the WRONG chat
      // with this chat's message. The server outcome stands either way: the
      // message is recorded or queued over there, and that thread's own outbox
      // and sync show it when it is next opened.
      if (activeIdRef.current !== threadId) {
        return r !== LANDED && (r.deferred || !r.delivered && !r.queued)
          ? { ok: false, error: "The chat changed before this message was sent." }
          : { ok: true };
      }
      if (r === LANDED) {
        dispatch({ type: "sendAcked", clientId });
        await syncUntilRead(sync);
        return { ok: true };
      }
      // THE SERVER TOOK IT AND IS HOLDING IT. The run in flight reads nothing (a
      // slash command, a voice turn), so it goes out as the next turn — sent by
      // the server, from the thread's own queue, whether or not this browser is
      // still here. It is not this tab's problem any more, so the optimistic
      // copy goes and the queue the sync brings back is what renders.
      if (r.queued) {
        dispatch({ type: "sendRetracted", clientId });
        await syncUntilRead(sync);
        return { ok: true };
      }
      // See sendTurn: an older backend deferring means it took nothing.
      if (r.deferred) {
        const why = "The chat is busy with a command this server cannot queue. Retry when it finishes.";
        dispatch({ type: "sendFailed", clientId, error: why });
        return { ok: false, error: why };
      }
      if (!r.delivered) return await sendTurn(text, clientId, sentAt, attachments);
      dispatch({ type: "sendAcked", clientId });
      // Pull the log immediately: the server recorded the message with the
      // `at_segment` that decides which block it closes, and that is the only
      // place that number exists.
      await syncUntilRead(sync);
      return { ok: true };
    } catch (e) {
      // The request never landed, so the server has no copy and this browser is
      // the only place the text exists. It stays in `pending`, marked, which is
      // both the bubble the operator sees and the durable outbox entry. If the
      // operator has switched threads, the entry lives in THAT thread's outbox
      // and surfaces when it is next opened; the current store is not touched.
      const error = sendFailureText(e);
      if (activeIdRef.current !== threadId) return { ok: false, error };
      dispatch({ type: "sendFailed", clientId, error, transient: isTransientSendFailure(e) });
      return { ok: false, error };
    }
  }, [dispatch, sync, sendTurn, activeIdRef, landedAlready]);
  injectIntoRunRef.current = injectIntoRun;

  const dispatchSend = useCallback(async (
    text: string, clientId: string, attachments?: AttachmentRef[],
  ): Promise<SendOutcome> => {
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
      turnActive: !!stateRef.current.activeTurn,
      hasThread: !!activeIdRef.current,
    });
    if (plan === "awaitThread") {
      // This chat is being created by a send that has not come back yet. A
      // second POST would create a second thread, so this one waits for the id
      // — the only thing the client still holds, because it is the only thing
      // the server cannot decide. It is in `pending`, so it is on disk, and
      // the flush below sends it the moment the thread exists.
      return new Promise<SendOutcome>((settle) => {
        awaitingRef.current.push({ clientId, text, ts, attachments, settle });
      });
    }
    // A RULE NEVER JOINS THE RUNNING TURN: it is posted as a turn, and the
    // server queues it.
    if (plan === "inject" && !whenRef.current.has(clientId)) {
      const threadId = activeIdRef.current;
      // Addressed by CHAT, not by run id. A client only knows about runs IT
      // started; a second tab, another device, or a run adopted after a restart
      // has no id to send, and every one of those used to fall back to queueing
      // and wait out the whole turn.
      if (threadId) return injectIntoRun(threadId, text, clientId, ts, attachments);
    }
    return sendTurn(text, clientId, ts, attachments);
  }, [dispatch, stateRef, activeIdRef, injectIntoRun, sendTurn]);

  const submitText = useCallback((raw: string, attachments?: AttachmentRef[],
                                  opts?: { keepDraft?: boolean; when?: SendRule }) => {
    const text = raw.trim();
    // Files with nothing typed is a complete message; the server stands the
    // filenames in for the text. Only a send with NEITHER is a no-op.
    if (!text && !attachments?.length) return null;
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
    if (cmd === "compact") { void compact(); return null; }
    if (cmd === "clear") { void clear(); return null; }
    if (cmd === "new") { onNewChat(); return null; }
    // A command that used to exist here is answered LOCALLY, for free, with
    // where its job went. Sending it spawned a process and spent a full prompt
    // so the CLI could reply that it does not work.
    const retired = retiredCommand(text);
    // Not a failure of anything, so it names no endpoint and takes the calm
    // info tone rather than the alert-triangle: the command was answered here,
    // locally, and the sentence is the whole answer, not an error.
    if (retired) { setErr({ text: retired, endpoint: null, tone: "info" }); return null; }
    const clientId = newClientId();
    if (opts?.keepDraft) keepDraftRef.current.add(clientId);
    else sentFromKeyRef.current.set(clientId, draftKeyRef.current);
    if (opts?.when) whenRef.current.set(clientId, opts.when);
    return { clientId, done: dispatchSend(text, clientId, attachments) };
  }, [setErr, onSend, compact, clear, dispatchSend]);

  const retry = useCallback(async (clientId: string) => {
    const pending = stateRef.current.pending[clientId];
    if (!pending) return;
    setErr(null);
    // THE SAME ID. The log dedupes on it, so if the first attempt did reach the
    // server after all, this re-send appends nothing and reconciles against the
    // event that is already there. There is nothing to take back first: the
    // entry in storage IS this pending send (useSendOutbox mirrors the map), so
    // re-dispatching under the same id updates one record rather than making a
    // second one.
    await dispatchSend(pending.text, clientId, pending.attachments);
  }, [stateRef, setErr, dispatchSend]);

  // Pull a still-local send back. Safe unconditionally: `sending` means the
  // request is in flight and this browser is simply giving up on showing it
  // (the request itself cannot be aborted from here, and if it does land the
  // server's own record of it is what the next sync shows, same as any other
  // message this tab did not know about yet); `failed` means the request
  // never reached the server at all, so there is nothing anywhere else to
  // take back. Either way a late `sendAcked`/`sendFailed` for this id is a
  // no-op (the reducer's `if (!cur) return state` guard).
  const cancel = useCallback((clientId: string) => {
    dispatch({ type: "sendCancelled", clientId });
  }, [dispatch]);

  return { starting, submitText, retry, cancel };
}

/** Whether this turn is the first exchange in the chat, which is when the
 *  auto-namer runs and the thread list needs a second look. */
export function wasFirstExchange(state: ThreadState): boolean {
  return committedMessages(state).length <= 2;
}
