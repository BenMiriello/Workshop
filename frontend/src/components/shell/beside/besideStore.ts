// The chat beside the page: one store for the drawer, the chips waiting in its
// composer, and what was asked of it from outside (an app's attach, Add to chat
// on a selection). Module state behind useSyncExternalStore, like the speech
// layer's, because the readers are spread across the shell, the chat and the
// selection overlay with no common parent below the shell.

import { useSyncExternalStore } from "react";
import type { ContextItem } from "./contextItems";
import type { ChatView, Level } from "./protocol";
import { userStorage } from "../../../lib/storage";
import { arrive, sameConversation, type Drawer } from "./roam";

export type ComposerApi = {
  addFiles: (files: File[]) => void;
  setDraft: (text: string) => void;
  /** Send what is in the composer if it can be sent now; false when it cannot
   *  yet (an upload still going). */
  trySend: () => boolean;
  /** Send these files and chips as a message of their own, leaving the draft
   *  and anything else attached where they are. */
  sendOnly: (files: File[], items: ContextItem[]) => void;
  focus: () => void;
};

export type Intent = {
  /** The conversation to open, or null for the scope's own choice. */
  threadId: string | null;
  startNew: boolean;
  /** Changes on every request, so asking for the same thread twice still lands. */
  nonce: number;
};

/** Where "Open full screen" left from, so the full chat can put the
 *  conversation back beside that page. */
export type PoppedFrom = { path: string; label: string };

export type BesideState = {
  /** The shell place the drawer is showing for (`ShellPlace.id`). */
  place: string;
  open: boolean;
  /** The chat scope the drawer's conversation is filed under. */
  scope: string | null;
  intent: Intent;
  /** The conversation the drawer's chat is on, as the chat reports it. */
  thread: string | null;
  items: ContextItem[];
  level: Level;
  driving: boolean;
  /** How the operator sees the drawer's chat at work (follow, show, hide). */
  view: ChatView;
  /** What the page beside the chat is called, for the Access menu. */
  pageLabel: string;
  /** The chat scope a conversation beside this page belongs in. */
  pageScope: string;
  poppedFrom: PoppedFrom | null;
  /** The conversation was brought from another place (roam.ts) rather than
   *  chosen here, so this place does not keep it as its own. */
  roamed: boolean;
  /** The list of every chat is showing in the drawer, over the conversation. */
  chats: boolean;
  /** The chat went from a conversation to a new blank one, which `thread`
   *  alone cannot say: a blank chat has no id to travel with. */
  blank: boolean;
};

// WHAT SURVIVES A RELOAD: every place's drawer, and the one on screen when the
// page went away (`last`, with the place it was on), so the rule in roam.ts
// still runs on the first place the shell reports. Per device, like `lastBeside`.
const STATE_KEY = "ws-beside-state";
type Saved = { drawers: [string, Drawer][]; last: (Drawer & { place: string; roamed: boolean }) | null };

// A new blank chat is a conversation like any other: it has no thread id, so
// this says it explicitly. Without it the drawer re-opened "no thread" at the
// next place and the chat picked a running turn in its scope instead.
function isBlank(s: BesideState): boolean {
  return !s.thread && (s.blank || s.intent.startNew);
}

function leaving(s: BesideState): Drawer {
  const blank = isBlank(s);
  return {
    open: s.open, scope: s.scope, threadId: blank ? null : s.thread ?? s.intent.threadId,
    blank, filed: s.scope ?? s.pageScope,
  };
}

function readSaved(): Saved {
  try {
    const raw = JSON.parse(userStorage.local.getItem(STATE_KEY) || "null") as Saved | null;
    if (raw && Array.isArray(raw.drawers)) return { drawers: raw.drawers, last: raw.last ?? null };
  } catch { /* unreadable or no storage: start closed */ }
  return { drawers: [], last: null };
}

// Read when the shell first names a place, not when this module loads: storage
// is keyed by the signed-in user, who is not known yet at load.
let restored: Saved["last"] = null;
let savedJson = "";

