// The modality matrix: how a conversation is carried, in each direction.
//
// `input_mode` and `output_mode` are the source of truth. `mic_enabled` and
// `speak_replies` are DERIVED, and are kept because stored prefs files and the
// API carry them.
//
// THE BUG THIS FILE IS SHAPED BY. The composer's mic button patches
// `mic_enabled` and nothing else. With `mic_enabled` recomputed from an
// `input_mode` nobody changed, the button's write was discarded on the same
// line it was made: the control did nothing in BOTH directions, and the
// operator was stuck on "Mic off" with the only thing that claimed to fix it
// incapable of it. The `patch` argument is what makes "whichever side the
// caller wrote wins" expressible; without it, it is not.
//
// `normalizeModes` is now called at exactly ONE place (useVoiceSession's
// updatePrefs). The previous fix landed at the hook and left
// `VoiceSession.setPrefs` calling it patchless, so the same bug class was one
// caller away from returning. There is no second caller now.

export type ModalityMode = "voice" | "text" | "both";
export type ReplyPace = "snappy" | "natural" | "patient";

export type VoicePrefs = {
  voice: string;
  speed: number;
  input_mode: ModalityMode;
  output_mode: ModalityMode;
  mic_enabled: boolean;      // derived from input_mode
  speak_replies: boolean;    // derived from output_mode
  // How soon it takes a turn. ONE setting, biasing the turn-end model.
  //
  // It replaces endpoint_silence_ms, eager_endpoint, eager_silence_ms and
  // barge_sensitivity. The first three tried to answer "have they finished"
  // with a stopwatch, which no value can do: short splits a thought and long
  // makes every turn sluggish. The fourth was labelled as an interruption
  // preference and was actually the master gate on whether you were heard.
  reply_pace: ReplyPace;
};

export const DEFAULT_PREFS: VoicePrefs = {
  voice: "af_heart",
  speed: 1.0,
  input_mode: "both",
  output_mode: "both",
  mic_enabled: true,
  speak_replies: true,
  reply_pace: "natural",
};

export function normalizeModes(p: VoicePrefs, patch?: Partial<VoicePrefs>): VoicePrefs {
  let input_mode: ModalityMode = p.input_mode ?? (p.mic_enabled === false ? "text" : "both");
  let output_mode: ModalityMode = p.output_mode ?? (p.speak_replies === false ? "text" : "both");
  // A patch naming the derived boolean and NOT the mode is asking for the lane
  // to change. Turning a lane back on restores "both" rather than "voice", so
  // re-enabling the mic can never take typing away.
  if (patch && patch.input_mode === undefined && patch.mic_enabled !== undefined) {
    input_mode = patch.mic_enabled ? (input_mode === "text" ? "both" : input_mode) : "text";
  }
  if (patch && patch.output_mode === undefined && patch.speak_replies !== undefined) {
    output_mode = patch.speak_replies ? (output_mode === "text" ? "both" : output_mode) : "text";
  }
  return {
    ...p,
    input_mode,
    output_mode,
    mic_enabled: input_mode !== "text",
    speak_replies: output_mode !== "text",
  };
}
