import { useCallback, useState } from "react";
import { GitBranch } from "lucide-react";
import { Menu, MenuItem, MenuLabel } from "@workshop/ui";
import { chatApi, type Branch } from "../../lib/chat";

// WHICH LINE OF THE CONVERSATION AM I ON, and switch to another (ADR 0020).
//
// This is THREAD state, so it lives on the toolbar beside the other thread
// state (which scope, which model, which mode) and not under a message. It used
// to be the bottom half of the per-message branch menu, where it was repeated
// identically under every bubble while describing none of them, and where the
// only way to answer "am I on a branch right now" was to open a message's menu
// and look for the checkmark.
//
// SHOWN ONLY ONCE THERE IS SOMETHING TO SAY. A conversation that has never been
// branched has exactly one line, and a picker offering that one line is a
// control that cannot do anything. It appears the moment a second line exists
// and stays, because from then on "which one is this" is a real question.
//
// Switching does not confirm: one pointer moves, nothing is created or
// destroyed, and switching back is the same click. The CREATING act
// (BranchAction) confirms, because going back feels destructive even though it
// is not, and that dialog's job is to say so.

/** Rows before the list starts scrolling. The CSS cap (.chat-branch-list) is
 *  what enforces it; this only keeps the "N more" line honest about the same
 *  number. The operator asked for exactly this: a picker that stays a small
 *  target no matter how many times a conversation has been branched. */
const VISIBLE_BRANCHES = 3;

function subtitle(b: Branch): string {
  if (b.is_root) return b.preview || "the original line";
  if (b.forked_from) return `from: ${b.forked_from}`;
  return b.preview || "no messages yet";
}

export default function BranchPicker({
  threadId,
  name,
  count,
  onChanged,
}: {
  threadId: string;
  /** The current branch's name, so the bar answers the question at rest. */
  name: string;
  /** From the sync endpoint, so the control can be offered before it is
   *  opened and the list is only fetched when someone looks. */
  count: number;
  onChanged: () => void;
}) {
  const [branches, setBranches] = useState<Branch[] | null>(null);

  // Fetched when the menu opens, not on every sync. Same shape as
  // RecentChatsMenu.
  const load = useCallback(() => {
    chatApi.listBranches(threadId)
      .then((r) => setBranches(r.branches))
      .catch(() => setBranches([]));
  }, [threadId]);

  const switchTo = useCallback((b: Branch) => {
    if (b.is_current) return;
    chatApi.switchBranch(threadId, b.id).then(onChanged).catch(() => {});
  }, [threadId, onChanged]);

  if (count < 2) return null;

  const rows = branches ?? [];
  const overflow = Math.max(0, rows.length - VISIBLE_BRANCHES);

  return (
    <Menu
      label="Lines of this conversation"
      trigger={
        <button
          className="chat-tb__picker"
          title="Which line of this conversation you are on"
          onClick={load}
        >
          <GitBranch size={14} strokeWidth={1.8} aria-hidden />
          <span className="chat-tb__picker-val chat-tb__picker-val--wide">{name || "Main"}</span>
        </button>
      }
    >
      <MenuLabel>Lines of this conversation</MenuLabel>
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
        {branches === null && <MenuItem disabled onSelect={() => {}}>Loading</MenuItem>}
      </div>
      {overflow > 0 && <MenuLabel>{overflow} more, scroll to see</MenuLabel>}
    </Menu>
  );
}