let state: BesideState = {
  place: "", open: false, scope: null, intent: { threadId: null, startNew: false, nonce: 0 },
  thread: null, items: [], level: "send", driving: false, view: "follow", pageLabel: "", pageScope: "general",
  poppedFrom: null, roamed: false, chats: false, blank: false,
};

// EACH PLACE HAS ITS OWN DRAWER. Opening the chat beside one app opens it
// there, not everywhere: leaving for another place shows that place's drawer
// (closed unless it was opened there), and coming back shows this one as it
// was left. Two things take a conversation along: a link followed from inside
// the chat (`carry`), and a conversation filed under Development or the
// Workshop root, which roams between places (roam.ts has the rule).
type PlaceDrawer = Drawer;
const drawers = new Map<string, PlaceDrawer>();
let carry: PlaceDrawer | null = null;
const listeners = new Set<() => void>();
let composer: ComposerApi | null = null;
// Asked for before the chat was ready to take it: applied when it binds.
let queued: { files: File[]; draft: string | null; send: boolean } = { files: [], draft: null, send: false };
let queuedSends: { files: File[]; items: ContextItem[] }[] = [];

function persist() {
  if (!state.place) return;
  const last = { ...leaving(state), place: state.place, roamed: state.roamed };
  const json = JSON.stringify({ drawers: [...drawers].slice(-40), last } satisfies Saved);
  if (json === savedJson) return;
  savedJson = json;
  try { userStorage.local.setItem(STATE_KEY, json); } catch { /* storage full or absent */ }
}

function set(patch: Partial<BesideState>) {
  state = { ...state, ...patch };
  persist();
  listeners.forEach((l) => l());
}

