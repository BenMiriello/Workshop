import { describe, expect, it } from "vitest";
import { DEFAULT_SPEECH_PREFS, parseSpeechPrefs } from "./prefs";
import { DEFAULT_CHORDS } from "./shortcuts";

const chord = { code: "KeyD", ctrl: false, shift: false, alt: true, meta: true };

describe("parseSpeechPrefs", () => {
  it("starts with everything on, the mic button in fields, the server voice, the default chords, not kept on", () => {
    expect(parseSpeechPrefs(null)).toEqual(DEFAULT_SPEECH_PREFS);
    expect(DEFAULT_SPEECH_PREFS).toEqual({
      dictation: true, micButton: "fields", selectionButton: true, readHighlight: true, voice: null, shortcuts: DEFAULT_CHORDS, keepOn: false,
    });
  });

  it("keeps what was stored", () => {
    const stored = {
      dictation: false, micButton: "shortcut", selectionButton: false, readHighlight: false, voice: "bf_emma",
      shortcuts: { dictate: chord, read: DEFAULT_CHORDS.read }, keepOn: true,
    };
    expect(parseSpeechPrefs(JSON.stringify(stored))).toEqual(stored);
  });

  it("falls back one field at a time on anything malformed", () => {
    expect(parseSpeechPrefs("{not json")).toEqual(DEFAULT_SPEECH_PREFS);
    expect(parseSpeechPrefs(JSON.stringify({ micButton: "everywhere", dictation: "yes", voice: 3 })))
      .toEqual(DEFAULT_SPEECH_PREFS);
    expect(parseSpeechPrefs(JSON.stringify({ selectionButton: false })).selectionButton).toBe(false);
    expect(parseSpeechPrefs(JSON.stringify({ voice: "" })).voice).toBeNull();
  });

  it("falls back one chord at a time", () => {
    const raw = JSON.stringify({ shortcuts: { dictate: chord, read: { code: "KeyR", ctrl: "yes" } } });
    expect(parseSpeechPrefs(raw).shortcuts).toEqual({ dictate: chord, read: DEFAULT_CHORDS.read });
    expect(parseSpeechPrefs(JSON.stringify({ shortcuts: "none" })).shortcuts).toEqual(DEFAULT_CHORDS);
    expect(parseSpeechPrefs(JSON.stringify({ shortcuts: { read: { code: "", ctrl: true, shift: false, alt: false, meta: false } } })).shortcuts.read)
      .toEqual(DEFAULT_CHORDS.read);
  });
});
