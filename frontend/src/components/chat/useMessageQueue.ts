import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { newClientId } from "../../lib/chat";
import {
  HOLD_PREFIX, QUEUE_PREFIX,
  clearFailedFlag, durableQueue, markFailed, mergeQueue, readHold, readQueue,
  restoreBatch, withoutIds, writeHold, writeQueue,
  type QueuedMessage,
} from "../../lib/chatSelection";
import { splitFirstTurn } from "./turnBatching";

// Type-ahead: messages sent while a turn is running.
//
// A session cannot run two turns at once, so a message sent during one waits.
// "Waits" is the whole problem: as far as the operator is concerned it has been
// SENT, so every path that could drop it is message loss. This hook owns that
// promise in one place — previously the queue was six variables and three
// effects interleaved with the rest of the chat, and each of the bugs below was
// found separately, months apart.
//
// THE INVARIANTS, each of which was once violated:
//
//  1. The queue is durable. It survives a reload or a closed tab (localStorage,
//     via lib/chatSelection).
//  2. The queue belongs to a thread, tracked EXPLICITLY. It used to be read off
//     `activeIdRef` at write time, and that ref is assigned during render — so
//     mid-switch it still held the OUTGOING thread: loading the incoming
//     thread's queue then wrote it back under the outgoing thread's key.
//     Switching chats destroyed the queue of the chat you left. The key moves
//     only when the code that changes threads says it moves.
//  3. A queue with nothing running must drain ITSELF. Dispatch used to be
//     reachable only from the stream's finish handler and from Stop — both
//     meaning "a turn ended in THIS tab". Every other way a turn can end (a
//     reload, a closed tab, a dropped stream past its reconnect budget, a
//     backend restart, a turn finishing while the tab slept) left the queue
//     restored on the next load with nothing that would ever send it. The
//     messages sat looking pending forever, and anything typed afterwards
//     sailed past them as its own turn. A message-loss bug wearing the costume
//     of a pending one.
//  4. Everything queued behind one turn goes as ONE next turn. Sending them
//     individually made N queued messages into N sequential turns, so the last
//     thing typed could wait out several more before being read.
//     ...EXCEPT a slash command, which is its own turn. Joined to anything else
//     it stops being a command at all — see turnBatching.
//  5. The queue is ORDERED BY WHEN THE OPERATOR HIT ENTER, and nothing may take
//     a position later than the moment it was asked for. `push` appends, which
//     is right for a message being typed and wrong for a request made earlier
//     that is only now being handed over — that is what put a "/compact" below
//     a message sent after it. `insertAt` exists for the second case.
//  6. A queued entry carries the browser id it will be SENT with. A queued
//     message is one the operator already hit enter on; when it finally goes
//     out it is that same send arriving late, so the bubble on screen is matched
//     to the committed event by that id. Entries used to be identified by their
//     text, which is not an identity — two identical messages were one entry as
//     far as `drop` was concerned.
//  7. The HOLD is as durable as the queue it holds. Stop stops the self-drain
//     until the operator sends again; that fact used to live in component state
//     while the queue lived in localStorage, so a reload or a thread switch
//     dropped the hold and kept the queue — and the whole thing fired as an
//     unattended turn. It is stored beside the queue, under the same key, and
//     is read back when the queue is.
//  8. A DRAINED BATCH IS STILL QUEUED UNTIL THE SERVER TAKES IT. Dispatch used
//     to write the shortened queue to localStorage and only then send, so for
//     the length of the POST — thread creation, prompt assembly, the request
//     itself — those messages existed nowhere but in memory. Closing the tab in
//     that window lost them outright, which is invariant 1 broken by the code
//     that drains the queue. They now move to an in-flight list that storage
//     still holds, in front of anything typed after them, and leave it only when
//     the server has accepted them. A refusal puts them back as ONE batch
//     (invariant 4) and holds the queue, so the retry is one turn the operator
//     asks for rather than a loop this hook runs on its own.
//  9. A WRITE MERGES; IT DOES NOT OVERWRITE. Every tab open on a thread writes
//     the same storage key, and each one used to write its whole in-memory list
//     over whatever was there. Two tabs on thread X: type A in tab 1 (storage
//     `[A]`), type B in tab 2 — whose list is `[]`, because A was typed
//     elsewhere — and tab 2 wrote `[B]`. A was gone. That is invariant 1 broken
//     across tabs by the code that exists to keep it. A write now keeps anything
//     in storage this tab has NEVER SEEN (chatSelection.mergeQueue) and adopts
//     the merged list, so two tabs converge instead of racing. "Never seen" is
//     the whole rule: an id this tab has held and dropped was dropped on
//     purpose, and a plain union would resurrect every drained batch.
// 10. A SEND THE SERVER REFUSED IS STILL THE OPERATOR'S TEXT. It used to live
//     only in the reducer, with the composer already cleared, so a reload or a
//     thread switch erased it with no trace at all. It goes onto the durable
//     queue MARKED, and the queue holds: it renders as a message that failed,
//     with a Retry, rather than as one that will send itself when the turn ends
//     — which is the one thing it will not do.
// 11. A QUEUE BELONGS TO A THREAD, NOT TO A TAB. Storage was read once, at
//     bind, so a second tab open on the same chat never learned about anything
//     queued elsewhere — and the tab that DID hold it might not be the one that
//     notices the turn end. A `storage` listener adopts another tab's write.

