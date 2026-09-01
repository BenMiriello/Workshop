import { useCallback, useState } from "react";
import { Split } from "lucide-react";
import { ConfirmDialog } from "@workshop/ui";
import { chatApi } from "../../lib/chat";
import type { BranchMode } from "./branchable";

// Go back to a point in this conversation and carry on from there (ADR 0020).
//
// ONE ACTION, NOT TWO. "Return to here" and "branch from here" sound like
// different things and are the same operation: the line you are leaving keeps
// its own pointer and stays in the picker, so going back never discards
// anything and there is nothing for a second verb to mean.
//
// AND ONE ACTION, NOT A MENU. This used to be a dropdown whose first row was
// the action and whose remainder was the list of the thread's branches. The
// list is THREAD state: identical on every bubble, saying nothing about the
// message it hung off. Repeating it under every message is the same mistake as
// painting a constant, and it had a visible cost: the trailing reply, where the
// action is the identity operation and does nothing, still carried a control,
// because the list underneath was the only thing left in the menu worth
// opening. The operator asked the obvious question of that: "why would it be
// there for the last message if nothing else is after?"
//
// Split by what each surface answers and both get simpler. Here: one message,
// one action, a button. In the toolbar (BranchPicker): which line am I on, and
// switch. Neither repeats the other.
//
// TWO SENSES, DECIDED BY WHOSE MESSAGE IT IS. See branchable.ts, which is also
// where the rule for WHICH bubbles get this at all lives.
//
// AND IT IS NOT THE BRANCH GLYPH. This used to be a `<GitBranch/>`, the same
// icon the toolbar's code-branch pill wears, on a control that has nothing to
// do with the code. `threads-branches-and-pages-2026-08-29.md` §3 gives the
// word and the glyph to the branch of the CODE, so the transcript's own lines
// take a different mark here and in `BranchPicker`. The copy never said branch
// and does not start now: it says what happens ("Go back to before this",
// "Continue from here"), which is what the operator has to know.

export default function BranchAction({
  threadId,
  anchorId,
  mode,
  turnActive,
  onChanged,
}: {
  threadId: string;
  /** The message pointed at: the transcript's own id for this bubble. */
  anchorId: string;
  /** Whether the anchor is KEPT. See branchable.ts. */
  mode: BranchMode;
  /** A turn is in flight. Going back mid-turn is refused by the server (the
   *  reply would commit onto a line that does not contain the message it is
   *  answering), so the control says so rather than offering a click that 409s.
   *  Situational, unlike an action that could never do anything: that one is
   *  not rendered at all. Same treatment as the scope picker mid-turn. */
  turnActive: boolean;
  /** Re-read the thread; the visible transcript has changed. */
  onChanged: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const fork = useCallback(() => {
    setBusy(true);
    setError("");
    chatApi.forkThread(threadId, anchorId, mode)
      .then(() => { setConfirming(false); onChanged(); })
      .catch((e) => setError(e?.message || "Could not go back to this point."))
      .finally(() => setBusy(false));
  }, [threadId, anchorId, mode, onChanged]);

  const label = mode === "before" ? "Go back to before this" : "Continue from here";

  return (
    <>
      <button
        type="button"
        className="chat-branch"
        title={turnActive ? "Wait for this turn to finish, or stop it first." : label}
        aria-label={label}
        disabled={turnActive}
        onClick={() => setConfirming(true)}
      >
        <Split size={13} strokeWidth={1.8} aria-hidden />
      </button>

      <ConfirmDialog
        open={confirming}
        busy={busy}
        // Not destructive, and the treatment has to say so. Nothing is deleted:
        // the line being left keeps its pointer and appears in the picker.
        danger={false}
        confirmLabel={label}
        title={mode === "before"
          ? "Go back to before this message?"
          : "Continue from this message?"}
        body={
          error
            ? error
            : (mode === "before"
                ? "The chat goes back to just before this message. It and everything after it are kept "
                : "The chat goes back to this point and carries on from here. Everything after it is kept ")
              + "as a separate line you can switch back to from the toolbar at any time."
        }
        onCancel={() => { setConfirming(false); setError(""); }}
        onConfirm={fork}
      />
    </>
  );
}
