// Which conversation the operator is actually in — persisted, per chat surface.
//
// Before this existed the chat had no durable notion of "the thread I am in": on
// every mount it adopted whichever thread had been touched most recently. That
// made three things broken in the same way:
//   * "New chat" / "/clear" cleared React state only, so the next mount silently
//     re-adopted the thread you had just left;
//   * a thread you created went missing if any other thread got a later update;
//   * switching provider reset the picker, stranding chats under a scope you
//     could no longer see.
// The selection is the operator's, so it is stored explicitly and read back on
// mount. NEW_CHAT is a real, persistable selection — "I asked for a blank chat"
// has to survive a reload just as much as "I am in thread X" does.

import { newClientId } from "./chat";

const SEL_PREFIX = "ws-chat-sel:";
const PROVIDER_PREFIX = "ws-chat-provider:";

// Sentinel selection meaning "the operator explicitly wants a blank chat here".
// Not a thread id — no thread exists until the first message is sent.
export const NEW_CHAT = "new";

// The workshop-wide scope: where a blank chat starts when nothing scoped it.
// A New chat launched from a project or an app dock inherits THAT surface
// instead; only an unscoped one lands here. It is never inherited from the
// picker's memory — "the scope I last looked at" is not "the scope this new
// conversation belongs to", and conflating the two is what made every new chat
// come up under whichever assistant was selected last.
export const DEFAULT_SCOPE = "general";

// Thread selection is keyed by SCOPE (the provider / context whose thread list
// the picker shows), because that is the list the selection indexes into.
export function readSelection(scope: string): string | null {
  try {
    return localStorage.getItem(SEL_PREFIX + scope);
  } catch {
    return null;
  }
}

export function writeSelection(scope: string, sel: string | null): void {
  try {
    if (sel) localStorage.setItem(SEL_PREFIX + scope, sel);
    else localStorage.removeItem(SEL_PREFIX + scope);
  } catch {
    /* private mode / storage disabled — selection just won't survive reload */
  }
}

// The route that starts a BLANK chat on a surface. Every "New chat" button
// outside the chat itself must use this and nothing else.
//
// The intent has to travel in the URL rather than in the selection store,
// because the store is keyed by the resolved SCOPE (the provider a data chat is
// on) and only Chat can compute that — a caller on the Chats page or a project
// page knows the context key and nothing more. That mismatch is precisely the
// bug: "New chat" navigated to /chat/general, Chat mounted, found no explicit
// intent, fell through to "restore the last selection for this scope", and
// reopened the conversation the operator had just asked to leave.
export function newChatHref(contextKey: string): string {
  return `/chat/${encodeURIComponent(contextKey)}?new=1`;
}

// Which scope a chat mount belongs in. Pure, so the rule can be read and tested
// on its own instead of being inferred from the order two async calls happen to
// resolve in — which is what it used to be, and why it broke intermittently.
//
// `scope` is the same axis as a thread's server-side context_key (list_threads
// filters on an exact match), so this is never a guess: a thread knows where it
// lives. Intent-first, matching the thread loader's own ranking:
//   1. a linked thread's home  — opening a chat opens it where it actually is
//   2. New chat → this surface — general unless launched from a scoped surface
//   3. what this surface was last left on — a bare return to a chat
// The picker's memory is LAST. It is "the scope I looked at", never "the scope
// this conversation belongs to"; ranking it above the intents is what made one
// assistant selection leak into every chat opened afterwards.
export function resolveScope(input: {
  contextKey: string;
  knownScopes: string[];
  linkedThreadHome: string | null;
  startNew: boolean;
  remembered: string | null;
}): string {
  const { contextKey, knownScopes, linkedThreadHome, startNew, remembered } = input;
  const known = (k: string | null): boolean => !!k && knownScopes.includes(k);
  const surface = known(contextKey) ? contextKey : DEFAULT_SCOPE;
  if (linkedThreadHome) return known(linkedThreadHome) ? linkedThreadHome : DEFAULT_SCOPE;
  if (startNew) return surface;
  return known(remembered) ? (remembered as string) : surface;
}

// Provider choice is keyed by CONTEXT KEY (the chat surface itself), so the
// focused chat reopens on the provider you left it on instead of snapping back
// to its default and hiding every thread you started under the other one.
export function readProvider(contextKey: string): string | null {
  try {
    return localStorage.getItem(PROVIDER_PREFIX + contextKey);
  } catch {
    return null;
  }
}

export function writeProvider(contextKey: string, providerKey: string): void {
  try {
    localStorage.setItem(PROVIDER_PREFIX + contextKey, providerKey);
  } catch {
    /* see writeSelection */
  }
}

