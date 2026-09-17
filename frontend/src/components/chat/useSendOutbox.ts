import { useCallback, useEffect, useRef } from "react";
import {
  OUTBOX_PREFIX, mergeOutbox, readOutbox, writeOutbox,
} from "../../lib/chatSelection";
import type { PendingSend, ThreadAction, ThreadState } from "./threadStore";
import { pendingSends } from "./threadStore";

// The outbox: sends this browser has made that THE SERVER HAS NOT TAKEN.
//
// This is all that is left of the old localStorage message queue, and the
// narrowing is the point. That queue tried to be two things at once — durable
// storage for messages waiting on a running turn, AND the mechanism that later
// sent them — and the second job is what made a message the operator had
// already sent depend on one browser being open, bound to the right thread, and
// awake at the right moment. Two of them were lost that way on 2026-08-12.
//
// The queue moved to the server (backend/services/chat_queue.py). What is left
// here is the ONE case the server cannot own, because it never heard about it:
//
//     the request did not land.
//
// Offline, backend restarting, edge 502, laptop lid closed mid-POST. The text
// exists only in this browser, so this browser has to hold it until it can hand
// it over. It is written BEFORE the request goes out and removed only when the
// server has answered, so the crash window is not "the message is gone", it is
// "the message is still in the outbox and goes out on the next attempt".
//
// ONE OWNER. `state.pending` in the reducer already means exactly "sends this
// browser has made that are not acknowledged", and this mirrors that map to
// storage rather than keeping a second list beside it. Every historical
// double-bubble in this chat came from two owners of the same message
// disagreeing; adding durability must not add another. So: the reducer is the
// truth, this is its disk, and the only thing it contributes on its own is the
// restore at mount.
//
// WHAT IS DELIBERATELY NOT HERE ANY MORE, because the server owns it now:
// self-draining, batching queued messages into one turn, the Stop hold,
// ordering a queue against a running turn. If you find yourself re-adding any
// of them, the message is being sent from a browser again.

const isFinal = (s: PendingSend) => s.status === "failed";

/** Everything to carry across a reload: in-flight and failed alike.
 *
 *  IN-FLIGHT COUNTS. A send whose POST was interrupted by the tab closing is
 *  indistinguishable, from here, from one that failed — and the honest thing to
 *  do with both is show them again with a Retry rather than pretend they went.
 *  If it did land, the client id it carries means the retry is deduped by the
 *  log and nothing is duplicated. */
function durable(sends: PendingSend[]): PendingSend[] {
  return sends.filter((s) => s.status !== "sent");
}

export type SendOutbox = {
  /** Point at a thread (or the blank-chat slot) and restore what is waiting. */
  bind: (key: string) => void;
  /** A blank chat just became a thread: its outbox moves with it. */
  carryToThread: (threadId: string) => void;
  /** New chat: the blank slot is explicitly discarded. */
  resetToBlank: () => void;
  /** Forget everything stored for a thread that no longer exists. */
  forget: (key: string) => void;
};

export function useSendOutbox({
  scope, state, dispatch,
}: {
  scope: string;
  state: ThreadState;
  dispatch: React.Dispatch<ThreadAction>;
}): SendOutbox {
  const blankKey = `scope:${scope}`;
  // THE KEY IS TRACKED EXPLICITLY, never read off a ref React assigns during
  // render. That exact mistake — deriving the storage key from `activeIdRef` —
  // is what made a thread switch write the incoming thread's queue back under
  // the outgoing thread's key, destroying it. The key moves only when the code
  // that changes threads says it moves.
  const keyRef = useRef<string>(blankKey);
  // Every id this tab has held for the current key, so a write can tell "another
  // tab added this" from "I took this out on purpose" (see mergeOutbox).
  const seenRef = useRef<Set<string>>(new Set());
  const restoringRef = useRef(false);

  const bind = useCallback((key: string) => {
    keyRef.current = key;
    const stored = readOutbox(key);
    // SEEDING `seen` WITH WHAT WAS JUST RESTORED IS HOW THE RESTORE ERASED
    // ITSELF. `seen` means "this tab has held this id", and `mergeOutbox` uses
    // it to tell another tab's new entry from one THIS tab deliberately took
    // out. Seeding it here asserts the second about an entry the reducer has
    // not applied yet -- and the reducer very often never gets to, because
    // `useThreadLog`'s own `[threadId]` effect dispatches `reset` (or
    // `seedThread`, which spreads over a fresh state) straight after, and
    // `reset` preserves `pending` only on the blank-chat-becomes-a-thread
    // transition. So `pending` emptied, the mirror ran with `mine = []`, every
    // stored id was in `seen`, and `mergeOutbox` concluded the operator had
    // retracted them all and wrote an empty outbox. A send that failed while
    // the network was down was destroyed by the act of reopening its thread.
    //
    // Starting empty costs nothing: the ids re-enter `seen` from the mirror's
    // own `for (const s of mine)` loop on the very next commit, which is the
    // only moment at which this tab has genuinely held them.
    seenRef.current = new Set();
    restoringRef.current = true;
    if (stored.length) dispatch({ type: "pendingRestored", sends: stored });
  }, [dispatch]);

  const carryToThread = useCallback((threadId: string) => {
    // A send that CREATED this thread was made against the blank slot. It moves
    // with the conversation rather than being left in a slot nothing reads
    // again — and it is still in flight, so it must not be dropped either.
    const carried = readOutbox(keyRef.current);
    keyRef.current = threadId;
    seenRef.current = new Set(carried.map((s) => s.clientId));
    writeOutbox(blankKey, []);
    if (carried.length) writeOutbox(threadId, mergeOutbox(readOutbox(threadId), carried, new Set()));
  }, [blankKey]);

  const resetToBlank = useCallback(() => {
    // Move the key FIRST: clearing before moving would wipe the outbox still
    // owed to the thread being left behind.
    keyRef.current = blankKey;
    seenRef.current = new Set();
    writeOutbox(blankKey, []);
  }, [blankKey]);

  const forget = useCallback((key: string) => {
    writeOutbox(key, []);
  }, []);

  // The mirror. Runs on every change to `pending`, which is cheap: it is empty
  // in the steady state, and a send is a handful of objects.
  useEffect(() => {
    const key = keyRef.current;
    const mine = durable(pendingSends(state));
    if (restoringRef.current) {
      // The restore that is still landing would otherwise be written back as if
      // this tab had produced it, before the reducer has applied it.
      restoringRef.current = false;
      if (!mine.length) return;
    }
    for (const s of mine) seenRef.current.add(s.clientId);
    // MERGE, never overwrite. Two tabs on one thread write the same key, and a
    // tab that overwrote with its own list erased a send made in the other one.
    writeOutbox(key, mergeOutbox(readOutbox(key), mine, seenRef.current));
  }, [state.pending, state]);

  // Another tab's write. A send made over there is a send this thread owes, and
  // the tab that made it may not be the one that comes back to retry it.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== OUTBOX_PREFIX + keyRef.current) return;
      const stored = readOutbox(keyRef.current).filter((s) => !seenRef.current.has(s.clientId));
      if (stored.length) dispatch({ type: "pendingRestored", sends: stored });
      for (const s of stored) seenRef.current.add(s.clientId);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [dispatch]);

  return { bind, carryToThread, resetToBlank, forget };
}

export { isFinal };
