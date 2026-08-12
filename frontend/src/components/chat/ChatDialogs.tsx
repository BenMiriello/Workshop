import { ConfirmDialog } from "@workshop/ui";
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
          the context modal and neither is obvious from its name: compact
          summarizes, clear drops. One line each, said where it matters. */}
      <ConfirmDialog
        open={confirmCompact}
        onCancel={onCancelCompact}
        onConfirm={onConfirmCompact}
        title="Compact this conversation?"
        body={
          <span>
            Replace chat history with a context summary.
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
            Previous messages will still be kept but not included in chat context.
          </span>
        }
        confirmLabel="Clear context"
        danger
      />
    </>
  );
}
