// Where dictated words are going: a field, a chat, or the clipboard.
//
// The destination is chosen when dictation starts (the field focused then, or
// the clipboard) and changes only when the operator changes it. A chat's
// composer is a chat, not an element, so its words keep going to that chat's
// draft after its page is left. A field keeps its identity, so a field that
// left the page can be found again when it comes back.
//
// The name is read from the field and the page it is on at the moment it
// becomes the destination (lib/speech/placeName.ts has the rule), so it still
// reads right after the operator has moved to another page.

import { openChats, type ChatTarget } from "../../../lib/speech/chatTargets";
import { sameIdentity, type FieldIdentity } from "../../../lib/speech/fieldIdentity";
import { currentPage, fieldName, placeName } from "../../../lib/speech/placeName";
import type { RecentPlace } from "../../../lib/speech/recentPlaces";
import { identityOf } from "./fieldFind";
import { documentShown } from "./frames";
import { captureTarget, type DictationTarget } from "./insertAtTarget";

export type FieldPlace = {
  kind: "field";
  /** Null for a field chosen from the recent list that is not on a page now. */
  target: DictationTarget | null;
  name: string;
  identity: FieldIdentity | null;
  /** The shell page it was on, for "Open". */
  href: string;
};
export type ChatPlace = { kind: "chat"; chat: ChatTarget; name: string };
export type ClipboardPlace = { kind: "clipboard"; name: string };
export type Destination = FieldPlace | ChatPlace | ClipboardPlace;

export const CLIPBOARD: ClipboardPlace = { kind: "clipboard", name: "Clipboard" };

function labelledBy(el: Element): string {
  const ids = el.getAttribute("aria-labelledby");
  if (!ids) return "";
  const doc = el.ownerDocument;
  return ids.split(/\s+/).map((id) => doc.getElementById(id)?.textContent ?? "").join(" ");
}

function nameOf(el: HTMLElement): string {
  const labels = (el as HTMLInputElement).labels;
  return fieldName({
    ariaLabel: el.getAttribute("aria-label"),
    labelledBy: labelledBy(el),
    label: labels && labels.length ? labels[0].textContent : null,
    placeholder: el.getAttribute("placeholder") ?? el.getAttribute("data-placeholder"),
    title: el.getAttribute("title"),
  });
}

/** The chat a composer belongs to, from the marks the chat puts on it. */
export function chatOfComposer(el: Element): ChatTarget | null {
  const host = el.closest("[data-dictation-chat]");
  const draftKey = host?.getAttribute("data-dictation-chat");
  if (!host || !draftKey) return null;
  const known = openChats().find((c) => c.draftKey === draftKey);
  if (known) return known;
  const sep = draftKey.lastIndexOf("::");
  return {
    draftKey,
    scope: sep > 0 ? draftKey.slice(0, sep) : draftKey,
    threadId: host.getAttribute("data-dictation-thread") || null,
    title: "Chat",
  };
}

export function chatPlace(chat: ChatTarget): ChatPlace {
  return { kind: "chat", chat, name: chat.title || "Chat" };
}

export function destinationFor(el: Element | null): FieldPlace | ChatPlace | null {
  const target = captureTarget(el);
  if (!target) return null;
  const chat = chatOfComposer(target.el);
  if (chat) return chatPlace(chat);
  return {
    kind: "field",
    target,
    name: placeName(nameOf(target.el), currentPage.get()),
    identity: identityOf(target.el),
    href: `${location.pathname}${location.search}`,
  };
}

/** Still somewhere words can go: on a page that is open, which an app page
 *  the shell keeps loaded in a hidden frame after it is left is not. */
export function present(t: DictationTarget | null): t is DictationTarget {
  return !!t && t.el.isConnected && !!t.el.ownerDocument.defaultView && documentShown(t.el.ownerDocument);
}

/** One chat is one place in every view that shows it: by conversation when it
 *  has one, since a view's draft key depends on the scope it resolved. */
export function sameChat(a: ChatTarget, b: ChatTarget): boolean {
  return a.threadId && b.threadId ? a.threadId === b.threadId : a.draftKey === b.draftKey;
}

export function samePlace(a: Destination | null, b: Destination | null): boolean {
  if (!a || !b || a.kind !== b.kind) return false;
  if (a.kind === "chat") return sameChat(a.chat, (b as ChatPlace).chat);
  if (a.kind === "field") {
    const f = b as FieldPlace;
    if (a.target && f.target) return a.target.el === f.target.el && samePage(a.identity, f.identity);
    return sameIdentity(a.identity, f.identity);
  }
  return true;
}

/** One element can show another field's content (an editor kept while its
 *  page moves on to another note), so the same element is the same field only
 *  on the same page under the same name. Its index among like-named fields is
 *  left out: it shifts as the page adds fields. */
export function samePage(a: FieldIdentity | null, b: FieldIdentity | null): boolean {
  return !a || !b || (a.path === b.path && a.key === b.key);
}

/** A key naming the place, the same for every element and view of it. */
export function placeKey(d: Destination): string {
  if (d.kind === "chat") return d.chat.threadId ? `chat:${d.chat.threadId}` : `chat:${d.chat.draftKey}`;
  if (d.kind === "field") return `field:${d.identity ? `${d.identity.path}|${d.identity.key}|${d.identity.index}` : d.name}`;
  return "clipboard";
}

/** A remembered place as a destination again. */
export function fromRecent(r: RecentPlace): FieldPlace | ChatPlace {
  if (r.kind === "chat") {
    return chatPlace({ draftKey: r.draftKey, scope: r.scope, threadId: r.threadId, title: r.name });
  }
  return { kind: "field", target: null, name: r.name, identity: r.identity, href: r.href };
}

/** How a destination is remembered in the bar's recent list, if it can be. */
export function recentOf(d: Destination): RecentPlace | null {
  if (d.kind === "chat") {
    const c = d.chat;
    return { kind: "chat", name: d.name, draftKey: c.draftKey, threadId: c.threadId, scope: c.scope };
  }
  if (d.kind === "field" && d.identity) return { kind: "field", name: d.name, identity: d.identity, href: d.href };
  return null;
}
