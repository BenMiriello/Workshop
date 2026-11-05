// Chat integration for dictation, behind one module. The speech layer treats
// a chat as a destination that outlives navigation: words go live into its
// composer when that is on screen, and onto its draft when it is not. See
// "Where dictated words go" in docs/dictation-and-clipboard.md.

import { beside } from "../../components/shell/beside/besideStore";
import { openChatStore } from "../../components/chat/openChats";
import { draftKeyFor } from "../../components/chat/composerDraft";
import { appendDraft } from "../draftWire";
import { chatApi } from "../chat";
import {
  findOpen, newChatTarget, targetOf, viewsOf, type ChatTarget,
} from "./chatTargetsCore";

export type { ChatTarget } from "./chatTargetsCore";
export { chatHref } from "./chatTargetsCore";
export {
  cancelSend, sendChat, type CancelOutcome, type SendHandle, type SendResult,
} from "./chatTargetsSend";
export { chatBusy, draftOf, holdSend, inRunningReply, putBackHeld, sendHeld, type Held } from "./chatTargetsHeld";
export { chatModels, modelNow, restoreModel, setChatModel, type ModelPick } from "./chatTargetsPick";

/** The chats open in this tab: the main view first, then the side panel. */
export function openChats(): ChatTarget[] {
  const rank = { main: 0, beside: 1, other: 2 } as const;
  const seen = new Set<string>();
  const idOf = (t: ChatTarget) => t.threadId ?? t.draftKey;
  return [...openChatStore.list()]
    .filter((c) => c.where !== "other")
    .sort((a, b) => rank[a.where] - rank[b.where])
    .map(targetOf)
    .filter((t) => (seen.has(idOf(t)) ? false : (seen.add(idOf(t)), true)));
}

const shown = (el: HTMLElement | null | undefined): el is HTMLElement => (
  !!el && el.isConnected && el.getClientRects().length > 0
);

/** Its composer's text box, when one is on screen: the main view's, else the
 *  side panel's. */
export function composerFor(t: ChatTarget): HTMLElement | null {
  const views = viewsOf(t).sort((a, b) => (a.where === "main" ? -1 : b.where === "main" ? 1 : 0));
  for (const v of views) {
    const input = v.composer.input();
    if (shown(input)) return input;
  }
  return null;
}

/** Which view shows the chat's composer now, if one does. */
export function shownIn(t: ChatTarget): "main" | "beside" | null {
  const el = composerFor(t);
  const v = el && viewsOf(t).find((c) => c.composer.input() === el);
  return v && v.where !== "other" ? v.where : null;
}

/** The draft its views keep: a mounted view's, whose scope may differ from
 *  the one the destination was named with. */
export function draftKeyOf(t: ChatTarget): string {
  return findOpen(t)?.draftKey ?? t.draftKey;
}

/** Words onto the end of the chat's draft: live in any composer showing it,
 *  in this tab or another, and on the server for other devices. */
export function appendToDraft(t: ChatTarget, text: string): void {
  if (!text) return;
  void appendDraft(draftKeyOf(t), text).catch(() => {});
}

/** A new chat in `scope`: its draft is the destination, and the chat is
 *  created on its first send, as the composer does. A view showing `from`
 *  switches to it. */
export function startNewChat(scope: string, from?: ChatTarget | null): ChatTarget {
  if (from && from.scope === scope) viewsOf(from).forEach((c) => c.composer.startNew());
  return newChatTarget(scope);
}

/** Every view showing `from` shows `to` instead: undoing, or redoing, a
 *  switch to a new chat. */
export function switchChat(from: ChatTarget, to: ChatTarget): void {
  viewsOf(from).forEach((c) => (
    to.threadId ? c.composer.openThread(to.threadId, to.scope) : c.composer.startNew()
  ));
}

/** Show the chat in the side panel, unless a view already shows it. */
export function openChat(t: ChatTarget): void {
  if (composerFor(t)) return;
  beside.open(t.threadId ? { scope: t.scope, threadId: t.threadId } : { scope: t.scope, startNew: true });
}

/** What the side panel shows now, to put back with `restorePanel`. */
export type PanelShown = { open: boolean; scope: string | null; threadId: string | null };

export function panelNow(): PanelShown {
  const s = beside.get();
  return { open: s.open, scope: s.scope, threadId: s.thread ?? s.intent.threadId };
}

export function restorePanel(p: PanelShown): void {
  if (!p.open) beside.close();
  else beside.open(p.threadId ? { scope: p.scope, threadId: p.threadId } : { scope: p.scope });
}

const RECENT = 20;

/** The chat search behind "Choose a chat": titles first, then messages. An
 *  empty query lists the most recent chats. */
export async function searchChats(q: string): Promise<ChatTarget[]> {
  const query = q.trim();
  const [hits, list] = await Promise.all([
    query ? chatApi.searchThreads(query) : Promise.resolve(null),
    chatApi.listThreads(),
  ]);
  const byId = new Map(list.map((t) => [t.id, t]));
  const rows = hits ? hits.map((h) => byId.get(h.id)).filter((t) => !!t) : list.slice(0, RECENT);
  return rows.map((t) => ({
    draftKey: draftKeyFor(t.context_key, t.id),
    scope: t.context_key,
    threadId: t.id,
    title: t.title || "Untitled chat",
  }));
}
