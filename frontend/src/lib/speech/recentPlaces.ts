// The places words recently went, newest first, kept per device for the
// dictation bar's menu: fields by their identity, chats by their draft.

import { userStorage } from "../storage";
import { sameIdentity, type FieldIdentity } from "./fieldIdentity";

export type RecentField = { kind: "field"; name: string; identity: FieldIdentity; href: string };
export type RecentChat = { kind: "chat"; name: string; draftKey: string; threadId: string | null; scope: string };
export type RecentPlace = RecentField | RecentChat;

export const RECENT_MAX = 8;
const KEY = "speech.recent";

function samePlace(a: RecentPlace, b: RecentPlace): boolean {
  if (a.kind === "chat" && b.kind === "chat") return a.threadId && b.threadId ? a.threadId === b.threadId : a.draftKey === b.draftKey;
  return a.kind === "field" && b.kind === "field" && sameIdentity(a.identity, b.identity);
}

export function addRecent(list: RecentPlace[], place: RecentPlace): RecentPlace[] {
  return [place, ...list.filter((p) => !samePlace(p, place))].slice(0, RECENT_MAX);
}

function isPlace(v: unknown): v is RecentPlace {
  const p = v as RecentPlace;
  if (!p || typeof p.name !== "string") return false;
  if (p.kind === "chat") return typeof p.draftKey === "string" && typeof p.scope === "string";
  return p.kind === "field" && !!p.identity && typeof p.identity.path === "string"
    && typeof p.identity.key === "string" && typeof p.href === "string";
}

export function parseRecent(raw: string | null): RecentPlace[] {
  try {
    const v = JSON.parse(raw ?? "[]");
    return Array.isArray(v) ? v.filter(isPlace).slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export const recentPlaces = {
  get: (): RecentPlace[] => parseRecent(userStorage.local.getItem(KEY)),
  add(place: RecentPlace): void {
    userStorage.local.setItem(KEY, JSON.stringify(addRecent(recentPlaces.get(), place)));
  },
};
