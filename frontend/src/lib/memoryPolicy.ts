import { shortHttpError } from "./httpError";
// What other chats SEE and SEARCH (memory-and-documentation-model-2026-08-14.md
// sec 6). This is CURATION, not security: everything on this box runs as one
// uid, so nothing here restricts what any process can read. It keeps a chat's
// recall about that chat's own work instead of filling it with a sibling
// project's transcripts. Never label any of it permission or access control.

export type ShareValue = "inherit" | "open" | "private";
export const SHARE_VALUES: ShareValue[] = ["inherit", "open", "private"];

// Written at the control, so the wording cannot drift away from the meaning.
// Item-level phrasing ("this"): used on one specific note/doc/chat, where
// "this" has a clear referent. Settings' page of DEFAULTS writes its own
// hints instead of reusing these, since a default has no single "this".
export const SHARE_HELP: Record<ShareValue, string> = {
  inherit: "Chats in this scope, and any scope above it, can find this.",
  open: "Any chat, in any scope, can find this.",
  private: "Only chats in this exact scope can find this.",
};

export const SHARE_LABEL: Record<ShareValue, string> = {
  inherit: "Its scope and above",
  open: "Every scope",
  private: "Its scope only",
};

export type MemoryPolicy = {
  version: number;
  defaults: Record<string, ShareValue>;
  scopes: Record<string, ShareValue>;
  items: Record<string, ShareValue>;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return r.json() as Promise<T>;
}

export async function getPolicy(): Promise<MemoryPolicy> {
  return j<MemoryPolicy>(await fetch("/api/memory-policy"));
}

export async function setDefaults(defaults: Record<string, ShareValue>): Promise<MemoryPolicy> {
  return j<MemoryPolicy>(await fetch("/api/memory-policy/defaults", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ defaults }),
  }));
}

export async function setScopes(scopes: Record<string, ShareValue | null>): Promise<MemoryPolicy> {
  return j<MemoryPolicy>(await fetch("/api/memory-policy/scopes", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ scopes }),
  }));
}

// `null` clears the override and falls the item back to its scope, then to the
// global default for its kind.
export async function setItem(ref: string, share: ShareValue | null): Promise<MemoryPolicy> {
  return j<MemoryPolicy>(await fetch("/api/memory-policy/item", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ref, share: share === "inherit" ? null : share }),
  }));
}

// A chat's policy key is the whole transcript, with no message anchor: sharing
// is a property of the conversation, not of one message in it.
export function chatItemRef(threadId: string): string {
  return `workshop:chat/${threadId}`;
}

export function noteItemRef(scope: string, noteId: string): string {
  return `workshop:note/${scope}/${noteId}`;
}

// What actually applies to an item right now, narrowest wins: the item's own
// value, then its scope's, then the global default for its kind.
export function effectiveShare(
  policy: MemoryPolicy | null,
  kind: string,
  scope?: string,
  itemRef?: string,
): ShareValue {
  if (!policy) return "inherit";
  if (itemRef && policy.items[itemRef]) return policy.items[itemRef];
  if (scope && policy.scopes[scope]) return policy.scopes[scope];
  return policy.defaults[kind] ?? "inherit";
}
