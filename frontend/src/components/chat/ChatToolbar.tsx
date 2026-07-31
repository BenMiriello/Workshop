import {
  Bell, ChevronDown, ChevronLeft, ListPlus, ListTodo, Maximize2, MessagesSquare,
  MoreVertical, PanelsTopLeft, Plus, Trash2,
} from "lucide-react";
import { Menu, MenuItem, MenuSeparator } from "../../ui";
import type { Provider } from "../../lib/chat";
import type { ChatMode, ChatVariant } from "./chatScope";
import type { ChatDrawer } from "./drawers";

// The chat header: where you are on the left, what you can do on the right.
//
// WHAT IS DELIBERATELY NOT HERE:
//
//  - The conversation picker. It duplicated the chat list you reach with back or
//    "All chats", and its one unique job — starting a new chat — is the + button.
//  - The model picker. That is a PER-TURN knob and lives in the composer with
//    the other per-turn controls. The one picker that belongs here is the scope
//    picker, because changing it changes what the chat can see and which thread
//    list you are in: it changes the whole surface.
//  - A second ticket button. File-as-ticket and file-and-run were one verb plus
//    a boolean, rendered as two unlabelled icons sharing a busy lock — so while
//    the first spun for 35-55s the second silently did nothing, and both filed
//    blind. One button now opens a review step.
//
// The overflow menu holds only what is genuinely rare or destructive. Actions
// you take ON a conversation stay on the row where you can see them.

export default function ChatToolbar({
  variant, mode, slug, hideExpand,
  activeId, activeThreadTitle,
  showProviderPicker, providers, activeProvider, activeProviderLabel, onChooseProvider,
  drawer, onSetDrawer, inboxCount, taskScope, openTaskCount,
  onBack, onOpenWorkbench, onFileTicket, onNewChat, onDelete, onPopOut, onNavigate,
}: {
  variant: ChatVariant;
  mode: ChatMode;
  slug: string | null;
  hideExpand?: boolean;
  activeId: string | null;
  activeThreadTitle?: string;
  showProviderPicker: boolean;
  providers: Provider[];
  activeProvider: string;
  activeProviderLabel: string;
  onChooseProvider: (key: string) => void;
  drawer: ChatDrawer;
  onSetDrawer: (next: (d: ChatDrawer) => ChatDrawer) => void;
  inboxCount: number;
  taskScope: string | null;
  openTaskCount: number;
  onBack: () => void;
  onOpenWorkbench: () => void;
  onFileTicket: () => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onPopOut: () => void;
  onNavigate: (to: string) => void;
}) {
  const inboxOpen = drawer === "inbox";
  const tasksOpen = drawer === "tasks";
  const toggle = (which: Exclude<ChatDrawer, null>) =>
    onSetDrawer((d) => (d === which ? null : which));

  return (
    <div className="chat-toolbar">
      {variant === "full" && (
        <button className="chat-tb__btn" onClick={onBack} title="Back" aria-label="Back">
          <ChevronLeft size={16} aria-hidden />
        </button>
      )}
      {/* Build agents: open the full workbench (files / editor / preview / diff).
          Only in the full chat — inside the workbench the chat is already
          embedded, so the button would be redundant. */}
      {mode === "build" && slug && variant === "full" && (
        <button
          className="chat-tb__btn"
          onClick={onOpenWorkbench}
          title="Open workbench (files · editor · preview · diff)"
          aria-label="Open workbench"
        >
          <PanelsTopLeft size={16} aria-hidden />
        </button>
      )}

      {showProviderPicker && providers.length > 1 && mode === "data" ? (
        <Menu
          label="Assistant"
          align="start"
          trigger={
            <button className="chat-tb__picker" title="Which assistant to talk to">
              <span className="chat-tb__picker-val">{activeProviderLabel}</span>
              <ChevronDown size={14} aria-hidden className="chat-tb__picker-caret" />
            </button>
          }
        >
          {providers.map((p) => (
            <MenuItem key={p.key} checked={p.key === activeProvider} onSelect={() => onChooseProvider(p.key)}>
              {p.label}
            </MenuItem>
          ))}
        </Menu>
      ) : showProviderPicker && providers.length === 1 && mode === "data" ? (
        <span className="chat-tb__picker is-static">{providers[0].label}</span>
      ) : null}

      {/* Which conversation you are in — a label, not a control. */}
      {activeThreadTitle && <span className="chat-tb__title">{activeThreadTitle}</span>}
      {/* Pushes the actions right on desktop; on mobile the toolbar wraps and
          they flow onto the next line. */}
      <span className="chat-tb__spacer" />

      {activeId && (
        <button
          className="chat-tb__btn"
          onClick={onFileTicket}
          title="File this chat as a ticket"
          aria-label="File as a ticket"
        >
          <ListPlus size={16} aria-hidden />
        </button>
      )}
      {activeId && (
        <button
          className={`chat-tb__btn chat-tb__tasks${inboxOpen ? " is-active" : ""}`}
          onClick={() => toggle("inbox")}
          title={inboxCount ? `${inboxCount} waiting on you` : "Nothing waiting"}
          aria-label="Agent inbox"
          aria-expanded={inboxOpen}
        >
          <Bell size={16} aria-hidden />
          {inboxCount > 0 && <span className="chat-tb__badge">{inboxCount}</span>}
        </button>
      )}
      {taskScope && (
        <button
          className={`chat-tb__btn chat-tb__tasks${tasksOpen ? " is-active" : ""}`}
          onClick={() => toggle("tasks")}
          title="Tasks"
          aria-label="Tasks"
          aria-expanded={tasksOpen}
        >
          <ListTodo size={16} aria-hidden />
          {openTaskCount > 0 && <span className="chat-tb__badge">{openTaskCount}</span>}
        </button>
      )}
      <button className="chat-tb__btn" onClick={onNewChat} title="Start a new chat" aria-label="New chat">
        <Plus size={16} aria-hidden />
      </button>

      <Menu
        label="Chat actions"
        align="end"
        trigger={
          <button className="chat-tb__btn" title="More" aria-label="More chat actions">
            <MoreVertical size={16} aria-hidden />
          </button>
        }
      >
        <MenuItem icon={<MessagesSquare size={14} />} onSelect={() => onNavigate("/chats")}>
          All chats
        </MenuItem>
        {variant !== "full" && !hideExpand && (
          <MenuItem icon={<Maximize2 size={14} />} onSelect={onPopOut}>
            Open full screen
          </MenuItem>
        )}
        {activeId && (
          <>
            <MenuSeparator />
            <MenuItem danger icon={<Trash2 size={14} />} onSelect={() => onDelete(activeId)}>
              Delete chat…
            </MenuItem>
          </>
        )}
      </Menu>
    </div>
  );
}
