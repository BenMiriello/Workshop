import { ChevronUp } from "lucide-react";
import { Button, Notice } from "@workshop/ui";

// THE TOP OF A TAIL-LOADED THREAD. Opening a thread now fetches only its
// newest slice (useThreadLog's `INITIAL_TAIL`), so a long conversation has
// history above the fold this tab has never asked for. This is the control
// that asks for it: shown only when the server said there is more
// (`has_more`, threaded through as `hasMoreOlder`), it pages one more window
// into the past and PREPENDS it, in place, preserving where the operator was
// reading (see useLogScroll's `holdForOlder`).
//
// A failed page is a `Notice`, not a `StateBlock`: the transcript below it is
// still on screen and still correct, so a dashed region claiming the whole
// area failed would be describing something that did not happen. See
// frontend/CLAUDE.md, "A region or a line".
export default function LoadOlderNote({
  loading, error, onLoad,
}: {
  loading: boolean;
  /** The last page failed, in a sentence. Null when nothing has gone wrong. */
  error: string | null;
  onLoad: () => void;
}) {
  if (error) {
    return (
      <Notice
        tone="error"
        action={<Button size="sm" variant="ghost" onClick={onLoad}>Retry</Button>}
      >
        {error}
      </Notice>
    );
  }
  return (
    <div className="chat-older">
      <Button size="sm" variant="ghost" loading={loading} onClick={onLoad}>
        <ChevronUp size={13} strokeWidth={1.8} aria-hidden />
        Load earlier messages
      </Button>
    </div>
  );
}
