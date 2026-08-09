import type { ReactNode } from "react";
import { createElement } from "react";
import { AlertTriangle, AudioLines, Loader, Square, X } from "lucide-react";
import { MIC_FAIL_TEXT } from "../../lib/voice";
import type { VoiceSnapshot, VoicePrefs } from "../../lib/voiceSession";
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

  const status: VoiceStatus =
    vsnap.error
      ? { kind: "error", text: vsnap.error }
      : vsnap.micFail
        ? { kind: "error", text: MIC_FAIL_TEXT[vsnap.micFail] }
        : vsnap.state === "listening" && (vsnap.partial || vsnap.pendingUser)
          ? { kind: "partial", text: vsnap.partial || vsnap.pendingUser }
          : {
              kind: "state",
              text:
                vsnap.state === "connecting" ? "Connecting"
                : vsnap.state === "thinking" ? "Thinking"
                : vsnap.state === "speaking" ? "Speaking. Esc to stop"
                : vsnap.state === "listening"
                  ? (vprefs.mic_enabled ? "Listening" : "Mic off. Type to send")
                  : "Voice on",
            };

  return { errored, orbAction, orbIcon, orbLabel, status };
}
