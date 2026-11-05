// THE CHATS ON SCREEN, and a handle on each one's composer.
//
// Every mounted Chat registers here: the main view (the full chat page), the
// side panel, and any other surface. Before this the main view's conversation
// lived only in that component and in `?thread=`, so nothing outside it (the
// dictation bar) could ask which chat was open or send what its composer held.
// Module state behind useSyncExternalStore, like besideStore.

import { useSyncExternalStore } from "react";
import type { DraftAttachment } from "../../lib/draftWire";
import type { SendOutcome } from "./useSendPipeline";

/** Where a chat is shown. `main` is the full chat page; `beside` the side
 *  panel; `other` a chat inside some other page or frame. */
export type ChatWhere = "main" | "beside" | "other";

/** One send made through a composer. */
export type ComposerSend = { clientId: string; done: Promise<SendOutcome> };

export type OpenComposer = {
  /** What the send button does. Null when it would send nothing (an empty box,
   *  an upload still going, the chat still loading). */
  send: () => ComposerSend | null;
  getDraft: () => { text: string; attachments: DraftAttachment[] };
  setDraft: (attachments: DraftAttachment[], text: string) => void;
  /** Give up on a send the server never took, and drop its bubble. */
  dropSend: (clientId: string) => void;
  /** Read the log again (after a go-back or a queue change made elsewhere). */
  resync: () => void;
  /** The conversation this composer is on right now, read through a ref. */
  threadId: () => string | null;
  model: () => string;
  modelOptions: () => string[];
  /** What the model picker does on change. */
  setModel: (id: string) => void;
  /** Switch this view to a blank chat in its own scope. */
  startNew: () => void;
  /** Switch this view to a conversation. */
  openThread: (threadId: string, contextKey: string) => void;
  input: () => HTMLTextAreaElement | null;
};

export type OpenChat = {
  mountId: number;
  where: ChatWhere;
  contextKey: string;
  scope: string;
  threadId: string | null;
  draftKey: string;
  title: string;
  composer: OpenComposer;
};

let chats: OpenChat[] = [];
const listeners = new Set<() => void>();
let nextMount = 0;

function emit() { listeners.forEach((l) => l()); }

export const openChatStore = {
  list: (): OpenChat[] => chats,
  subscribe(fn: () => void) {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
  mountId: () => ++nextMount,
  /** Add or replace this mount's entry. Replacing keeps its place in the list. */
  put(entry: OpenChat) {
    const i = chats.findIndex((c) => c.mountId === entry.mountId);
    const prev = i >= 0 ? chats[i] : null;
    if (prev && sameEntry(prev, entry)) return;
    chats = i >= 0 ? chats.map((c, k) => (k === i ? entry : c)) : [...chats, entry];
    emit();
  },
  remove(mountId: number) {
    if (!chats.some((c) => c.mountId === mountId)) return;
    chats = chats.filter((c) => c.mountId !== mountId);
    emit();
  },
  /** The full chat page's conversation, when that page is open. */
  main: (): OpenChat | null => chats.find((c) => c.where === "main") ?? null,
  byDraftKey: (key: string): OpenChat | null => chats.find((c) => c.draftKey === key) ?? null,
};

function sameEntry(a: OpenChat, b: OpenChat): boolean {
  return a.where === b.where && a.contextKey === b.contextKey && a.scope === b.scope
    && a.threadId === b.threadId && a.draftKey === b.draftKey && a.title === b.title
    && a.composer === b.composer;
}

export function useOpenChats(): OpenChat[] {
  return useSyncExternalStore(openChatStore.subscribe, openChatStore.list, openChatStore.list);
}