// The type-ahead queue: messages typed while a turn is running, waiting to be
// delivered when it finishes. This used to be React state and nothing else, so
// closing the tab or refreshing destroyed them silently — a real message-loss
// path, not just a display bug. Keyed by thread id (the queue belongs to the
// conversation, not the surface).
export const QUEUE_PREFIX = "ws-chat-queue:";

// A queued message carries WHEN IT WAS TYPED, not just what it said.
//
// Without it, three messages typed inside one minute while nothing was
// responding are indistinguishable — to the model reading them — from three
// thoughts spread across an afternoon, and the difference decides whether the
// right reply is three answers or one. The operator's report: "this and the last
// two were sent in sequence before you ever read any of them and you won't even
// know that til now."
//
// It also carries the browser's id for the send. A queued message is one the
// operator has already hit enter on, so when it finally goes out it is that
// same send arriving late — and the bubble on screen has to be matched to the
// committed event by identity, not by comparing its text.
//
// `failed` marks an entry the server would not take. It is durable for the same
// reason the rest of the entry is: a send that failed is text the operator hit
// enter on and has not got back, and it used to live only in the reducer — so a
// reload or a thread switch erased it with no trace. A failed entry never drains
// on its own (the queue holds beside it); it waits for Retry.
//
// `error` is WHY it failed, and it is durable for exactly the same reason. The
// diagnosis lived only in the reducer, so a reload turned "the server refused
// this message (403: developer mode is off)" into UnsentNote's default line,
// "this message never reached the server" — which points at the network no
// matter what actually happened.
export type QueuedMessage = {
  text: string; sentAt: string; clientId: string; failed?: boolean; error?: string;
  // Files attached to this message, as server ids. They are already uploaded by
  // the time anything is queued, so a queued message survives a reload with its
  // attachments intact rather than being sent later as bare text. Carried here
  // and not just on the in-memory pending record because the queue is the copy
  // that outlives the tab.
  attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
};

