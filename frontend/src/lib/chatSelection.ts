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

const SEL_PREFIX = "ws-chat-sel:";
const PROVIDER_PREFIX = "ws-chat-provider:";

// Sentinel selection meaning "the operator explicitly wants a blank chat here".
// Not a thread id — no thread exists until the first message is sent.
export const NEW_CHAT = "new";

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

export function readQueue(threadKey: string): string[] {
  try {
    const raw = localStorage.getItem(QUEUE_PREFIX + threadKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function writeQueue(threadKey: string, queue: string[]): void {
  try {
    if (queue.length) localStorage.setItem(QUEUE_PREFIX + threadKey, JSON.stringify(queue));
    else localStorage.removeItem(QUEUE_PREFIX + threadKey);
  } catch {
    /* see writeSelection */
  }
}
