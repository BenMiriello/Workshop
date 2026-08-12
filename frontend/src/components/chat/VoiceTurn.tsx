import type { VoiceSnapshot } from "../../lib/voice/client";
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

// What you are saying RIGHT NOW, as the message it is about to become.
//
// This used to be one clipped line in the strip above the composer: nowrap plus
// an ellipsis, so it showed the first few words of an utterance and never the
// latest ones — the part you actually want to check. And because the strip sits
// inside the composer, growing it pushed the whole action row down.
//
// A bubble at the foot of the log fixes both by construction. It wraps to as
// many lines as the speech needs, the log is already pinned to the bottom so the
// newest words are the visible ones, and it takes its space from the transcript
// (which scrolls) rather than from the composer (which does not).
export function VoicePendingTurn({ text, previewing }: { text: string; previewing: boolean }) {
  return (
    <div className="chat-msg user">
      <div className="chat-bubble chat-bubble--speaking" aria-live="polite">
        {text}
        {previewing && <span className="chat-voice-catchup" aria-hidden />}
      </div>
    </div>
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
