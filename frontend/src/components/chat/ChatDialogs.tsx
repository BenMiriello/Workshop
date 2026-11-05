import { ConfirmDialog } from "@workshop/ui";
import { scopeLabel } from "../../lib/tasks";
import TicketDraftsDialog from "./TicketDraftsDialog";
import RenameChatDialog from "./RenameChatDialog";

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
  ticketsOpen, onCloseTickets,
  renameOpen, renameBusy, onCancelRename, onSaveRename,
  confirmNew, onCancelNew, onConfirmNew,
  confirmClear, onCancelClear, onConfirmClear,
}: {
  scope: string;
  activeId: string | null;
  activeThreadTitle?: string;
  ticketsOpen: boolean;
  onCloseTickets: () => void;
  renameOpen: boolean;
  renameBusy: boolean;
  onCancelRename: () => void;
  onSaveRename: (title: string) => void;
  confirmNew: boolean;
  confirmClear: boolean;
  onCancelClear: () => void;
  onConfirmClear: () => void;
  onCancelNew: () => void;
  onConfirmNew: () => void;
}) {
  return (
    <>
      <TicketDraftsDialog
        open={ticketsOpen}
        chatId={activeId}
        onClose={onCloseTickets}
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

      {/* CLEAR ASKS FIRST: it drops the live session's context, which the
          name alone does not say. Compact does not ask; it deletes nothing. */}
      <ConfirmDialog
        open={confirmClear}
        onCancel={onCancelClear}
        onConfirm={onConfirmClear}
        title="Clear this conversation's context?"
        body={
          <span>
            Previous messages will still be kept but not included in chat context.
          </span>
        }
        confirmLabel="Clear context"
        danger
      />
    </>
  );
}
