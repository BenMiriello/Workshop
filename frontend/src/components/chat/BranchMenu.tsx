import { useCallback, useState } from "react";
import { GitBranch } from "lucide-react";
import { ConfirmDialog, Menu, MenuItem, MenuLabel, MenuSeparator } from "@workshop/ui";
import { chatApi, type Branch } from "../../lib/chat";

// Go back to a point in this conversation and carry on from there (ADR 0020).
//
// ONE ACTION, NOT TWO. "Return to here" and "branch from here" sound like
// different things and are the same operation: the line you are leaving keeps
// its own pointer and stays in the list below, so going back never discards
// anything and there is nothing for a second verb to mean. Offering both would
// be two names for one act, and the operator would reasonably expect them to
// differ.
//
// TWO MEANINGS, THOUGH, AND THEY DEPEND ON WHOSE BUBBLE IT IS.
//
//   a reply    keep this answer, drop what came after it. The answer was fine
//              and the conversation after it was not.
//   your own   take back what you said here, so the anchor is the message
//   message    BEFORE it. Keeping your own message and continuing from it would
//              leave you typing into your own last word, which is not what
//              pointing at it means.
//
// That is why the label differs by role rather than one label covering both:
// the same word would be doing two jobs and the operator would have to learn
// which one from the outcome.
//
// The confirmation is not a warning. Going back FEELS destructive, and the
// dialog exists to say plainly that it is not.

/** Rows before the branch list starts scrolling. The CSS cap
 *  (.chat-branch-list) is what enforces it; this only keeps the "N more" line
 *  honest about the same number. */
const VISIBLE_BRANCHES = 3;

function subtitle(b: Branch): string {
  if (b.is_root) return b.preview || "the original line";
  if (b.forked_from) return `from: ${b.forked_from}`;
  return b.preview || "no messages yet";
}

export default function BranchMenu({
  threadId,
  anchorId,
  mode,
  isHead,
  branchCount,
  onChanged,
}: {
  threadId: string;
  /** The message pointed at: the transcript's own id for this bubble. */
  anchorId: string;
  /** Whether the anchor is KEPT. See the note above. */
  mode: "after" | "before";
  /** This bubble is already the end of the conversation, so going back to it
   *  would change nothing. The action says so instead of accepting the click
   *  and appearing to do nothing. */
  isHead: boolean;
  /** From the sync endpoint, so the picker can be offered before it is opened. */
  branchCount: number;
  /** Re-read the thread; the visible transcript has changed. */
  onChanged: () => void;
}) {
  const [branches, setBranches] = useState<Branch[] | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Fetched when the menu opens, not on every render of every bubble. A
  // transcript can hold hundreds of these and none of them needs the list until
  // it is looked at. Same shape as RecentChatsMenu.
  const load = useCallback(() => {
    chatApi.listBranches(threadId)
      .then((r) => setBranches(r.branches))
      .catch(() => setBranches([]));
  }, [threadId]);

  const fork = useCallback(() => {
    setBusy(true);
    setError("");
    chatApi.forkThread(threadId, anchorId, mode)
      .then(() => { setConfirming(false); onChanged(); })
      .catch((e) => setError(e?.message || "Could not go back to this point."))
      .finally(() => setBusy(false));
  }, [threadId, anchorId, mode, onChanged]);

  const switchTo = useCallback((b: Branch) => {
    if (b.is_current) return;
    chatApi.switchBranch(threadId, b.id).then(onChanged).catch(() => {});
  }, [threadId, onChanged]);

  const rows = branches ?? [];
  const overflow = Math.max(0, rows.length - VISIBLE_BRANCHES);
  // Before the list has loaded, the sync endpoint's count is what tells us
  // whether this conversation has other lines at all.
  const hasOthers = rows.length > 1 || branchCount > 1;
  const label = mode === "before" ? "Go back to before this" : "Continue from here";

  return (
    <>
      <Menu
        className="chat-branch-anchor"
        side="top"
        label="Branches"
        trigger={
          <button
            type="button"
            className="chat-branch"
            title={label}
            aria-label={label}
            onClick={load}
          >
            <GitBranch size={13} strokeWidth={1.8} aria-hidden />
          </button>
        }
      >
        <MenuItem
          icon={<GitBranch size={14} strokeWidth={1.8} aria-hidden />}
          disabled={isHead}
          title={isHead ? "This is already the end of the chat" : undefined}
          onSelect={() => setConfirming(true)}
        >
          {label}
        </MenuItem>

        {hasOthers && (
          <>
            <MenuSeparator />
            <MenuLabel>Branches</MenuLabel>
            <div className="chat-branch-list">
              {rows.map((b) => (
                <MenuItem
                  key={b.id}
                  checked={b.is_current}
                  onSelect={() => switchTo(b)}
                  title={b.preview || undefined}
                >
                  <span className="chat-branch-row">
                    <span>{b.name}</span>
                    <span className="chat-branch-row__sub">{subtitle(b)}</span>
                  </span>
                </MenuItem>
              ))}
              {branches === null && (
                <MenuItem disabled onSelect={() => {}}>Loading</MenuItem>
              )}
            </div>
            {overflow > 0 && <MenuLabel>{overflow} more, scroll to see</MenuLabel>}
          </>
        )}
      </Menu>

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
                ? "The chat goes back to just before this message. It and "
                : "The chat goes back to this point and carries on from here. ")
              + (mode === "before" ? "everything after it are kept " : "Everything after it is kept ")
              + "as a separate branch you can switch back to at any time."
        }
        onCancel={() => { setConfirming(false); setError(""); }}
        onConfirm={fork}
      />
    </>
  );
}
