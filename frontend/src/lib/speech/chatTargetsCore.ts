// What a chat is to the speech layer, and how to find the one on screen.
// Shared by chatTargets (the public module) and its send and model halves.

import { draftKeyFor } from "../../components/chat/composerDraft";
import { openChatStore, type OpenChat } from "../../components/chat/openChats";
import { newChatHref } from "../chatSelection";

/** A chat as a destination: its draft, the scope it is filed under, its
 *  conversation (null for a new chat, which is created on its first send),
 *  and what the bar calls it. */
export type ChatTarget = { draftKey: string; scope: string; threadId: string | null; title: string };

export const NEW_CHAT_TITLE = "New chat";

export function targetOf(c: OpenChat): ChatTarget {
  return { draftKey: c.draftKey, scope: c.scope, threadId: c.threadId, title: c.title };
}

export function newChatTarget(scope: string): ChatTarget {
  return { draftKey: draftKeyFor(scope, null), scope, threadId: null, title: NEW_CHAT_TITLE };
}

/** The same chat once it has a conversation. */
export function withThread(t: ChatTarget, threadId: string | null): ChatTarget {
  if (!threadId || threadId === t.threadId) return t;
  return { ...t, threadId, draftKey: draftKeyFor(t.scope, threadId) };
}

/** The page that shows this chat. */
export function chatHref(t: ChatTarget): string {
  return t.threadId
    ? `/chat/${encodeURIComponent(t.scope)}?thread=${encodeURIComponent(t.threadId)}`
    : newChatHref(t.scope);
}

/** The views showing this chat in this tab: by conversation when it has one
 *  (its draft key depends on the scope the view resolved), else by draft. */
export function viewsOf(t: ChatTarget): OpenChat[] {
  return openChatStore.list().filter((c) => (
    t.threadId ? c.threadId === t.threadId : c.draftKey === t.draftKey
  ));
}

/** This chat's composer, when a view of it is mounted. */
export function findOpen(t: ChatTarget): OpenChat | null {
  const views = viewsOf(t);
  return views.find((c) => c.where === "main") ?? views[0] ?? null;
}
