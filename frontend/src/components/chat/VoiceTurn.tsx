import type { VoiceSnapshot } from "../../lib/voice/client";
import WorkingIndicator from "./WorkingIndicator";

// The reply being spoken, and nothing else.
//
// THIS FILE USED TO HOLD THREE COMPONENTS AND THEY ALL DREW THE OPERATOR'S OWN
// WORDS. A pending bubble from `partial`/`pendingUser`, a committed bubble from
// `lastUser`, and a held copy of the finished exchange, each mounted on a
// different phase check and handed off to the next by timing. Four copies of one
// string, counting the real message, with no identity tying them together:
//
//   - the handoff blinked, because the next slot mounted on a later commit than
//     the previous one unmounted (the flicker),
//   - `lastUser` sat directly above the assistant bubble, so a new utterance
//     appeared attached to the previous exchange (words moving into an earlier
//     message),
//   - the held copy could be on screen at the same time as the live one and as
//     the real persisted message (the duplication),
//   - and all of it was socket state, so turning voice off deleted every copy.
//
// There is one rule now and it removes the whole class: THE OPERATOR'S WORDS ARE
// NEVER RENDERED FROM VOICE STATE. Before they are sent they are a draft in the
// composer; after they are sent they are an ordinary message in the log, written
// at capture. Voice draws only the half the log does not have yet, which is the
// reply as it is being spoken.

export function VoiceReply({ vsnap }: { vsnap: VoiceSnapshot }) {
  return (
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
  );
}
