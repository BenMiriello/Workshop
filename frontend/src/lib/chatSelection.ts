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
const QUEUE_PREFIX = "ws-chat-queue:";

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
export type QueuedMessage = { text: string; sentAt: string; clientId: string };

export function readQueue(threadKey: string): QueuedMessage[] {
  try {
    const raw = localStorage.getItem(QUEUE_PREFIX + threadKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Migrates the older `string[]` format in place. A queue written by the
    // previous build is a message the operator has already hit enter on, so
    // dropping it would be message loss on upgrade; it just has no send time,
    // and "" reads as unknown rather than as some fabricated instant.
    return parsed.flatMap((x): QueuedMessage[] => {
      if (typeof x === "string") return [{ text: x, sentAt: "", clientId: newClientId() }];
      if (x && typeof x.text === "string") {
        return [{
          text: x.text,
          sentAt: typeof x.sentAt === "string" ? x.sentAt : "",
          // A queue written by an older build has no id. Minting one here is
          // right: it has not been sent, so no committed event can be carrying
          // an id it would need to match.
          clientId: typeof x.clientId === "string" && x.clientId ? x.clientId : newClientId(),
        }];
      }
      return [];
    });
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
