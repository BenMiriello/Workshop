// The three copies of a draft and how they travel: localStorage for an instant
// restore with the backend down, a BroadcastChannel so two tabs agree without a
// round trip, and the server as the durable cross-device copy and the arbiter.
// The rules that decide which copy wins live in draftSync.ts.

import { HttpError, withTransientRetry } from "@workshop/ui";
import { userStorage } from "./storage";
import { joinAppend, swapTail } from "./speech/chatTargetsText";

/** A file already uploaded and sitting in the composer. */
export type DraftAttachment = { id: string; name: string; kind: "image" | "pdf" | "text" };

// A DRAFT IS EVERYTHING IN THE COMPOSER, text and files together. They are one
// record because they are one thing to the operator: what they have written and
// not sent. Keeping the files in React state while the text was persisted meant
// a reload brought back a message about a screenshot that was no longer
// attached, and left the uploaded file orphaned on the server.
export type DraftPayload = {
  key: string; text: string; ts: number; client_id: string;
  attachments?: DraftAttachment[];
  settings?: DraftSettings;
  /** Set on a write from outside the composer (dictation into a chat that is
   *  not on screen): the words added to the end. A composer applies them to
   *  what it holds, even mid-typing, rather than adopting `text`. */
  append?: string;
};

/** What was picked in a blank chat before any message exists. A field that is
 *  absent was never chosen, which is not the same as the default's value. */
export type DraftSettings = {
  model?: string; effort?: string; permission_mode?: string; mode?: string;
};

const LS_PREFIX = "ws-chat-draft:";
export const ACTIVE_MS = 3000; // "actively editing" window after a keystroke
export const BROADCAST_DEBOUNCE_MS = 120;
export const SERVER_DEBOUNCE_MS = 600;

// Stable per-tab id — one per page load is enough to suppress self-echo.
export const CLIENT_ID = Math.random().toString(36).slice(2) + Date.now().toString(36);

export const channel: BroadcastChannel | null =
  typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("ws-chat-drafts") : null;

// Per-key subscribers — each mounted chat surface for that key.
export type Sub = (p: DraftPayload) => void;
export const subs = new Map<string, Set<Sub>>();

export function emitLocal(p: DraftPayload) {
  subs.get(p.key)?.forEach((fn) => fn(p));
}

if (channel) {
  channel.onmessage = (e) => {
    const p = e.data as DraftPayload;
    if (p && p.key) emitLocal(p);
  };
}

export type DraftValue = {
  text: string; ts: number; attachments: DraftAttachment[]; settings: DraftSettings;
};

export function lsRead(key: string): DraftValue {
  try {
    const raw = userStorage.local.getItem(LS_PREFIX + key);
    if (raw) {
      const v = JSON.parse(raw);
      if (typeof v?.text === "string") {
        return {
          text: v.text,
          ts: Number(v.ts) || 0,
          attachments: Array.isArray(v.attachments) ? v.attachments : [],
          settings: cleanSettings(v.settings),
        };
      }
    }
  } catch { /* ignore */ }
  return { text: "", ts: 0, attachments: [], settings: {} };
}

const SETTING_KEYS = ["model", "effort", "permission_mode", "mode"] as const;

/** Only the string fields that carry a value. A blank is "never chosen". */
export function cleanSettings(raw: unknown): DraftSettings {
  const out: DraftSettings = {};
  if (!raw || typeof raw !== "object") return out;
  for (const k of SETTING_KEYS) {
    const v = (raw as Record<string, unknown>)[k];
    if (typeof v === "string" && v) out[k] = v;
  }
  return out;
}

export function lsWrite(
  key: string, text: string, ts: number, attachments: DraftAttachment[] = [],
  settings: DraftSettings = {},
) {
  // A CLEAR IS STORED, NOT DELETED. The timestamp is what makes "this was
  // cleared at T" comparable with a write from before T; removing the row
  // threw that away, so a late write looked like the first thing anyone had
  // said about this key and won.
  userStorage.local.setItem(LS_PREFIX + key, JSON.stringify({ text, ts, attachments, settings }));
}

export async function serverGet(key: string): Promise<DraftValue | null> {
  try {
    const r = await fetch(`/api/chat/drafts/${encodeURIComponent(key)}`);
    if (!r.ok) return null;
    const v = await r.json();
    return {
      text: typeof v?.text === "string" ? v.text : "",
      ts: Number(v?.ts) || 0,
      attachments: Array.isArray(v?.attachments) ? v.attachments : [],
      settings: cleanSettings(v?.settings),
    };
  } catch { return null; }
}

