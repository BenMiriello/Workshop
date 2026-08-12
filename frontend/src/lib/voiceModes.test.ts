import { describe, expect, it } from "vitest";
import { normalizeModes, DEFAULT_PREFS, type VoicePrefs } from "./voice/modes";

const prefs = (over: Partial<VoicePrefs> = {}): VoicePrefs =>
  ({ ...DEFAULT_PREFS, ...over });

// The composer's mic button patches `mic_enabled` and nothing else. That field
// is DERIVED from `input_mode`, so without the patch argument its value was
// recomputed from a mode nobody changed and discarded on the same line: the
// button did nothing, in either direction, and the operator was stuck on
// "Mic off. Type to send" with no way to clear it.
describe("the mic button (a patch naming only mic_enabled)", () => {
  it("turns the mic OFF from both", () => {
    const p = prefs({ input_mode: "both", mic_enabled: true });
    const next = normalizeModes({ ...p, mic_enabled: false }, { mic_enabled: false });
    expect(next.mic_enabled).toBe(false);
    expect(next.input_mode).toBe("text");
  });

  it("turns the mic BACK ON from text", () => {
    // This is the one the operator actually hit: stuck off, pressing did nothing.
    const p = prefs({ input_mode: "text", mic_enabled: false });
    const next = normalizeModes({ ...p, mic_enabled: true }, { mic_enabled: true });
    expect(next.mic_enabled).toBe(true);
    expect(next.input_mode).toBe("both");
  });

  it("re-enabling the mic never takes typing away", () => {
    // "voice" would mean hands-free only. A mic toggle must not silently remove
    // the composer, so restoring a lane always restores "both".
    const p = prefs({ input_mode: "text" });
    expect(normalizeModes({ ...p, mic_enabled: true }, { mic_enabled: true }).input_mode).toBe("both");
  });

  it("leaves a voice-only lane alone when the mic is switched on again", () => {
    // Already hands-free: nothing to restore, and it must not be widened.
    const p = prefs({ input_mode: "voice", mic_enabled: true });
    expect(normalizeModes({ ...p, mic_enabled: true }, { mic_enabled: true }).input_mode).toBe("voice");
  });

  it("round-trips off and on again", () => {
    let p = prefs({ input_mode: "both" });
    p = normalizeModes({ ...p, mic_enabled: false }, { mic_enabled: false });
    expect(p.mic_enabled).toBe(false);
    p = normalizeModes({ ...p, mic_enabled: true }, { mic_enabled: true });
    expect(p.mic_enabled).toBe(true);
    p = normalizeModes({ ...p, mic_enabled: false }, { mic_enabled: false });
    expect(p.mic_enabled).toBe(false);
  });
});

describe("the reply-mute (a patch naming only speak_replies)", () => {
  it("mutes and unmutes, the same way", () => {
    let p = prefs({ output_mode: "both" });
    p = normalizeModes({ ...p, speak_replies: false }, { speak_replies: false });
    expect(p.output_mode).toBe("text");
    expect(p.speak_replies).toBe(false);
    p = normalizeModes({ ...p, speak_replies: true }, { speak_replies: true });
    expect(p.output_mode).toBe("both");
    expect(p.speak_replies).toBe(true);
  });
});

describe("the settings sheet (a patch naming the mode itself)", () => {
  it("lets the mode win over the stale boolean beside it", () => {
    const p = prefs({ input_mode: "both", mic_enabled: true });
    const next = normalizeModes({ ...p, input_mode: "text" }, { input_mode: "text" });
    expect(next.input_mode).toBe("text");
    expect(next.mic_enabled).toBe(false);
  });

  it("wins even when the patch names both and they disagree", () => {
    // The mode is the source of truth; the boolean is a projection of it.
    const p = prefs({ input_mode: "both" });
    const patch = { input_mode: "text" as const, mic_enabled: true };
    expect(normalizeModes({ ...p, ...patch }, patch).mic_enabled).toBe(false);
  });
});

describe("prefs written before the modality matrix existed", () => {
  it("derives the modes from the legacy booleans when no patch is involved", () => {
    const legacy = { ...DEFAULT_PREFS, mic_enabled: false, speak_replies: false } as VoicePrefs;
    delete (legacy as Partial<VoicePrefs>).input_mode;
    delete (legacy as Partial<VoicePrefs>).output_mode;
    const next = normalizeModes(legacy);
    expect(next.input_mode).toBe("text");
    expect(next.output_mode).toBe("text");
  });

  it("defaults to both lanes open", () => {
    const bare = { ...DEFAULT_PREFS } as VoicePrefs;
    delete (bare as Partial<VoicePrefs>).input_mode;
    delete (bare as Partial<VoicePrefs>).output_mode;
    const next = normalizeModes(bare);
    expect(next.input_mode).toBe("both");
    expect(next.output_mode).toBe("both");
  });
});
