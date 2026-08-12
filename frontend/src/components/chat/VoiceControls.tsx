import type { ReactNode, RefObject } from "react";
import { AlertTriangle, Mic, MicOff, Settings2 } from "lucide-react";
import { Button } from "@workshop/ui";

// Voice mode's two pieces of composer chrome: the status strip above the input,
// and the control cluster beside Send.
//
// Voice is a MODE of the chat, not a separate screen — same thread, same log —
// so its controls live in the composer with the other per-turn knobs rather than
// taking over the surface.

export type VoiceStatus = { kind: string; text: string };

export function VoiceStrip({ status, onRetry }: { status: VoiceStatus; onRetry: () => void }) {
  return (
    <div className={`chat-voice-strip is-${status.kind}`} role="status" aria-live="polite">
      {status.kind === "error" ? (
        <>
          <span className="chat-voice-strip__msg">
            <AlertTriangle size={14} aria-hidden className="chat-voice-strip__icon" />
            {status.text}
          </span>
          <Button size="sm" variant="ghost" onClick={onRetry}>Retry</Button>
        </>
      ) : status.kind === "notice" ? (
        // A standing capture condition (permission refused, no device, insecure
        // origin). Says what to do, in the neutral tone, with no Retry: there is
        // nothing here for a retry to fix until the browser setting changes.
        <span className="chat-voice-strip__msg">
          <MicOff size={14} aria-hidden className="chat-voice-strip__icon" />
          <span className="chat-voice-strip__state">{status.text}</span>
        </span>
      ) : (
        <span className="chat-voice-strip__msg">
          <span className="chat-voice-strip__state">{status.text}</span>
        </span>
      )}
    </div>
  );
}

// The orb, mic mute and settings share a subtle background pill so they read as
// ONE group, distinct from the exit toggle and Send that sit outside it.
//
// Reply-mute ("Speak replies") deliberately lives in Voice settings, not here: a
// persistent TTS-off button next to the mic was the single most confusing thing
// about this cluster — two mute buttons doing different things.
export function VoiceCluster({
  orbRef, orbState, orbIcon, orbLabel, onOrb,
  micEnabled, onToggleMic, onOpenSettings, errored,
}: {
  orbRef: RefObject<HTMLButtonElement>;
  orbState: string;
  orbIcon: ReactNode;
  orbLabel: string;
  onOrb: () => void;
  micEnabled: boolean;
  onToggleMic: () => void;
  onOpenSettings: () => void;
  errored: boolean;
}) {
  return (
    <div className={`chat-voice-cluster${errored ? " is-error" : ""}`}>
      <button
        ref={orbRef}
        className={`chat-voice-orb is-${orbState}${errored ? " is-error" : ""}`}
        onClick={onOrb}
        aria-label={orbLabel}
        title={orbLabel}
      >
        {orbIcon}
      </button>
      <button
        className={`chat-slash-btn${micEnabled ? " is-active" : ""}`}
        onClick={onToggleMic}
        aria-pressed={micEnabled}
        aria-label={micEnabled ? "Mute microphone" : "Unmute microphone"}
        title={micEnabled ? "Mic on, tap to mute" : "Mic muted, tap to talk"}
      >
        {micEnabled ? <Mic size={16} aria-hidden /> : <MicOff size={16} aria-hidden />}
      </button>
      <button
        className="chat-slash-btn"
        onClick={onOpenSettings}
        aria-label="Voice settings"
        title="Voice settings"
      >
        <Settings2 size={16} aria-hidden />
      </button>
    </div>
  );
}