/** How a send ended.
 *
 *  THE REASON TRAVELS WITH THE REFUSAL. A drained batch is settled by the queue,
 *  not by the sender that knows what went wrong, and this used to be a bare
 *  boolean — so the queue wrote the batch back marked `failed` with nothing to
 *  say about why, and the operator got UnsentNote's default line ("this message
 *  never reached the server") for a 403 that had named its own cause. */
export type SendOutcome = { ok: true } | { ok: false; error: string };

export type MessageQueue = {
  items: QueuedMessage[];
  set: (next: QueuedMessage[]) => void;
  // Append one message. This is what every caller actually does, and spelling it
  // out stops each of them reaching into the live ref to do it by hand. Returns
  // the id it will be sent with, so the caller can track it (invariant 6).
  push: (text: string, clientId?: string, attachments?: QueuedMessage["attachments"]) => string;
  // Append at a position captured EARLIER (invariant 5). The index is where the
  // queue ended when the request was made, so a request that has been waiting on
  // a round trip rejoins ahead of anything typed while it waited.
  insertAt: (index: number, text: string, clientId?: string,
             attachments?: QueuedMessage["attachments"]) => string;
  // How long the queue is right now — the position a request made NOW would take.
  length: number;
  // Take one entry back out, BY ID. Used when something queued optimistically
  // turns out not to be needed — a no-op if the queue already drained, which is
  // the right answer: it was sent, so there is nothing to retract.
  drop: (clientId: string) => void;
  // A send the server refused (invariant 10). Held durably, marked, WITH THE
  // REASON, and the queue holds so nothing sends it unattended.
  pushFailed: (text: string, clientId: string, sentAt?: string, error?: string,
               attachments?: QueuedMessage["attachments"]) => void;
  // Retry: unmark it and hand the wheel back, so the ordinary self-drain sends
  // it as the turn it always was. Nothing bespoke re-sends a failed entry.
  retryFailed: (clientId: string) => void;
  dispatch: () => void;
  // Is this thread's queue holding? Read by the chat into canDrainQueue, so the
  // queue and the send path share one answer to "may anything send now".
  held: boolean;
  // Stop the self-drain until the operator sends again (Stop).
  hold: () => void;
  // They are driving again.
  release: () => void;
  // Point the queue at a thread and load that thread's stored queue.
  bindThread: (threadId: string) => void;
  // Point at the blank-chat slot for this scope and load anything waiting there.
  bindBlank: () => void;
  // Start a new chat: point at the blank slot and clear it.
  resetToBlank: () => void;
  // A blank chat just became a real thread: carry anything typed while the first
  // turn was starting. Without this the next hydrate reads the (empty) thread
  // queue and those messages are never delivered and never seen again.
  carryToThread: (threadId: string) => void;
};

