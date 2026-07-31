import type { VoiceSnapshot } from "../../lib/voiceSession";
import WorkingIndicator from "./WorkingIndicator";

// A spoken exchange in the log, as ordinary bubbles.
//
// Voice is a MODE of this chat, not a separate screen: the same thread, the same
// message log. So a spoken turn renders as normal bubbles, and the only
// voice-specific touch is the caption cue (heard / speaking / pending) INSIDE a
// standard assistant bubble.
//
// ORDER MATTERS HERE. The settling exchange — the turn that just finished, held
// until its persisted messages land — renders ABOVE the live overlay, so a fast
// follow-up turn never hides it. Turn N stays put as plain bubbles until it
// swaps into the log, which is what removes the vanish-then-pop.

export function SettlingTurn({ settling }: { settling: { user: string; assistant: string } }) {
  return (
    <>
      {settling.user && (
        <div className="chat-msg user"><div className="chat-bubble">{settling.user}</div></div>
      )}
      {settling.assistant && (
        <div className="chat-msg assistant"><div className="chat-bubble">{settling.assistant}</div></div>
      )}
    </>
  );
}

// Muted (captions-only) replies stay in `thinking` the whole time and stream
// their captions here too, which is why this is keyed on thinking OR speaking
// rather than on whether audio is playing.
export function VoiceLiveTurn({ vsnap }: { vsnap: VoiceSnapshot }) {
  return (
    <>
      {vsnap.lastUser && (
        <div className="chat-msg user"><div className="chat-bubble">{vsnap.lastUser}</div></div>
      )}
      <div className="chat-msg assistant">
        <div className="chat-bubble">
          {vsnap.sentences.length > 0 ? (
            <span className="chat-voice-live">
              {vsnap.sentences.map((s, i) => (
                <span
                  key={i}
                  className={
                    s.status === "heard" ? "chat-voice-heard"
                    : s.status === "speaking" ? "chat-voice-now"
                    : "chat-voice-pending"
                  }
                >
                  {s.text}{" "}
                </span>
              ))}
            </span>
          ) : (
            <WorkingIndicator label="Thinking" />
          )}
        </div>
      </div>
    </>
  );
}
