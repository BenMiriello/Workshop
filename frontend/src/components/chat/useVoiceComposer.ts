import type { ReactNode } from "react";
import { createElement } from "react";
import { AlertTriangle, AudioLines, Loader, Square, X } from "lucide-react";
import { MIC_FAIL_TEXT } from "../../lib/voice/errors";
import type { VoiceSnapshot } from "../../lib/voice/client";
import type { VoicePrefs } from "../../lib/voice/modes";
import type { VoiceStatus } from "./VoiceControls";

// What the voice orb does and says, and what the status strip reads, derived
// from the session snapshot.
//
// Pure derivation, no state — but it is a page of branching that was sitting in
// the middle of the chat component, and the two rules it encodes are easy to
// break by accident:
//
//  1. THE ORB NEVER SHOWS A MIC GLYPH. A Mic on the orb was the confusing twin
//     of the mic-MUTE button beside it: two round controls, one glyph family,
//     different jobs. The orb uses AudioLines (listening, with the level halo),
//     Loader (connecting), X (thinking — tap to scrap a mis-heard send), Square
//     (speaking — the stop affordance), AlertTriangle (error).
//  2. "LISTENING" WITH THE MIC OFF IS A LIE, and it is the exact lie that made a
//     muted mic look like a broken one. A standing capture failure also gets its
//     own branch ahead of the per-turn error, because it outlives any one turn:
//     without that, clearing `error` silently turned "no microphone" back into
//     "Listening".

export function useVoiceComposer(
  vsnap: VoiceSnapshot,
  vprefs: VoicePrefs,
  vsession: {
    interrupt: () => void;
    cancelTurn: () => void;
    forceEndUtterance: () => void;
  },
): {
  errored: boolean;
  orbAction: () => void;
  orbIcon: ReactNode;
  orbLabel: string;
  status: VoiceStatus;
} {
  const errored = !!vsnap.error;

  const orbAction = () => {
    if (vsnap.state === "speaking") vsession.interrupt();
    else if (vsnap.state === "thinking") vsession.cancelTurn();   // scrap a mis-heard send
    else if (vsnap.state === "listening") vsession.forceEndUtterance();
  };

  const icon = (c: typeof AlertTriangle, className?: string) =>
    createElement(c, { size: 16, "aria-hidden": true, ...(className ? { className } : {}) });

  const orbIcon =
    errored || vsnap.state === "idle"
      ? icon(AlertTriangle)
      : vsnap.state === "connecting"
        ? icon(Loader, "ws-spin")
        : vsnap.state === "thinking"
          ? icon(X)
          : vsnap.state === "speaking"
            ? icon(Square)
            : icon(AudioLines);

  const orbLabel =
    errored ? "Voice error"
    : vsnap.state === "speaking" ? "Stop"
    : vsnap.state === "listening" ? "Send now"
    : vsnap.state === "thinking" ? "Cancel"
    : vsnap.state === "connecting" ? "Starting" : "Listening";

  // THE STRIP NO LONGER CARRIES THE TRANSCRIPT. It is one line inside the
  // composer, so anything that grows in it pushes the action row down, and it
  // clipped a long utterance to its first few words anyway. What you are saying
  // goes into the message box itself, as a draft (see voiceDictation.ts), which
  // is both where unsent text belongs and the only place it survives the session
  // ending. The strip keeps the one thing that is genuinely a fixed-size status.
  //
  // A capture problem is a NOTICE, not an error. "You didn't allow the
  // microphone" is a state with a recovery step, and showing it in the red
  // per-turn slot said something broke when nothing did.
  const status: VoiceStatus =
    vsnap.error
      ? { kind: "error", text: vsnap.error }
      : vsnap.micFail
        ? { kind: "notice", text: MIC_FAIL_TEXT[vsnap.micFail] }
        : {
            kind: "state",
            text:
              vsnap.state === "connecting" ? "Connecting"
              : vsnap.state === "thinking" ? "Thinking"
              : vsnap.state === "speaking" ? "Speaking. Esc to stop"
              : vsnap.state === "listening"
                ? (!vprefs.mic_enabled ? "Mic off. Tap the mic to talk"
                  // A muted output lane is a legitimate setting and an invisible
                  // one: you talk, the reply arrives as text, and nothing ever
                  // says why it did not speak. Say it here rather than leaving it
                  // to be discovered in the settings sheet.
                  : !vprefs.speak_replies ? "Listening. Replies in text"
                  : "Listening")
                : "Voice on",
          };

  return { errored, orbAction, orbIcon, orbLabel, status };
}