export const beside = {
  get: () => state,
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  open(opts: { scope?: string | null; threadId?: string | null; startNew?: boolean } = {}) {
    const intent = (opts.threadId || opts.startNew || opts.scope !== undefined)
      ? { threadId: opts.threadId ?? null, startNew: !!opts.startNew, nonce: state.intent.nonce + 1 }
      : state.intent;
    const chosen = !!opts.threadId || !!opts.startNew || (opts.scope !== undefined && opts.scope !== state.scope);
    set({
      open: true, scope: opts.scope !== undefined ? opts.scope : state.scope, intent,
      roamed: chosen ? false : state.roamed,
      chats: chosen ? false : state.chats,
      blank: opts.threadId ? false : state.blank,
    });
  },
  /** Show or hide the list of every chat over the drawer's conversation. */
  showChats(chats: boolean) {
    if (chats !== state.chats) set({ chats });
  },
  /** Closing a conversation closes it wherever else it was left open, so a
   *  roaming chat does not come back at the place it started from. */
  close() {
    const id = state.thread ?? state.intent.threadId;
    if (id) drawers.forEach((d, k) => { if (d.threadId === id) drawers.set(k, { ...d, open: false }); });
    set({ open: false, driving: false, chats: false });
  },
  /** The shell moved to another place: keep this one's drawer, show that one's. */
  setPlace(place: string) {
    if (place === state.place) return;
    // The first place after a load has no drawer yet: what was on screen before
    // it stands in as the one being left.
    const first = !state.place;
    if (first) {
      const saved = readSaved();
      saved.drawers.forEach(([k, d]) => drawers.set(k, d));
      restored = saved.last;
      // The drawer on screen is its place's own unless it was brought from another.
      if (restored && !restored.roamed) drawers.set(restored.place, restored);
    }
    const left: Drawer = first && restored ? restored : leaving(state);
    const leftRoamed = first ? !!restored?.roamed : state.roamed;
    restored = null;
    if (!first && !leftRoamed) drawers.set(state.place, left);
    const arrived = carry ? { drawer: carry, roamed: false } : arrive(left, drawers.get(place));
    carry = null;
    const next = arrived.drawer;
    // The same conversation stays mounted: a new nonce restarts the chat, and
    // a blank one would lose what was being typed into it.
    const stays = !first && sameConversation(left, next) && state.intent.startNew === !!next.blank;
    set({
      place, open: next.open, scope: next.scope, thread: stays ? state.thread : null, driving: false,
      roamed: arrived.roamed, chats: false, blank: !!next.blank,
      intent: { threadId: next.threadId, startNew: !!next.blank, nonce: state.intent.nonce + (stays ? 0 : 1) },
    });
  },
  /** The next place the shell moves to opens its drawer on this conversation. */
  carry(threadId: string | null, scope: string | null) {
    carry = { open: true, scope, threadId, blank: !threadId && isBlank(state), filed: scope ?? state.pageScope };
  },
  /** A navigation that did not change place spends nothing carried. */
  dropCarry() {
    carry = null;
  },
  setPoppedFrom(poppedFrom: PoppedFrom | null) {
    if (poppedFrom?.path !== state.poppedFrom?.path) set({ poppedFrom });
  },
  setThread(thread: string | null) {
    if (thread === state.thread) return;
    // Another conversation than the one brought here is a choice made here.
    const chosen = state.roamed && !!thread && !!state.intent.threadId && thread !== state.intent.threadId
      || state.roamed && !thread && !!state.thread;
    // Going from a conversation to nothing is a new chat; any thread is not.
    const blank = !thread && (!!state.thread || state.blank);
    set({ thread, blank, roamed: chosen ? false : state.roamed });
  },
  setLevel(level: Level) {
    set({ level, driving: level === "drive" ? state.driving : false });
  },
  setView(view: ChatView) {
    if (view !== state.view) set({ view });
  },
  setPage(pageLabel: string, pageScope: string) {
    if (pageLabel !== state.pageLabel || pageScope !== state.pageScope) set({ pageLabel, pageScope });
  },
  setDriving(driving: boolean) {
    if (driving !== state.driving) set({ driving });
  },
  addItems(items: ContextItem[]) {
    if (items.length) set({ items: [...state.items, ...items] });
  },
  removeItem(id: string) {
    set({ items: state.items.filter((i) => i.id !== id) });
  },
  /** The waiting chips, cleared: called by the send that carries them. */
  takeItems(): ContextItem[] {
    const items = state.items;
    if (items.length) set({ items: [] });
    return items;
  },
  bindComposer(api: ComposerApi | null) {
    composer = api;
    if (api) flush();
  },
  /** Hand files and text to the drawer's composer, now or when it binds, and
   *  send once it can when `send` is set. */
  toComposer(files: File[], draft: string | null, send: boolean) {
    queued = {
      files: [...queued.files, ...files],
      draft: draft ?? queued.draft,
      send: send || queued.send,
    };
    if (composer) flush();
  },
  /** Send files and chips as a message of their own, now or when the drawer's
   *  composer binds. Nothing the composer holds goes with them. */
  sendOnly(files: File[], items: ContextItem[]) {
    queuedSends = [...queuedSends, { files, items }];
    if (composer) flush();
  },
};

const SEND_WAIT_MS = 30000;

function flush() {
  const api = composer;
  if (!api) return;
  const sends = queuedSends;
  queuedSends = [];
  for (const s of sends) api.sendOnly(s.files, s.items);
  const { files, draft, send } = queued;
  queued = { files: [], draft: null, send: false };
  if (files.length) api.addFiles(files);
  if (draft !== null) api.setDraft(draft);
  if (!send) {
    if (files.length || draft !== null) api.focus();
    return;
  }
  // An image is still uploading when it was just handed over, and a send then
  // would leave it behind, so keep trying until the composer can send.
  const started = Date.now();
  const attempt = () => {
    if (composer !== api || api.trySend()) return;
    if (Date.now() - started < SEND_WAIT_MS) window.setTimeout(attempt, 250);
  };
  window.setTimeout(attempt, 50);
}

export function useBeside(): BesideState {
  return useSyncExternalStore(beside.subscribe, beside.get, beside.get);
}

let itemSeq = 0;
export function itemId(): string {
  itemSeq += 1;
  return `ctx-${Date.now().toString(36)}-${itemSeq}`;
}