export function readQueue(threadKey: string): QueuedMessage[] {
  try {
    const raw = localStorage.getItem(QUEUE_PREFIX + threadKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    let minted = false;
    // Migrates the older `string[]` format in place. A queue written by the
    // previous build is a message the operator has already hit enter on, so
    // dropping it would be message loss on upgrade; it just has no send time,
    // and "" reads as unknown rather than as some fabricated instant.
    const queue = parsed.flatMap((x): QueuedMessage[] => {
      if (typeof x === "string") {
        minted = true;
        return [{ text: x, sentAt: "", clientId: newClientId() }];
      }
      if (x && typeof x.text === "string") {
        const has = typeof x.clientId === "string" && !!x.clientId;
        if (!has) minted = true;
        return [{
          text: x.text,
          sentAt: typeof x.sentAt === "string" ? x.sentAt : "",
          ...(x.failed ? { failed: true as const } : {}),
          ...(typeof x.error === "string" && x.error ? { error: x.error } : {}),
          // Field-by-field on purpose (see the migration above), so this has to
          // be named or a queued message loses its files on the next read.
          ...(Array.isArray(x.attachments) && x.attachments.length
            ? { attachments: x.attachments as QueuedMessage["attachments"] }
            : {}),
          // A queue written by an older build has no id. Minting one here is
          // right: it has not been sent, so no committed event can be carrying
          // an id it would need to match.
          clientId: has ? (x.clientId as string) : newClientId(),
        }];
      }
      return [];
    });
    // AND THE MINTED ID IS WRITTEN BACK, so it is the entry's id rather than
    // this read's id. Two tabs holding the same pre-upgrade queue each minted
    // their own, sent the same message under different ids, and the server's
    // client_id dedupe had nothing to collapse them by — the upgrade path turned
    // into a duplicate-send path.
    if (minted) writeQueue(threadKey, queue);
    return queue;
  } catch {
    return [];
  }
}

export function writeQueue(threadKey: string, queue: QueuedMessage[]): void {
  try {
    if (queue.length) localStorage.setItem(QUEUE_PREFIX + threadKey, JSON.stringify(queue));
    else localStorage.removeItem(QUEUE_PREFIX + threadKey);
  } catch {
    /* see writeSelection */
  }
}

// THE HOLD, stored beside the queue and keyed the same way.
//
// Stop hands the wheel back: the queue stops draining itself until the operator
// sends again. That promise is only as durable as the thing that records it, and
// it used to be plain component state while the queue it governs was in
// localStorage. So the hold died on a reload or a thread switch and the queue
// did not — press Stop, switch away and back, and everything typed ahead fired
// as a new turn with nobody watching. Money spent without a click.
//
// Same key as the queue (a thread id, or the blank-chat slot for a scope),
// because it is a property OF that queue and has to travel with it.
export const HOLD_PREFIX = "ws-chat-hold:";

export function readHold(threadKey: string): boolean {
  try {
    return localStorage.getItem(HOLD_PREFIX + threadKey) === "1";
  } catch {
    return false;
  }
}

export function writeHold(threadKey: string, held: boolean): void {
  try {
    if (held) localStorage.setItem(HOLD_PREFIX + threadKey, "1");
    else localStorage.removeItem(HOLD_PREFIX + threadKey);
  } catch {
    /* see writeSelection */
  }
}

/** Everything this browser stores for a thread that no longer exists.
 *
 *  The delete path cleared the QUEUE, and only when the deleted thread was the
 *  one on screen. So deleting any other conversation left its queue behind, and
 *  every delete left its `ws-chat-hold:` key behind forever — a key that says
 *  "do not self-drain" for a chat that is gone. Neither leak was reachable
 *  afterwards, which is precisely why neither was ever noticed. */
export function forgetThread(threadKey: string): void {
  writeQueue(threadKey, []);
  writeHold(threadKey, false);
}

// What durable storage must hold: everything the server has not accepted yet, in
// the order the operator sent it. A batch being POSTed right now is still owed,
// so it stays in front of whatever was typed after it (invariant 5).
export function durableQueue(inFlight: QueuedMessage[], items: QueuedMessage[]): QueuedMessage[] {
  return [...inFlight, ...items];
}

// A batch the server refused goes back where it was: ahead of anything typed
// while it was in flight, and still ONE batch, so the retry is one turn.
export function restoreBatch(batch: QueuedMessage[], items: QueuedMessage[]): QueuedMessage[] {
  const ids = new Set(batch.map((m) => m.clientId));
  return [...batch, ...items.filter((m) => !ids.has(m.clientId))];
}

// Drop entries by send id. Used when a batch is accepted after the queue has
// already moved to another thread: the entries are still sitting in the old
// thread's storage and nothing else will take them out.
export function withoutIds(queue: QueuedMessage[], ids: Set<string>): QueuedMessage[] {
  return queue.filter((m) => !ids.has(m.clientId));
}

// Mark a batch the server refused, so it says so instead of sitting there
// claiming it will send when the current turn finishes. It will not: the queue
// holds after a refusal, by design, and only Retry releases it.
export function markFailed(batch: QueuedMessage[], error?: string): QueuedMessage[] {
  return batch.map((m) => {
    // Nothing to say that is not already said. Returning the entry itself keeps
    // its identity, which is what stops a re-mark from churning storage and the
    // list that renders from it.
    if (m.failed && (!error || m.error === error)) return m;
    return { ...m, failed: true as const, ...(error ? { error } : {}) };
  });
}

export function clearFailedFlag(queue: QueuedMessage[], clientId: string): QueuedMessage[] {
  return queue.map((m) => {
    if (m.clientId !== clientId || !m.failed) return m;
    // The diagnosis goes with the flag. It described the attempt that failed,
    // and a retry is a new attempt: leaving it would explain the wrong one.
    const { failed: _dropped, error: _why, ...rest } = m;
    return rest;
  });
}

// TWO TABS ON ONE THREAD MUST NOT DESTROY EACH OTHER'S QUEUE.
//
// The queue is per-thread durable storage, and every tab open on that thread
// writes the same key. A tab used to write its whole in-memory list over
// whatever was there, so: two tabs on thread X, type A in tab 1 (storage is
// `[A]`), type B in tab 2 — whose in-memory list is `[]`, because A was typed
// somewhere else — and tab 2 writes `[B]`. A is gone, and it was a message the
// operator had already hit enter on. That is invariant 1 broken across tabs by
// the code that exists to keep it.
//
// So a write MERGES. The rule is not "union everything", which would resurrect
// every entry this tab deliberately took out (a drain, a drop, an accepted
// batch): it is
//
//   an entry this tab has NEVER SEEN belongs to another tab, and survives.
//
// `seen` is every id this tab has held for this key since it bound to it. An id
// in `seen` and absent from `next` was removed on purpose. An id in neither is
// news from elsewhere and is kept.
//
// Order is by WHEN THE OPERATOR HIT ENTER (invariant 5), which is the only
// ordering that means anything once two tabs are involved — neither list's
// position can speak for the other's. `sentAt` is an ISO string so it sorts
// lexicographically; an entry with none (the pre-upgrade format) sorts first,
// which is true of it, since it was written by an older build.
export function mergeQueue(
  stored: QueuedMessage[], next: QueuedMessage[], seen: Set<string>,
): QueuedMessage[] {
  const mine = new Set(next.map((m) => m.clientId));
  const foreign = stored.filter((m) => !mine.has(m.clientId) && !seen.has(m.clientId));
  if (!foreign.length) return next;
  // Array.prototype.sort is stable, so entries typed in the same millisecond (or
  // carrying no time at all) keep the order of the list they came from.
  return [...next, ...foreign].sort((a, b) => (a.sentAt || "").localeCompare(b.sentAt || ""));
}
