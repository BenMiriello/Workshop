// Why the input lane cannot run, as a value the UI can explain.
//
// Extracted unchanged in spirit from the old voice.ts, which mixed this with
// capture, playback, three STT tiers and three TTS tiers in one 1100 line file.
// The classification is worth keeping exactly as it is: each kind and its
// sentence came from a real failure that used to surface as "mic error".

export type MicFailKind =
  | "insecure"     // not an https/localhost origin, so mediaDevices does not exist
  | "unsupported"  // secure, but no getUserMedia at all
  | "notAllowed"   // the user or a policy denied permission
  | "notFound"     // no input device
  | "inUse"        // held by another app (NotReadableError / TrackStartError)
  | "unknown";

// ONE sentence per kind, defined once, beside the kinds themselves. This used
// to live in three places that had drifted to three different recovery hints
// for the same condition. The recovery never names a specific control, because
// these render from more than one surface.
export const MIC_FAIL_TEXT: Record<MicFailKind, string> = {
  insecure: "Voice input needs a secure origin (https, or localhost). Typing still works.",
  unsupported: "This browser can't capture audio. Typing still works.",
  notAllowed: "Microphone permission denied. Allow it in your browser's site settings, then try the mic again.",
  notFound: "No microphone found. Connect one, then try the mic again.",
  inUse: "The microphone is in use by another app. Close it, then try the mic again.",
  unknown: "Couldn't open the microphone. Try the mic again.",
};

export function micFailText(kind: MicFailKind, detail?: string): string {
  const d = (detail || "").trim();
  return d ? `${MIC_FAIL_TEXT[kind]} (${d})` : MIC_FAIL_TEXT[kind];
}

export class MicError extends Error {
  kind: MicFailKind;
  constructor(kind: MicFailKind, message?: string) {
    super(message ?? MIC_FAIL_TEXT[kind]);
    this.name = "MicError";
    this.kind = kind;
  }
}

// Static check, safe before any user gesture. getUserMedia only exists on
// secure origins, so an http tailnet or a WebView without the right host wiring
// lands here instead of throwing an opaque TypeError from inside capture.
export function micPreflight(): MicError | null {
  if (typeof window === "undefined") {
    return new MicError("unsupported", micFailText("unsupported", "no browser environment"));
  }
  if (!window.isSecureContext) {
    return new MicError("insecure", micFailText(
      "insecure", `this page is ${window.location.protocol}//${window.location.host}`,
    ));
  }
  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
    return new MicError("unsupported", micFailText("unsupported", "no getUserMedia"));
  }
  return null;
}

export function classifyGumError(e: unknown): MicError {
  const name = (e as { name?: string })?.name || "";
  const msg = String((e as Error)?.message || e);
  if (name === "NotAllowedError" || name === "SecurityError" || /permission|not allowed/i.test(msg)) {
    return new MicError("notAllowed");
  }
  if (name === "NotFoundError" || name === "OverconstrainedError") {
    return new MicError("notFound");
  }
  if (name === "NotReadableError" || name === "TrackStartError" || name === "AbortError") {
    return new MicError("inUse");
  }
  return new MicError("unknown", micFailText("unknown", msg));
}
