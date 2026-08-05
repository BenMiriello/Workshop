import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readQueue, writeQueue } from "../../lib/chatSelection";
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

export type MessageQueue = {
  items: string[];
  set: (next: string[]) => void;
  // Append one message. This is what every caller actually does, and spelling it
  // out stops each of them reaching into the live ref to do it by hand.
  push: (text: string) => void;
  // Append at a position captured EARLIER (invariant 5). The index is where the
  // queue ended when the request was made, so a request that has been waiting on
  // a round trip rejoins ahead of anything typed while it waited.
  insertAt: (index: number, text: string) => void;
  // How long the queue is right now — the position a request made NOW would take.
  length: number;
  // Take one entry back out (the newest match). Used when something queued
  // optimistically turns out not to be needed — a no-op if the queue already
  // drained, which is the right answer: it was sent, so there is nothing to
  // retract.
  drop: (text: string) => void;
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
  streaming,
  loadingThreads,
  held,
  logBusyRef,
  send,
  appendUserMessages,
}: {
  scope: string;
  streaming: boolean;
  loadingThreads: boolean;
  // Something outside a turn owns the conversation right now — currently only a
  // /compact whose server-side fold is still in flight. Draining under it would
  // send a later message as a turn BEFORE the command that was asked for first,
  // which is the reordering this whole hook exists to prevent.
  held: boolean;
  logBusyRef: React.MutableRefObject<boolean>;
  send: (text: string) => void;
  appendUserMessages: (texts: string[]) => void;
}): MessageQueue {
  const [items, setItems] = useState<string[]>([]);
  const itemsRef = useRef<string[]>([]);
  const blankKey = `scope:${scope}`;
  const keyRef = useRef<string>(blankKey);

  // The caller passes these as inline lambdas. Held in refs so every callback
  // below stays referentially stable: the returned object lands in the dep
  // arrays of the chat's own callbacks, and a queue that changed identity on
  // every render would invalidate all of them (and re-run their effects) on
  // every keystroke.
  const sendRef = useRef(send);
  sendRef.current = send;
  const appendRef = useRef(appendUserMessages);
  appendRef.current = appendUserMessages;

  const set = useCallback((next: string[]) => {
    itemsRef.current = next;
    setItems(next);
    writeQueue(keyRef.current, next);
  }, []);

  const push = useCallback((text: string) => {
    set([...itemsRef.current, text]);
  }, [set]);

  const insertAt = useCallback((index: number, text: string) => {
    const q = itemsRef.current;
    const i = Math.max(0, Math.min(index, q.length));
    set([...q.slice(0, i), text, ...q.slice(i)]);
  }, [set]);

  const drop = useCallback((text: string) => {
    const q = itemsRef.current;
    const i = q.lastIndexOf(text);
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
    appendRef.current(now);
    sendRef.current(now.join("\n\n"));
  }, [set]);

  // Invariant 3: whenever there is something queued and no turn is running, send
  // it. Queued means the operator already hit enter; delivery is owed.
  const drainingRef = useRef(false);
  useEffect(() => {
    if (streaming || loadingThreads || held || !items.length) return;
    if (drainingRef.current) return;
    drainingRef.current = true;
    // A tick of slack so a turn that is in the act of starting (the sender sets
    // `streaming` before its POST resolves) wins the race and we don't open a
    // second turn alongside it.
    const t = window.setTimeout(() => {
      drainingRef.current = false;
      if (!logBusyRef.current && itemsRef.current.length) dispatch();
    }, 250);
    return () => { window.clearTimeout(t); drainingRef.current = false; };
  }, [streaming, loadingThreads, held, items, dispatch, logBusyRef]);

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
