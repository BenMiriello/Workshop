// The operator's own shortcut for each overlay app, kept per device: a desk
// keyboard and a laptop's want different keys, as with the speech shortcuts.
// An app with no override uses the shortcut its manifest declares.

import { useSyncExternalStore } from "react";
import { userStorage } from "../storage";
import { parseChord, type Chord } from "../speech/shortcuts";
import { parseShortcut } from "./shortcut";

export type ShortcutOverrides = Record<string, Chord>;

const KEY = "overlayApps.shortcuts";
const listeners = new Set<() => void>();

/** Stored text to overrides; anything malformed is dropped entry by entry. */
export function parseOverrides(raw: string | null): ShortcutOverrides {
  let v: unknown = {};
  try {
    v = raw ? JSON.parse(raw) : {};
  } catch {
    v = {};
  }
  const out: ShortcutOverrides = {};
  if (!v || typeof v !== "object" || Array.isArray(v)) return out;
  for (const [slug, c] of Object.entries(v as Record<string, unknown>)) {
    const chord = parseChord(c);
    if (chord) out[slug] = chord;
  }
  return out;
}

let cache: { raw: string | null; value: ShortcutOverrides } | null = null;

function read(): ShortcutOverrides {
  const raw = userStorage.local.getItem(KEY);
  if (!cache || cache.raw !== raw) cache = { raw, value: parseOverrides(raw) };
  return cache.value;
}

function write(next: ShortcutOverrides): void {
  userStorage.local.setItem(KEY, JSON.stringify(next));
  listeners.forEach((l) => l());
}

export const overlayShortcuts = {
  get: read,
  set(slug: string, chord: Chord) {
    write({ ...read(), [slug]: chord });
  },
  reset(slug: string) {
    const next = { ...read() };
    delete next[slug];
    write(next);
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => { listeners.delete(l); };
  },
};

export function useOverlayShortcuts(): ShortcutOverrides {
  return useSyncExternalStore(overlayShortcuts.subscribe, read, read);
}

/** The chord that opens an app: the operator's, else the manifest's. */
export function chordFor(slug: string, declared: string | null | undefined,
  overrides: ShortcutOverrides, mac: boolean): Chord | null {
  return overrides[slug] ?? parseShortcut(declared, mac);
}
