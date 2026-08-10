import {
  Bell, Blocks, Bot, ChevronLeft, CodeXml, FolderGit2, Home, ListPlus, ListTodo,
  Maximize2, MessagesSquare, MoreVertical, PanelsTopLeft, Plus, Ticket, Trash2,
} from "lucide-react";
import { Menu, MenuItem, MenuLabel, MenuSeparator, Toggle } from "../../ui";
import type { Provider } from "../../lib/chat";
import {
  SCOPE_GROUPS, scopeKind, scopeLabel, scopeName,
  type ChatMode, type ChatVariant, type ScopeKind,
} from "./chatScope";
import type { ChatDrawer } from "./drawers";
import { chatModeDef, type ChatModeId } from "./chatModes";
import { keepFocusOnTap } from "./keepFocus";

// One icon per scope KIND, not per scope: the icon says what kind of place a
// row is, and the name says which one. Sixteen project rows all carrying the
// same folder is the point — that is how you see the group at a glance.
function ScopeIcon({ kind }: { kind: ScopeKind }) {
  // Development is the source of the Workshop itself, so it takes the code
  // icon rather than a second house — the point of the row is that it is a
  // different KIND of place from the root scope, not another root.
  if (kind === "development") return <CodeXml size={14} aria-hidden />;
  if (kind === "project") return <FolderGit2 size={14} aria-hidden />;
  if (kind === "app") return <Blocks size={14} aria-hidden />;
  if (kind === "agent") return <Bot size={14} aria-hidden />;
  return <Home size={14} aria-hidden />;
}

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
//
//    That picker used to be labelled "Assistant" and rendered the provider
//    directory flat, so asking for a scope handed you a list of assistants —
//    with `project:<slug>` sitting in it wearing an assistant's label. The keys
//    were already scopes; only the framing was wrong. It now names and groups
//    them as places (Workshop / Projects / Apps / Agents). Which assistant
//    answers is derived from the scope server-side (`get_provider`), which is
//    the right way round: you pick a place, the place decides who replies.
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
  showProviderPicker, providers, activeProvider, onChooseProvider,
  chatMode, onChooseChatMode,
  drawer, onSetDrawer, inboxCount, taskScope, openTaskCount,
  onBack, onOpenWorkbench, onFileTicket, onEditTitle, onNewChat, onDelete, onPopOut, onNavigate,
}: {
  variant: ChatVariant;
  mode: ChatMode;
  slug: string | null;
  hideExpand?: boolean;
  activeId: string | null;
  activeThreadTitle?: string;
  showProviderPicker: boolean;
  // The scopes this chat can be in. Still the provider directory, because a
  // provider key IS a scope key (`general`, an app id, `project:<slug>`,
  // `build:<slug>`) — what changes here is that they are named and grouped as
  // the places they are, instead of listed as assistants to talk to.
  providers: Provider[];
  activeProvider: string;
  onChooseProvider: (key: string) => void;
  // A ChatModeId (see chatModes). Absent handler hides the control — a chat
  // with no thread yet has nothing to set it on.
  chatMode?: string;
  onChooseChatMode?: (mode: ChatModeId) => void;
  drawer: ChatDrawer;
  onSetDrawer: (next: (d: ChatDrawer) => ChatDrawer) => void;
  inboxCount: number;
  taskScope: string | null;
  openTaskCount: number;
  onBack: () => void;
  onOpenWorkbench: () => void;
  onFileTicket: () => void;
  onEditTitle: () => void;
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
    // Same rule as the composer's action row: with the keyboard up, a tap here
    // presses the button instead of spending itself closing the keyboard.
    <div className="chat-toolbar" onPointerDown={keepFocusOnTap}>
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

      {/* The place icon is always shown, on desktop and mobile alike — it is
          the one thing on this bar that says where a message you type here
          actually goes. The name beside it is the part a narrow viewport
          drops (`--wide`): the icon alone still answers "which kind of
          place", and the full name is one tap away in the menu. */}
      {showProviderPicker && providers.length > 1 && mode === "data" ? (
        <Menu
          label="Scope"
          trigger={
            <button className="chat-tb__picker" title="Which scope this chat is in">
              <ScopeIcon kind={scopeKind(activeProvider)} />
              <span className="chat-tb__picker-val chat-tb__picker-val--wide">
                {scopeLabel(activeProvider)}
              </span>
            </button>
          }
        >
          {SCOPE_GROUPS.flatMap(({ kind, label }) => {
            const inGroup = providers.filter((p) => scopeKind(p.key) === kind);
            if (!inGroup.length) return [];
            return [
              label ? <MenuLabel key={`h:${kind}`}>{label}</MenuLabel> : null,
              ...inGroup.map((p) => (
                <MenuItem
                  key={p.key}
                  icon={<ScopeIcon kind={kind} />}
                  checked={p.key === activeProvider}
                  onSelect={() => onChooseProvider(p.key)}
                >
                  {scopeName(p.key)}
                </MenuItem>
              )),
            ];
          })}
        </Menu>
      ) : showProviderPicker && providers.length === 1 && mode === "data" ? (
        <span className="chat-tb__picker is-static">
          <ScopeIcon kind={scopeKind(providers[0].key)} />
          <span className="chat-tb__picker-val chat-tb__picker-val--wide">
            {scopeLabel(providers[0].key)}
          </span>
        </span>
      ) : null}

      {/* Which conversation you are in — tap to rename it. Shown on mobile too
          now that the scope name and the mode picker no longer compete with
          it for room, and the one thing that shrinks when the row is tight. */}
      {activeThreadTitle && (
        <button className="chat-tb__title" onClick={onEditTitle} title="Rename chat">
          {activeThreadTitle}
        </button>
      )}
      {/* Pushes the actions right on desktop; on a narrow viewport the title
          just left of it has already given up the room instead. */}
      <span className="chat-tb__spacer" />

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

      {/* On/off, not a two-way choice: whether this chat's work leaves as
          tickets run by agents, or stays in the conversation. Commits on
          change — cheap, reversible, re-stated to the model every turn, so
          flipping it takes effect mid-conversation. Shown on a BLANK chat too
          (no `activeId` guard): the mode decides where the FIRST message's
          work goes, so hiding the control until a thread exists would hide it
          until after that decision had already been made — on a blank chat
          the choice is held client-side and applied when the thread is
          created. Filing this chat as a ticket is a one-off action on an
          existing conversation, so that row only appears once one exists;
          it folds in here instead of owning a second toolbar icon, which
          used to sit right next to this one wearing an almost-identical
          glyph. */}
      {onChooseChatMode && (
        <Menu
          label="Tickets"
          trigger={
            <button
              className={`chat-tb__btn${chatModeDef(chatMode).id === "orchestrated" ? " is-active" : ""}`}
              title={
                chatModeDef(chatMode).id === "orchestrated"
                  ? "Tickets on. Work leaves as tickets run by agents"
                  : "Tickets off. Work stays in this conversation"
              }
              aria-label="Tickets"
            >
              <Ticket size={16} aria-hidden />
            </button>
          }
        >
          <div className="ws-menu__row">
            <span>Tickets</span>
            <Toggle
              checked={chatModeDef(chatMode).id === "orchestrated"}
              onChange={(on) => onChooseChatMode(on ? "orchestrated" : "direct")}
            />
          </div>
          {activeId && (
            <MenuItem icon={<ListPlus size={14} aria-hidden />} onSelect={onFileTicket}>
              File this chat as a ticket
            </MenuItem>
          )}
        </Menu>
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
