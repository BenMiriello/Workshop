// What a VersionedDocument keeps on this device: which device it is, and the
// typing not yet confirmed by the source, together with the text it was typed
// on, so it can be merged into whatever was saved since rather than shown over
// it.
import { userStorage } from "./storage";
import { merge3, mergeField, mergeTags, type MergePrefer } from "./documentMerge";
import type { DocumentDraft } from "./documentSource";

export interface DeviceInfo {
  /** Stable for this browser, so the source can group one device's saves. */
  id: string;
  /** How the device is named in history: "iPhone", "Mac · Firefox". */
  label: string;
}

export interface Unsent {
  draft: DocumentDraft;
  /** The version and text the draft was typed on. */
  base: number;
  baseDraft: DocumentDraft | null;
  at: string;
}

const UNSENT_PREFIX = "ws-vdoc:";
const DEVICE_KEY = "ws-device-id";

export const sameDraft = (a: DocumentDraft, b: DocumentDraft): boolean =>
  a.title === b.title && a.body === b.body
  && a.tags.length === b.tags.length && a.tags.every((t, i) => t === b.tags[i]);

export const draftOf = (d: DocumentDraft): DocumentDraft => ({ title: d.title, body: d.body, tags: [...d.tags] });

/** `mine` and `theirs`, both edited from `base`, as one draft, and how many
 *  places both changed differently (`prefer` says whose is kept in those). */
export function mergeDrafts(
  base: DocumentDraft, mine: DocumentDraft, theirs: DocumentDraft, prefer: MergePrefer = "mine",
): { draft: DocumentDraft; conflicts: number } {
  const title = mergeField(base.title, mine.title, theirs.title, prefer);
  const body = merge3(base.body, mine.body, theirs.body, prefer);
  return {
    draft: { title: title.text, body: body.text, tags: mergeTags(base.tags, mine.tags, theirs.tags) },
    conflicts: title.conflicts + body.conflicts,
  };
}

export function deviceLabel(ua = typeof navigator === "undefined" ? "" : navigator.userAgent,
  touch = typeof navigator === "undefined" ? 0 : navigator.maxTouchPoints): string {
  const platform = /iPhone/.test(ua) ? "iPhone"
    : /iPad/.test(ua) || (/Macintosh/.test(ua) && touch > 1) ? "iPad"
      : /Android/.test(ua) ? "Android"
        : /Macintosh/.test(ua) ? "Mac"
          : /Windows/.test(ua) ? "Windows"
            : /Linux|CrOS/.test(ua) ? "Linux" : "";
  const browser = /Firefox|FxiOS/.test(ua) ? "Firefox"
    : /Edg\//.test(ua) ? "Edge"
      : /Chrome|CriOS/.test(ua) ? "Chrome"
        : /Safari/.test(ua) && !/iPhone|iPad|Android/.test(ua) ? "Safari" : "";
  return [platform, browser].filter(Boolean).join(" · ") || "this device";
}

export function thisDevice(): DeviceInfo {
  let id = userStorage.local.getItem(DEVICE_KEY);
  if (!id) {
    id = typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
    userStorage.local.setItem(DEVICE_KEY, id);
  }
  return { id, label: deviceLabel() };
}

export function readUnsent(key: string): Unsent | null {
  try {
    const raw = userStorage.local.getItem(UNSENT_PREFIX + key);
    if (!raw) return null;
    const v = JSON.parse(raw);
    // An entry written before the base text was kept holds the draft at the
    // top level and only the base's number.
    if (!v.draft) return { draft: draftOf(v), base: v.base ?? 0, baseDraft: null, at: v.savedAt ?? "" };
    return v as Unsent;
  } catch {
    return null;
  }
}

export function writeUnsent(key: string, draft: DocumentDraft, base: number, baseDraft: DocumentDraft) {
  try {
    const v: Unsent = { draft, base, baseDraft, at: new Date().toISOString() };
    userStorage.local.setItem(UNSENT_PREFIX + key, JSON.stringify(v));
  } catch {
    /* storage full or blocked: the next save still carries the text */
  }
}

export function dropUnsent(key: string) {
  try { userStorage.local.removeItem(UNSENT_PREFIX + key); } catch { /* nothing to drop */ }
}
