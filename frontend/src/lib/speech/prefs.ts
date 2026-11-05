// What speech offers on this device: whether dictation is on, whether it stays
// on, whether its mic button shows in text fields or only the shortcut works, whether selecting
// text offers Read aloud, whether what is read is highlighted on the page, the
// reading voice and the two shortcut chords. Kept
// per device, because the microphone permission is per device and a phone and
// a desk want different buttons and keys.

import { useSyncExternalStore } from "react";
import { userStorage } from "../storage";
import { DEFAULT_CHORDS, parseChord, type SpeechChords } from "./shortcuts";

export type MicButton = "fields" | "shortcut";

export type SpeechPrefs = {
  dictation: boolean;
  micButton: MicButton;
  selectionButton: boolean;
  /** Mark the sentence being read on the page, and let a tap read from one. */
  readHighlight: boolean;
  /** A voice id from /speech/voices; null reads in the server's default. */
  voice: string | null;
  shortcuts: SpeechChords;
  /** Dictation stays on until turned off: no stop after a long silence, and
   *  it starts again after a reload if it was on. */
  keepOn: boolean;
};

export const DEFAULT_SPEECH_PREFS: SpeechPrefs = {
  dictation: true,
  micButton: "fields",
  selectionButton: true,
  readHighlight: true,
  voice: null,
  shortcuts: DEFAULT_CHORDS,
  keepOn: false,
};

const KEY = "speech.prefs";

function parseChords(v: unknown): SpeechChords {
  const c = v && typeof v === "object" ? v as Record<string, unknown> : {};
  return {
    dictate: parseChord(c.dictate) ?? DEFAULT_CHORDS.dictate,
    read: parseChord(c.read) ?? DEFAULT_CHORDS.read,
  };
}

/** Stored text to prefs. Anything missing or malformed falls back to its
 *  default, one field at a time. */
export function parseSpeechPrefs(raw: string | null): SpeechPrefs {
  let v: Record<string, unknown> = {};
  try {
    const parsed = raw ? JSON.parse(raw) : {};
    if (parsed && typeof parsed === "object") v = parsed as Record<string, unknown>;
  } catch {
    v = {};
  }
  return {
    dictation: typeof v.dictation === "boolean" ? v.dictation : DEFAULT_SPEECH_PREFS.dictation,
    micButton: v.micButton === "shortcut" || v.micButton === "fields" ? v.micButton : DEFAULT_SPEECH_PREFS.micButton,
    selectionButton: typeof v.selectionButton === "boolean"
      ? v.selectionButton : DEFAULT_SPEECH_PREFS.selectionButton,
    readHighlight: typeof v.readHighlight === "boolean" ? v.readHighlight : DEFAULT_SPEECH_PREFS.readHighlight,
    voice: typeof v.voice === "string" && v.voice ? v.voice : null,
    shortcuts: parseChords(v.shortcuts),
    keepOn: typeof v.keepOn === "boolean" ? v.keepOn : DEFAULT_SPEECH_PREFS.keepOn,
  };
}

const listeners = new Set<() => void>();
let current: SpeechPrefs | null = null;

export const speechPrefs = {
  get(): SpeechPrefs {
    if (!current) current = parseSpeechPrefs(userStorage.local.getItem(KEY));
    return current;
  },
  set(patch: Partial<SpeechPrefs>): void {
    current = { ...speechPrefs.get(), ...patch };
    userStorage.local.setItem(KEY, JSON.stringify(current));
    listeners.forEach((fn) => fn());
  },
  subscribe(fn: () => void): () => void {
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  },
};

export function useSpeechPrefs(): SpeechPrefs {
  return useSyncExternalStore(speechPrefs.subscribe, speechPrefs.get, speechPrefs.get);
}