export function putDraft(p: DraftPayload): Promise<Response> {
  return fetch(`/api/chat/drafts/${encodeURIComponent(p.key)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: p.text, ts: p.ts, client_id: p.client_id,
      attachments: p.attachments || [],
      ...(p.settings && Object.keys(p.settings).length ? { settings: p.settings } : {}),
    }),
    keepalive: true,
  }).then((r) => {
    // Typed so the retry below can tell a restart window from a real refusal.
    // A keystroke write never looks at this; a CLEAR does.
    if (!r.ok) throw new HttpError(r.status, `draft PUT ${r.status}`);
    return r;
  });
}

export function serverPut(p: DraftPayload) {
  try {
    void putDraft(p).catch(() => {});
  } catch { /* offline — localStorage already holds it */ }
}

/** THE CLEAR IS THE ONE DRAFT WRITE NOTHING SUPERSEDES.
 *
 *  Every other write here is fire-and-forget on purpose: a keystroke write that
 *  is lost is replaced by the next keystroke 600ms later, so retrying it would
 *  be spending requests on a value that is already stale. A CLEAR has no next
 *  write. If it is dropped, the server keeps holding text the operator sent, at
 *  a timestamp older than this browser's tombstone -- invisible here, because
 *  localStorage remembers the clear, and visible on ANY OTHER SURFACE, which
 *  adopts it as the newest thing anyone said about that composer.
 *
 *  That is the standing report: "I often will send a message in chat then the
 *  text remains in the input even after it's submitted even days later." The
 *  server's own clear-on-accept covers the send that lands; this covers the one
 *  that does not, and the restart window in which neither request gets through.
 *  Same schedule as every other retry in the app. */
export function serverClear(p: DraftPayload) {
  try {
    void withTransientRetry(() => putDraft(p)).catch(() => {});
  } catch { /* offline — the local tombstone still stands on this device */ }
}


// ---- Writes from outside a composer -----------------------------------------
// Dictation into a chat whose composer is not on screen writes the draft here.
// Every copy is updated at once: this tab's composers through `emitLocal`,
// other tabs through the channel, other devices through the server.

/** Same-tab composers adopt a value with this id; their own id is an echo. */
const OUTSIDE_ID = `${CLIENT_ID}:outside`;
const appendChains = new Map<string, Promise<void>>();

/** The newest copy: this browser's, or the server's when it is newer. */
export async function readDraftNewest(key: string): Promise<DraftValue> {
  const local = lsRead(key);
  const s = await serverGet(key);
  return s && s.ts > local.ts ? s : local;
}

function publish(p: DraftPayload) {
  lsWrite(p.key, p.text, p.ts, p.attachments, p.settings);
  emitLocal(p);
  channel?.postMessage(p);
  serverPut(p);
}

/** Add words to the end of a draft. Appends to one key are applied in order.
 *  With a composer for the key mounted in this tab, localStorage is already
 *  the newest copy (every keystroke writes it); otherwise the server's copy is
 *  read first, since another device may have typed since. */
export function appendDraft(key: string, add: string): Promise<void> {
  const prev = appendChains.get(key) ?? Promise.resolve();
  const next = prev.then(async () => {
    const base = subs.has(key) ? lsRead(key) : await readDraftNewest(key);
    publish({
      key, text: joinAppend(base.text, add), ts: Math.max(Date.now(), base.ts + 1),
      client_id: CLIENT_ID, attachments: base.attachments, settings: base.settings, append: add,
    });
  });
  const settled = next.catch(() => {});
  appendChains.set(key, settled);
  void settled.then(() => { if (appendChains.get(key) === settled) appendChains.delete(key); });
  return next;
}

/** Swap the words at the end of a draft, `was`, for `now`: dictation still
 *  being said into a chat that is not on screen, rewritten as it is revised.
 *  When the draft no longer ends with `was` (someone edited it), `now` is
 *  added instead. Ordered with appends to the same key. */
export function replaceDraftTail(key: string, was: string, now: string): Promise<void> {
  const prev = appendChains.get(key) ?? Promise.resolve();
  const next = prev.then(async () => {
    const base = subs.has(key) ? lsRead(key) : await readDraftNewest(key);
    const text = swapTail(base.text, was, now);
    if (text === base.text) return;
    publish({
      key, text, ts: Math.max(Date.now(), base.ts + 1),
      client_id: CLIENT_ID, attachments: base.attachments, settings: base.settings,
    });
  });
  const settled = next.catch(() => {});
  appendChains.set(key, settled);
  void settled.then(() => { if (appendChains.get(key) === settled) appendChains.delete(key); });
  return next;
}

/** Resolves once every append asked for so far has been written. */
export function draftAppendsSettled(key: string): Promise<void> {
  return appendChains.get(key) ?? Promise.resolve();
}

/** Replace a draft from outside its composer. A composer typing right now
 *  keeps its own text, as it does for any other outside copy. */
export function writeDraftOutside(key: string, v: Omit<DraftValue, "ts">) {
  publish({
    key, text: v.text, ts: Math.max(Date.now(), lsRead(key).ts + 1), client_id: OUTSIDE_ID,
    attachments: v.attachments, settings: v.settings,
  });
}
