import {
  Bell, Blocks, Bot, ChevronDown, ChevronLeft, FolderGit2, Home, ListPlus, ListTodo,
  Maximize2, MessagesSquare, MoreVertical, PanelsTopLeft, Plus, Trash2,
} from "lucide-react";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "../../ui";
import type { Provider } from "../../lib/chat";
import {
  SCOPE_GROUPS, scopeKind, scopeLabel, scopeName,
  type ChatMode, type ChatVariant, type ScopeKind,
} from "./chatScope";
import type { ChatDrawer } from "./drawers";
import { CHAT_MODES, chatModeDef, type ChatModeId } from "./chatModes";

// One icon per scope KIND, not per scope: the icon says what kind of place a
// row is, and the name says which one. Sixteen project rows all carrying the
// same folder is the point — that is how you see the group at a glance.
function ScopeIcon({ kind }: { kind: ScopeKind }) {
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
  onBack, onOpenWorkbench, onFileTicket, onNewChat, onDelete, onPopOut, onNavigate,
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
          label="Scope"
          align="start"
          trigger={
            <button className="chat-tb__picker" title="Which scope this chat is in">
              <span className="chat-tb__picker-val">{scopeLabel(activeProvider)}</span>
              <ChevronDown size={14} aria-hidden className="chat-tb__picker-caret" />
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
        <span className="chat-tb__picker is-static">{scopeLabel(providers[0].key)}</span>
      ) : null}

      {/* How this chat handles work. It belongs beside the scope picker for the
          same reason the scope picker belongs here: it changes the whole
          surface, not one turn. Commits on change — cheap, reversible, and
          re-stated to the model every turn so a switch takes effect
          mid-conversation. Labels and icons come from chatModes, so the trigger
          and the menu cannot describe the same mode differently.

          On a narrow viewport the trigger keeps the ICON and drops the word: the
          toolbar has more controls than a phone has room for, and an icon that
          names its mode carries it. The word is always present where you are
          choosing — in the menu — which is where you need to read it. */}
      {activeId && onChooseChatMode && (
        <Menu
          label="How work is handled"
          align="start"
          trigger={
            <button className="chat-tb__picker" title="How this chat handles work">
              {(() => { const { Icon } = chatModeDef(chatMode); return <Icon size={14} aria-hidden />; })()}
              <span className="chat-tb__picker-val chat-tb__picker-val--wide">
                {chatModeDef(chatMode).label}
              </span>
              <ChevronDown size={14} aria-hidden className="chat-tb__picker-caret" />
            </button>
          }
        >
          {CHAT_MODES.map(({ id, label, hint, Icon }) => (
            <MenuItem
              key={id}
              icon={<Icon size={14} aria-hidden />}
              checked={chatModeDef(chatMode).id === id}
              hint={hint}
              onSelect={() => onChooseChatMode(id)}
            >
              {label}
            </MenuItem>
          ))}
        </Menu>
      )}

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
