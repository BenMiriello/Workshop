import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { newClientId } from "../../lib/chat";
import { readQueue, writeQueue, type QueuedMessage } from "../../lib/chatSelection";
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

export type MessageQueue = {
  items: QueuedMessage[];
  set: (next: QueuedMessage[]) => void;
  // Append one message. This is what every caller actually does, and spelling it
  // out stops each of them reaching into the live ref to do it by hand. Returns
  // the id it will be sent with, so the caller can track it (invariant 6).
  push: (text: string, clientId?: string) => string;
  // Append at a position captured EARLIER (invariant 5). The index is where the
  // queue ended when the request was made, so a request that has been waiting on
  // a round trip rejoins ahead of anything typed while it waited.
  insertAt: (index: number, text: string, clientId?: string) => string;
  // How long the queue is right now — the position a request made NOW would take.
  length: number;
  // Take one entry back out, BY ID. Used when something queued optimistically
  // turns out not to be needed — a no-op if the queue already drained, which is
  // the right answer: it was sent, so there is nothing to retract.
  drop: (clientId: string) => void;
  dispatch: () => void;
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
  send: (text: string, parts: QueuedMessage[]) => void;
}): MessageQueue {
  const [items, setItems] = useState<QueuedMessage[]>([]);
  const itemsRef = useRef<QueuedMessage[]>([]);
  const blankKey = `scope:${scope}`;
  const keyRef = useRef<string>(blankKey);

  // The caller passes these as inline lambdas. Held in refs so every callback
  // below stays referentially stable: the returned object lands in the dep
  // arrays of the chat's own callbacks, and a queue that changed identity on
  // every render would invalidate all of them (and re-run their effects) on
  // every keystroke.
  const sendRef = useRef(send);
  sendRef.current = send;
  const canDrainNowRef = useRef(canDrainNow);
  canDrainNowRef.current = canDrainNow;

  const set = useCallback((next: QueuedMessage[]) => {
    itemsRef.current = next;
    setItems(next);
    writeQueue(keyRef.current, next);
  }, []);

  const push = useCallback((text: string, clientId?: string) => {
    // Stamped at the moment the operator hit enter, which is the only moment
    // that can be observed later. Anything derived at dispatch time would record
    // when the queue drained instead — the very confusion this is here to end.
    const id = clientId || newClientId();
    set([...itemsRef.current, { text, sentAt: new Date().toISOString(), clientId: id }]);
    return id;
  }, [set]);

  const insertAt = useCallback((index: number, text: string, clientId?: string) => {
    const q = itemsRef.current;
    const i = Math.max(0, Math.min(index, q.length));
    const id = clientId || newClientId();
    set([...q.slice(0, i), { text, sentAt: new Date().toISOString(), clientId: id }, ...q.slice(i)]);
    return id;
  }, [set]);

  const drop = useCallback((clientId: string) => {
    const q = itemsRef.current;
    const i = q.findIndex((m) => m.clientId === clientId);
    if (i >= 0) set([...q.slice(0, i), ...q.slice(i + 1)]);
  }, [set]);

  const bindThread = useCallback((threadId: string) => {
    // Point at the thread BEFORE reading it, so the read-then-write round trip
    // can't land on the thread we're leaving (invariant 2).
    keyRef.current = threadId;
    const q = readQueue(threadId);
    itemsRef.current = q;
    setItems(q);
  }, []);

  const bindBlank = useCallback(() => {
    keyRef.current = blankKey;
    const q = readQueue(blankKey);
    itemsRef.current = q;
    setItems(q);
  }, [blankKey]);

  const resetToBlank = useCallback(() => {
    // Move the key to the blank slot FIRST. Clearing before moving would wipe
    // the queue still owed to the thread being left behind.
    keyRef.current = blankKey;
    set([]);
  }, [blankKey, set]);

  const carryToThread = useCallback((threadId: string) => {
    const carried = itemsRef.current;
    keyRef.current = threadId;
    writeQueue(blankKey, []);
    if (carried.length) writeQueue(threadId, carried);
  }, [blankKey]);

  const dispatch = useCallback(() => {
    const q = itemsRef.current;
    if (!q.length) return;
    // Invariant 4: consecutive messages go as ONE turn, not N — but a command
    // breaks the batch, because a command joined to anything else is no longer
    // a command. Whatever is left stays queued in order and the self-drain
    // (invariant 3) sends it when this turn ends.
    const [now, rest] = splitFirstTurn(q);
    set(rest);
    // The joined text is what the model reads; `now` carries the send times and
    // ids alongside it so the server can record each message at the moment it
    // was actually typed, instead of collapsing a burst into one timestamp.
    sendRef.current(now.map((m) => m.text).join("\n\n"), now);
  }, [set]);

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
  return useMemo(
    () => ({
      items, length: items.length,
      set, push, insertAt, drop, dispatch,
      bindThread, bindBlank, resetToBlank, carryToThread,
    }),
    [items, set, push, insertAt, drop, dispatch, bindThread, bindBlank, resetToBlank, carryToThread],
  );
}
