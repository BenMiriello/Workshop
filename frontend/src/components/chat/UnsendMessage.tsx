import { Undo2 } from "lucide-react";

// Take back a message the server is holding to send when the current turn ends.
// It is already on the thread, so this is an undo of an accepted-but-not-yet-
// delivered message rather than discarding a local-only send (that is Cancel's
// job, .chat-cancel). Icon only, in the bubble's bottom-right beside Copy, the
// same corner Branch and Copy use on a delivered message. It shares .chat-cancel
// for placement: same size, same corner, same shift-left when Copy is present.
export default function UnsendMessage({ onUnsend }: { onUnsend: () => void }) {
  return (
    <button
      type="button"
      className="chat-cancel"
      onClick={onUnsend}
      aria-label="Unsend message"
      title="Unsend message"
    >
      <Undo2 size={13} strokeWidth={1.8} aria-hidden />
    </button>
  );
}
