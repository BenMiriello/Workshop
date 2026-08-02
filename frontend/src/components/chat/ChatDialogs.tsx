import { ConfirmDialog } from "../../ui";
import { scopeLabel } from "../../lib/tasks";
import TicketDraftsDialog from "./TicketDraftsDialog";
import type { FiledNotice } from "./FiledNotice";

// Everything the chat opens over itself: filing tickets, and the two
// confirmations.
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
  confirmNew, onCancelNew, onConfirmNew,
  confirmDeleteId, confirmDeleteTitle, onCancelDelete, onConfirmDelete,
}: {
  scope: string;
  activeId: string | null;
  activeThreadTitle?: string;
  ticketsOpen: boolean;
  onCloseTickets: () => void;
  onFiled: (n: FiledNotice) => void;
  confirmNew: boolean;
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

      <ConfirmDialog
        open={confirmNew}
        onCancel={onCancelNew}
        onConfirm={onConfirmNew}
        title="Start a new chat?"
        body={
          activeId ? (
            <span>
              This leaves {activeThreadTitle ? `"${activeThreadTitle}"` : "the current chat"} and opens a
              blank one in {scopeLabel(scope)}. Nothing is deleted — you can come back to it from All chats.
            </span>
          ) : (
            <span>Opens a blank chat in {scopeLabel(scope)}.</span>
          )
        }
        confirmLabel="New chat"
        danger={false}
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