export function useMessageQueue({
  scope,
  canDrain,
  canDrainNow,
  send,
}: {
  scope: string;
  // May the queue send right now? ONE condition, computed by the chat from the
  // store's state (see sendPlan.canDrainQueue) so the queue and the send path
  // cannot disagree about whether the chat is busy. It used to be four separate
  // conditions here plus a 250ms timer that re-checked a ref when it fired,
  // because a turn in the act of starting looked idle.
  //
  // It arrives twice on purpose. `canDrain` is the TRIGGER — a value, so a
  // change re-runs the effect. `canDrainNow` is the TRUTH AT EFFECT TIME, read
  // through a ref: dispatching sets `starting` in the same commit that shortens
  // the queue, so the re-run caused by the shorter queue would otherwise still
  // see the previous render's `true` and fire a second turn alongside the first.
  canDrain: boolean;
  canDrainNow: () => boolean;
  // Hand the batch to the send path. Resolves ok only once the server has
  // accepted it; until then the batch stays in durable storage (invariant 8).
  // A refusal carries the reason, which is written down with the batch.
  send: (text: string, parts: QueuedMessage[]) => Promise<SendOutcome>;
}): MessageQueue {
  const [items, setItems] = useState<QueuedMessage[]>([]);
  const itemsRef = useRef<QueuedMessage[]>([]);
  const blankKey = `scope:${scope}`;
  const keyRef = useRef<string>(blankKey);
  // Invariant 7. Held in a ref as well as in state because `dispatch` is called
  // directly by the stream's finish handler, which does not go through the
  // chat's `canDrain` and would otherwise fire a held queue the moment a run
  // ended — including a run this tab picked up after a reload.
  const [held, setHeld] = useState(false);
  const heldRef = useRef(false);
  const setHeldBoth = useCallback((v: boolean, persist = true) => {
    heldRef.current = v;
    setHeld(v);
    if (persist) writeHold(keyRef.current, v);
  }, []);

  // The caller passes these as inline lambdas. Held in refs so every callback
  // below stays referentially stable: the returned object lands in the dep
  // arrays of the chat's own callbacks, and a queue that changed identity on
  // every render would invalidate all of them (and re-run their effects) on
  // every keystroke.
  const sendRef = useRef(send);
  sendRef.current = send;
  const canDrainNowRef = useRef(canDrainNow);
  canDrainNowRef.current = canDrainNow;

  // The batch that has been handed to the send path and not yet accepted
  // (invariant 8). It is out of `items` — the send pipeline renders it as
  // pending bubbles, so leaving it in the queue would draw it twice — but it is
  // still in durable storage, ahead of everything typed after it.
  const inFlightRef = useRef<QueuedMessage[]>([]);

  // Every send id this tab has held for the CURRENT key (invariant 9). It is
  // what separates "another tab queued this" from "I took this out on purpose",
  // and it is per-key, so binding elsewhere starts it over.
  const seenRef = useRef<Set<string>>(new Set());

  const set = useCallback((next: QueuedMessage[]) => {
    const key = keyRef.current;
    // MERGE, never overwrite (invariant 9). The merged list is adopted, not just
    // written: a tab that keeps rendering a queue it has already been told is
    // longer is the same disagreement one layer up.
    const merged = mergeQueue(readQueue(key), next, seenRef.current);
    for (const m of merged) seenRef.current.add(m.clientId);
    for (const m of inFlightRef.current) seenRef.current.add(m.clientId);
    itemsRef.current = merged;
    setItems(merged);
    writeQueue(key, durableQueue(inFlightRef.current, merged));
  }, []);

  const push = useCallback((text: string, clientId?: string,
                            attachments?: QueuedMessage["attachments"]) => {
    // Stamped at the moment the operator hit enter, which is the only moment
    // that can be observed later. Anything derived at dispatch time would record
    // when the queue drained instead — the very confusion this is here to end.
    const id = clientId || newClientId();
    set([...itemsRef.current, {
      text, sentAt: new Date().toISOString(), clientId: id,
      ...(attachments?.length ? { attachments } : {}),
    }]);
    return id;
  }, [set]);

  const insertAt = useCallback((index: number, text: string, clientId?: string,
                                attachments?: QueuedMessage["attachments"]) => {
    const q = itemsRef.current;
    const i = Math.max(0, Math.min(index, q.length));
    const id = clientId || newClientId();
    set([...q.slice(0, i), {
      text, sentAt: new Date().toISOString(), clientId: id,
      ...(attachments?.length ? { attachments } : {}),
    }, ...q.slice(i)]);
    return id;
  }, [set]);

  const drop = useCallback((clientId: string) => {
    const q = itemsRef.current;
    const i = q.findIndex((m) => m.clientId === clientId);
    // Seen even if it is not here: `drop` is a decision about this id, and the
    // merge must not read its absence as news from another tab.
    seenRef.current.add(clientId);
    if (i >= 0) set([...q.slice(0, i), ...q.slice(i + 1)]);
  }, [set]);

  // Point the queue at a key and load what is waiting there — the queue AND its
  // hold, which is a property of that queue and stored beside it.
  const bindKey = useCallback((key: string) => {
    // Point at the thread BEFORE reading it, so the read-then-write round trip
    // can't land on the thread we're leaving (invariant 2).
    keyRef.current = key;
    // A batch in flight belongs to the thread it was drained from, and its
    // entries are still in THAT thread's storage. Forget it here; the send's
    // completion handler sees the key has moved and settles it there.
    inFlightRef.current = [];
    const q = readQueue(key);
    // What this tab has seen of the NEW key is exactly what it just read.
    seenRef.current = new Set(q.map((m) => m.clientId));
    itemsRef.current = q;
    setItems(q);
    setHeldBoth(readHold(key), false);
  }, [setHeldBoth]);

  const bindThread = useCallback((threadId: string) => { bindKey(threadId); }, [bindKey]);

  const bindBlank = useCallback(() => { bindKey(blankKey); }, [bindKey, blankKey]);

  const resetToBlank = useCallback(() => {
    // Move the key to the blank slot FIRST. Clearing before moving would wipe
    // the queue still owed to the thread being left behind.
    keyRef.current = blankKey;
    // The ONE write that does not merge (invariant 9). "New chat" is an explicit
    // discard of the blank slot; going through `set` would read the slot back,
    // find every entry unseen under the key we have only just moved to, and
    // faithfully restore what the operator just asked to clear.
    seenRef.current = new Set();
    inFlightRef.current = [];
    itemsRef.current = [];
    setItems([]);
    writeQueue(blankKey, []);
    setHeldBoth(false);
  }, [blankKey, setHeldBoth]);

  const carryToThread = useCallback((threadId: string) => {
    // A batch drained from the blank chat is the reason this thread exists, so
    // it moves with the key rather than being left in a slot nobody reads again.
    const carried = durableQueue(inFlightRef.current, itemsRef.current);
    const carriedHold = heldRef.current;
    keyRef.current = threadId;
    seenRef.current = new Set(carried.map((m) => m.clientId));
    writeQueue(blankKey, []);
    writeHold(blankKey, false);
    if (carried.length) writeQueue(threadId, carried);
    if (carriedHold) writeHold(threadId, true);
  }, [blankKey]);

  // The end of a drain (invariant 8): the server took the batch, or it did not.
  const settleDrain = useCallback((
    key: string, batch: QueuedMessage[], outcome: SendOutcome,
  ) => {
    const ok = outcome.ok;
    const ids = new Set(batch.map((m) => m.clientId));
    if (!inFlightRef.current.some((m) => ids.has(m.clientId))) {
      // The queue has moved to another thread and left this batch behind in
      // that thread's storage. Settle it THERE, without touching what is on
      // screen now: accepted means take it out, refused means it stays queued
      // where the operator will find it when they come back.
      if (ok) writeQueue(key, withoutIds(readQueue(key), ids));
      return;
    }
    inFlightRef.current = withoutIds(inFlightRef.current, ids);
    if (ok) { set(itemsRef.current); return; }
    // Refused. Back in front of whatever was typed while it was in flight, as
    // ONE batch, and the queue holds: re-draining on the spot would spin the
    // failing request as fast as the effect can re-run. The operator's next
    // send releases it and it goes out as the single turn it always was.
    //
    // MARKED, because it is held (invariant 10). Unmarked it rendered as an
    // ordinary queued message — "sends when the current turn finishes" — which
    // is precisely what a held entry does not do, so a refused batch looked like
    // a patient one for as long as the operator left it alone.
    set(restoreBatch(markFailed(batch, outcome.ok ? undefined : outcome.error), itemsRef.current));
    setHeldBoth(true);
  }, [set, setHeldBoth]);

  const pushFailed = useCallback((
    text: string, clientId: string, sentAt?: string, error?: string,
    attachments?: QueuedMessage["attachments"],
  ) => {
    seenRef.current.add(clientId);
    set([
      ...itemsRef.current.filter((m) => m.clientId !== clientId),
      {
        text, sentAt: sentAt || new Date().toISOString(), clientId, failed: true,
        ...(error ? { error } : {}),
        ...(attachments?.length ? { attachments } : {}),
      },
    ]);
    setHeldBoth(true);
  }, [set, setHeldBoth]);

  const retryFailed = useCallback((clientId: string) => {
    set(clearFailedFlag(itemsRef.current, clientId));
    // Releasing is the retry. The self-drain (invariant 3) sends it, batched
    // with anything else waiting (invariant 4), under the id it already has —
    // so if the first attempt did reach the server after all, the log's
    // client_id dedupe collapses the two.
    setHeldBoth(false);
  }, [set, setHeldBoth]);

  const dispatch = useCallback(() => {
    const q = itemsRef.current;
    if (!q.length) return;
    // Invariant 7. The gate lives HERE, not only in the chat's canDrain, because
    // the stream's finish handler calls this directly.
    if (heldRef.current) return;
    // One batch at a time. A second would race the first's settlement over the
    // same storage key.
    if (inFlightRef.current.length) return;
    // A FAILED ENTRY IS NOT DRAINABLE (invariant 10). It waits for Retry, and
    // that is what the bubble beside it says. The hold alone did not enforce
    // this: `onSend` releases the queue on the operator's next send, so a
    // failure could go out on its own after the following turn ended — as a
    // turn nobody asked for, and, since a re-send re-stamps where it sits, one
    // that jumped from where it failed to the bottom of the chat.
    const drainable = q.filter((m) => !m.failed);
    if (!drainable.length) return;
    // Invariant 4: consecutive messages go as ONE turn, not N — but a command
    // breaks the batch, because a command joined to anything else is no longer
    // a command. Whatever is left stays queued in order and the self-drain
    // (invariant 3) sends it when this turn ends.
    const [now] = splitFirstTurn(drainable);
    // Taken out BY ID rather than by taking `splitFirstTurn`'s tail, so the
    // failed entries it never saw keep their places instead of being dropped.
    const drained = new Set(now.map((m) => m.clientId));
    const rest = q.filter((m) => !drained.has(m.clientId));
    // Out of the queue, into the in-flight list — which `set` keeps writing to
    // storage, so a tab closed mid-POST still has them (invariant 8).
    const key = keyRef.current;
    inFlightRef.current = now;
    set(rest);
    // The joined text is what the model reads; `now` carries the send times and
    // ids alongside it so the server can record each message at the moment it
    // was actually typed, instead of collapsing a burst into one timestamp.
    void Promise.resolve(sendRef.current(now.map((m) => m.text).join("\n\n"), now))
      .then((outcome) => settleDrain(key, now, outcome))
      .catch((e) => settleDrain(key, now, { ok: false, error: String(e?.message || e) }));
  }, [set, settleDrain]);

  // Invariant 3: whenever there is something queued and nothing is running, send
  // it. Queued means the operator already hit enter; delivery is owed.
  //
  // No timer. `canDrain` already accounts for a turn that has been asked for and
  // not confirmed, so there is no interval in which the chat is honestly idle
  // but a turn is on its way — which is the race the 250ms slack covered, and
  // it covered it by guessing how long a POST takes.
  useEffect(() => {
    if (!canDrain || !items.length) return;
    if (!canDrainNowRef.current()) return;
    dispatch();
  }, [canDrain, items, dispatch]);

  // Memoized: see the sendRef/appendRef note above — the identity of this object
  // is load-bearing for the caller's dep arrays.
  // Invariant 11: A QUEUE BELONGS TO A THREAD, NOT TO A TAB. Storage was read
  // once, at bind, so a message typed in one tab sat in another tab's storage
  // and was never seen by the tab that would go on to notice the turn end and
  // drain. Two tabs on one chat is ordinary — the operator keeps a focused chat
  // open beside the app — and in that arrangement a queued message could wait
  // out every turn until the tab that typed it happened to come back.
  //
  // `storage` fires only in OTHER tabs, which is exactly the notification
  // wanted, and `set` merges rather than overwrites (invariant 9), so adopting
  // another tab's write cannot resurrect a batch this tab drained on purpose.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      const key = keyRef.current;
      if (e.key === QUEUE_PREFIX + key) set(itemsRef.current);
      else if (e.key === HOLD_PREFIX + key) setHeldBoth(readHold(key), false);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [set, setHeldBoth]);

  const hold = useCallback(() => setHeldBoth(true), [setHeldBoth]);
  const release = useCallback(() => setHeldBoth(false), [setHeldBoth]);

  return useMemo(
    () => ({
      items, length: items.length,
      set, push, insertAt, drop, dispatch, pushFailed, retryFailed,
      held, hold, release,
      bindThread, bindBlank, resetToBlank, carryToThread,
    }),
    [items, set, push, insertAt, drop, dispatch, pushFailed, retryFailed,
     held, hold, release,
     bindThread, bindBlank, resetToBlank, carryToThread],
  );
}
