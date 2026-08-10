import { ConfirmDialog } from "../../ui";
import { scopeLabel } from "../../lib/tasks";
import TicketDraftsDialog from "./TicketDraftsDialog";
import RenameChatDialog from "./RenameChatDialog";
import type { FiledNotice } from "./FiledNotice";

// Everything the chat opens over itself: filing tickets, renaming, and the
// two confirmations.
//
// BOTH CONFIRMATIONS ARE DELIBERATE, and they are deliberately different.
// Starting a new chat is cheap to undo — the thread is still there — but
// disorienting mid-conversation, so it asks and says plainly that nothing is
// deleted. Deleting is not undoable, so it gets the danger treatment and names
// the consequence. A confirmation that does not distinguish those two is a
// confirmation the operator learns to click through.

export default function ChatDialogs({
  scope,
  activeId, activeThreadTitle,
  ticketsOpen, onCloseTickets, onFiled,
  renameOpen, renameBusy, onCancelRename, onSaveRename,
  confirmNew, onCancelNew, onConfirmNew,
  confirmCompact, onCancelCompact, onConfirmCompact,
  confirmClear, onCancelClear, onConfirmClear,
  confirmDeleteId, confirmDeleteTitle, onCancelDelete, onConfirmDelete,
}: {
  scope: string;
  activeId: string | null;
  activeThreadTitle?: string;
  ticketsOpen: boolean;
  onCloseTickets: () => void;
  onFiled: (n: FiledNotice) => void;
  renameOpen: boolean;
  renameBusy: boolean;
  onCancelRename: () => void;
  onSaveRename: (title: string) => void;
  confirmNew: boolean;
  confirmCompact: boolean;
  onCancelCompact: () => void;
  onConfirmCompact: () => void;
  confirmClear: boolean;
  onCancelClear: () => void;
  onConfirmClear: () => void;
  onCancelNew: () => void;
  onConfirmNew: () => void;
  confirmDeleteId: string | null;
  confirmDeleteTitle?: string;
  onCancelDelete: () => void;
  onConfirmDelete: (id: string) => void | Promise<void>;
}) {
  return (
    <>
      <TicketDraftsDialog
        open={ticketsOpen}
        chatId={activeId}
        onClose={onCloseTickets}
        onFiled={onFiled}
      />

      <RenameChatDialog
        open={renameOpen}
        initialTitle={activeThreadTitle ?? ""}
        busy={renameBusy}
        onCancel={onCancelRename}
        onSave={onSaveRename}
      />

      <ConfirmDialog
        open={confirmNew}
        onCancel={onCancelNew}
        onConfirm={onConfirmNew}
        title="Start a new chat?"
        body={
          activeId ? (
            <span>
              This leaves {activeThreadTitle ? `"${activeThreadTitle}"` : "the current chat"} and opens a
              blank one in {scopeLabel(scope)}. Nothing is deleted; it stays in All chats.
            </span>
          ) : (
            <span>Opens a blank chat in {scopeLabel(scope)}.</span>
          )
        }
        confirmLabel="New chat"
        danger={false}
      />

      {/* COMPACT AND CLEAR ASK FIRST. Both are one click from the primary in
          the context modal, both change the conversation in a way nothing can
          put back, and neither is obvious from its name: a fold keeps a running
          summary, a clear keeps nothing. The dialog is where that difference
          gets stated, once, at the moment it matters. */}
      <ConfirmDialog
        open={confirmCompact}
        onCancel={onCancelCompact}
        onConfirm={onConfirmCompact}
        title="Compact this conversation?"
        body={
          <span>
            Earlier messages are folded into a running summary so the model reads
            less of them. The transcript stays here in full, but the detail the
            model can still draw on is lost. This can't be undone.
          </span>
        }
        confirmLabel="Compact"
        danger={false}
      />

      <ConfirmDialog
        open={confirmClear}
        onCancel={onCancelClear}
        onConfirm={onConfirmClear}
        title="Clear this conversation's context?"
        body={
          <span>
            The model stops seeing every earlier message, with NO summary kept.
            The transcript stays here and you can still read it, but the next
            turn starts from nothing. This can't be undone.
          </span>
        }
        confirmLabel="Clear context"
        danger
      />

      <ConfirmDialog
        open={confirmDeleteId != null}
        onCancel={onCancelDelete}
        onConfirm={async () => { if (confirmDeleteId) await onConfirmDelete(confirmDeleteId); }}
        title="Delete chat?"
        body={
          <span>
            This permanently deletes {confirmDeleteTitle ? `"${confirmDeleteTitle}"` : "this chat"} and its
            messages. This can't be undone.
          </span>
        }
        confirmLabel="Delete chat"
      />
    </>
  );
}
