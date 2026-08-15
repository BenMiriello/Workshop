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

import { DEV_MODE, DEVELOPMENT_SCOPE } from "./devMode";

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
//
// In a developer build the unscoped surface's OWN default is `development`,
// not `general`. Development sorts above Workshop in the picker for the same
// reason (chatScope.ts's scopeGroups): it is where a developer already is, and
// landing a brand-new chat under the root organising layer instead makes the
// common case the second click, not the first. This only changes what an
// UNTOUCHED surface defaults to — `contextKey` naming a specific place (a
// project, an app dock) and `remembered` (an explicit past pick, including
// picking `general` on purpose) both still win, same as before.
export function resolveScope(input: {
  contextKey: string;
  knownScopes: string[];
  linkedThreadHome: string | null;
  startNew: boolean;
  remembered: string | null;
}, devMode: boolean = DEV_MODE): string {
  const { contextKey, knownScopes, linkedThreadHome, startNew, remembered } = input;
  const known = (k: string | null): boolean => !!k && knownScopes.includes(k);
  const devDefault =
    devMode && known(DEVELOPMENT_SCOPE) ? DEVELOPMENT_SCOPE : DEFAULT_SCOPE;
  const surface =
    known(contextKey) && contextKey !== DEFAULT_SCOPE ? contextKey : devDefault;
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


// ---------------------------------------------------------------------------
// The OUTBOX: sends this browser made that the server has not taken.
// ---------------------------------------------------------------------------
//
// WHAT USED TO BE HERE was a type-ahead queue: messages waiting on a running
// turn, stored per thread, drained by whichever tab happened to be looking at
// that thread when the turn ended. Eleven numbered invariants accumulated on
// top of it, each from a real report, and every one of them was an attempt to
// make one browser's localStorage behave like storage.
//
// It cannot be. On 2026-08-12 the operator sent two long messages during a
// `/compact` turn, moved to another chat before it finished, and came back on a
// different device. The messages had never left that first browser and the
// server had never heard of them. There was nothing to recover.
//
// THE QUEUE IS THE SERVER'S NOW (backend/services/chat_queue.py). A message the
// operator hits enter on is POSTed immediately; if the thread is busy with
// something that cannot be spoken to, the server records it on the thread and
// sends it when that turn ends, with or without a browser present.
//
// So what is left here is the one thing the server genuinely cannot know about:
// A SEND WHOSE REQUEST NEVER LANDED. Offline, a backend restart, an edge 502.
// That text exists only in this browser, and it is written here before the
// request goes out and removed when the server answers.
//
// Keyed by thread id (or `scope:<scope>` for a chat that does not exist yet),
// because an un-sent message belongs to the conversation it was typed into.

export const OUTBOX_PREFIX = "ws-chat-outbox:";

/** One un-acknowledged send. The same shape the reducer's `pending` holds, so
 *  the mirror is a copy rather than a translation — see useSendOutbox. */
export type OutboxSend = {
  clientId: string;
  text: string;
  ts: string;
  status: "sending" | "sent" | "failed";
  error?: string;
  attachments?: { id: string; name: string; kind: "image" | "pdf" | "text" }[];
  at: number;
  afterSeq: number;
};

/** How long a send that never landed stays offered as a Retry.
 *
 *  There was no bound at all until 2026-08-14, and `SendOutbox.forget()` (the
 *  one function that clears a key outright) has never had a caller. So a send
 *  that failed once sat in localStorage forever: the operator reported opening
 *  a chat and seeing a single old failed message, alone on screen, every time.
 *
 *  Why a bound rather than an auto-resend. Every consumer messenger with a
 *  stated position on this (iMessage, WhatsApp, Signal) requires an explicit
 *  tap; auto-retry-on-reconnect is standard only for idempotent infrastructure
 *  writes, like the Background Sync API. And this chat is not a person: a
 *  resend here POSTs a turn that can start an agentic run and spend money, on a
 *  conversation whose context has moved on. So retry stays explicit, and the
 *  offer simply stops being made once it is stale.
 *
 *  It also stops a windowing bug. The transcript now loads only its newest
 *  slice (useThreadLog INITIAL_TAIL), and `committedClientIds` is what hides
 *  this durable copy once the log carries the same send. A send that actually
 *  LANDED, long enough ago to fall outside that window, would find no committed
 *  twin and draw again as a failure. Bounding the age keeps every surviving
 *  entry recent enough to sit inside the window. */
const OUTBOX_MAX_AGE_MS = 24 * 60 * 60 * 1000;

function outboxExpired(ts: string, now: number): boolean {
  // `ts` and NOT `at`: `at` is a monotonic counter used to order two sends made
  // in the same millisecond, not a clock (see PendingSend in threadStore). It
  // is meaningless as an age and reading it as one would expire everything.
  if (!ts) return false;
  const at = Date.parse(ts);
  // An unparseable stamp cannot be shown to be old, and dropping it would throw
  // away text the operator wrote. Keep it: the reported failure mode is real
  // sends piling up, and those always carry an ISO `ts`.
  if (!Number.isFinite(at)) return false;
  return now - at > OUTBOX_MAX_AGE_MS;
}

export function readOutbox(key: string): OutboxSend[] {
  try {
    const raw = localStorage.getItem(OUTBOX_PREFIX + key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    const out: OutboxSend[] = [];
    for (const x of parsed) {
      if (!x || typeof x.text !== "string" || typeof x.clientId !== "string" || !x.clientId) {
        continue;
      }
      // Filtered on READ, so every caller is covered by one check and nothing
      // here writes as a side effect of reading. The expired entries leave
      // storage on the next write for this key, which useSendOutbox does
      // whenever the set changes.
      if (outboxExpired(typeof x.ts === "string" ? x.ts : "", now)) continue;
      out.push({
        clientId: x.clientId,
        text: x.text,
        ts: typeof x.ts === "string" ? x.ts : "",
        // A send read back from storage is one nothing is currently carrying:
        // whatever it was doing when this tab went away, it is not doing now.
        // "failed" is the honest state, and it is the one that offers a Retry.
        status: "failed",
        ...(typeof x.error === "string" && x.error ? { error: x.error } : {}),
        ...(Array.isArray(x.attachments) && x.attachments.length
          ? { attachments: x.attachments }
          : {}),
        at: Number(x.at) || 0,
        afterSeq: Number(x.afterSeq) || 0,
      });
    }
    return out;
  } catch {
    return [];
  }
}

export function writeOutbox(key: string, sends: OutboxSend[]): void {
  try {
    if (sends.length) localStorage.setItem(OUTBOX_PREFIX + key, JSON.stringify(sends));
    else localStorage.removeItem(OUTBOX_PREFIX + key);
  } catch {
    /* see writeSelection */
  }
}

/** TWO TABS ON ONE THREAD MUST NOT DESTROY EACH OTHER'S OUTBOX.
 *
 *  Both write the same key. A tab that wrote its whole in-memory list over
 *  whatever was there erased a send made in the other one — invariant 1 broken
 *  by the code that exists to keep it. So a write MERGES, and the rule is not
 *  "union everything", which would resurrect every send this tab deliberately
 *  removed after the server took it:
 *
 *      an entry this tab has NEVER SEEN belongs to another tab, and survives.
 *
 *  Ordered by `at`, the monotonic counter the reducer stamps, so two sends made
 *  in the same millisecond keep the order they were made in. */
export function mergeOutbox(
  stored: OutboxSend[], next: OutboxSend[], seen: Set<string>,
): OutboxSend[] {
  const mine = new Set(next.map((s) => s.clientId));
  const foreign = stored.filter((s) => !mine.has(s.clientId) && !seen.has(s.clientId));
  if (!foreign.length) return next;
  return [...next, ...foreign].sort((a, b) => (a.ts || "").localeCompare(b.ts || "") || a.at - b.at);
}

/** Everything this browser stores for a thread that no longer exists. */
export function forgetThread(threadKey: string): void {
  writeOutbox(threadKey, []);
}
