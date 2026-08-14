import {
  Archive, ChevronLeft, ListPlus, Maximize2, MessagesSquare,
  MoreVertical, PanelsTopLeft, Pencil, Plus, Ticket,
} from "lucide-react";
import type { ReactNode } from "react";
import { Menu, MenuItem, MenuLabel, MenuSeparator, Toggle } from "@workshop/ui";
import type { Provider, ThreadSummary } from "../../lib/chat";
import {
  SCOPE_GROUPS, scopeKind, scopeLabel, scopeName,
  type ChatMode, type ChatVariant,
} from "./chatScope";
import type { ChatDrawer } from "./drawers";
import { chatModeDef, type ChatModeId } from "./chatModes";
import { keepFocusOnTap } from "./keepFocus";
import RecentChatsMenu from "./RecentChatsMenu";
import ScopeIcon from "./ScopeIcon";

// The chat header: where you are on the left, what you can do on the right.
//
// WHAT IS DELIBERATELY NOT HERE:
//
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
//  - A second work button, and this is the same mistake twice. There used to be
//    FOUR work affordances here: a bell (inbox drawer), a "Tasks" drawer, a
//    "Tickets" menu that was not a list at all (it held the mode toggle plus
//    file-as-ticket), and this overflow. "Tickets" and "Tasks" are synonyms on
//    two different KINDS of control, wearing near-identical glyphs six pixels
//    apart. The bell and the Tasks drawer are now ONE drawer named Tickets, and
//    the mode toggle moved into the overflow below.
//
// The overflow menu holds only what is genuinely rare or destructive, plus what
// is a property of the CONVERSATION rather than a thing you do to it: whether
// this chat's work leaves as tickets is a setting, and a setting does not get a
// toolbar icon. Actions you take ON a conversation stay on the row where you
// can see them.

export default function ChatToolbar({
  variant, mode, slug, hideExpand,
  activeId, activeThreadTitle, branchPicker,
  showProviderPicker, providers, activeProvider, onChooseProvider, runActive,
  chatMode, onChooseChatMode,
  drawer, onSetDrawer, needsYouCount, taskScope,
  onBack, onOpenWorkbench, onFileTicket, onEditTitle, onOpenThread,
  onNewChat, onArchive, onPopOut, onNavigate,
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
  // A turn is live. Rescope drops the session it's speaking through, so every
  // OTHER row is disabled (not hidden — the picker still shows where you'd go)
  // rather than left selectable and refused after the fact.
  runActive: boolean;
  // A ChatModeId (see chatModes). Absent handler hides the control — a chat
  // with no thread yet has nothing to set it on.
  chatMode?: string;
  onChooseChatMode?: (mode: ChatModeId) => void;
  drawer: ChatDrawer;
  onSetDrawer: (next: (d: ChatDrawer) => ChatDrawer) => void;
  // The BADGE. Inbox items waiting on the operator in this chat, and nothing
  // else. It deliberately does not count open tickets: a badge is an alarm, and
  // forty open tickets is the normal state of a working queue.
  needsYouCount: number;
  // The task-store scope this chat maps to, or null when it has no queue. The
  // drawer's second zone needs one; its first does not.
  taskScope: string | null;
  onBack: () => void;
  onOpenWorkbench: () => void;
  onFileTicket: () => void;
  onEditTitle: () => void;
  /** Switch to another conversation from the name menu. It may live under a
   *  different scope, so the caller routes rather than setting local state. */
  onOpenThread: (t: ThreadSummary) => void;
  onNewChat: () => void;
  /** Close this chat: it goes to the archive, from where it can be restored
   *  or deleted. Nothing here destroys a conversation. */
  /** Which line of the conversation this is, and the way to the others (ADR
   *  0020). A node rather than three props: the toolbar does not need to know
   *  what a branch is, only where the control goes. Null until the thread has
   *  more than one line, which is most threads. */
  branchPicker?: ReactNode;
  onArchive: (id: string) => void;
  onPopOut: () => void;
  onNavigate: (to: string) => void;
}) {
  const workOpen = drawer === "work";
  const toggle = (which: Exclude<ChatDrawer, null>) =>
    onSetDrawer((d) => (d === which ? null : which));
  const orchestrated = chatModeDef(chatMode).id === "orchestrated";

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
              ...inGroup.map((p) => {
                const isCurrent = p.key === activeProvider;
                const locked = runActive && !isCurrent;
                return (
                  <MenuItem
                    key={p.key}
                    icon={<ScopeIcon kind={kind} />}
                    checked={isCurrent}
                    disabled={locked}
                    title={locked ? "Scope cannot be changed mid-turn." : undefined}
                    onSelect={() => onChooseProvider(p.key)}
                  >
                    {scopeName(p.key)}
                  </MenuItem>
                );
              }),
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

      {/* Which conversation you are in, and the way back to the others. Shown on
          mobile too now that the scope name and the mode picker no longer
          compete with it for room, and the one thing that shrinks when the row
          is tight.

          The name OPENS THE RECENT LIST; the pencil renames. It used to be one
          button doing the rename, which put the rarer of the two verbs on the
          bigger target and left switching chats to a round trip through /chats.
          Two controls, the common one first. */}
      {activeThreadTitle && (
        // ONE CLUSTER, so the toolbar's own gap spaces the PAIR against its
        // neighbours instead of spacing the pencil away from the name it acts
        // on. The pencil was a `chat-tb__btn` sitting a full --space-2 from the
        // title, which is the same gap used between unrelated actions at the far
        // end of the bar, so it read as one more button that happened to land
        // there. Grouping is what fixes that; a negative margin would too, and
        // the note on .chat-tb__title records why that was the wrong tool.
        <span className="chat-tb__namegroup">
          <RecentChatsMenu
            title={activeThreadTitle}
            activeId={activeId}
            onOpenThread={onOpenThread}
            onSeeAll={() => onNavigate("/chats")}
          />
          <button
            className="chat-tb__rename"
            onClick={onEditTitle}
            title="Rename chat"
            aria-label="Rename chat"
          >
            <Pencil size={12} aria-hidden />
          </button>
        </span>
      )}

      {/* Beside the chat's name, because it is the same kind of fact: which
          conversation you are in, then which line of it. Absent entirely until
          there is a second line to be on. */}
      {branchPicker}
      {/* Pushes the actions right on desktop; on a narrow viewport the title
          just left of it has already given up the room instead. */}
      <span className="chat-tb__spacer" />

      {/* ONE work button. It opens the one drawer, which holds both zones:
          what needs the operator, and what is under way in this scope. The
          badge is the first zone only. Shown once there is a thread to have an
          inbox for, or a scope to have a queue in. */}
      {(activeId || taskScope) && (
        <button
          className={`chat-tb__btn${workOpen ? " is-active" : ""}`}
          onClick={() => toggle("work")}
          title={needsYouCount ? `Tickets. ${needsYouCount} waiting on you` : "Tickets"}
          aria-label="Tickets"
          aria-expanded={workOpen}
        >
          <Ticket size={16} aria-hidden />
          {needsYouCount > 0 && <span className="chat-tb__badge">{needsYouCount}</span>}
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
        {/* On/off, not a two-way choice: whether this chat's work leaves as
            tickets run by agents, or stays in the conversation. Commits on
            change — cheap, reversible, re-stated to the model every turn, so
            flipping it takes effect mid-conversation. Shown on a BLANK chat too
            (no `activeId` guard): the mode decides where the FIRST message's
            work goes, so hiding the control until a thread exists would hide it
            until after that decision had already been made — on a blank chat
            the choice is held client-side and applied when the thread is
            created.

            It is worded as what it DOES rather than as "Tickets": that word now
            belongs to the drawer that lists tickets, and two controls sharing a
            name is the collision this pass exists to remove. */}
        {onChooseChatMode && (
          <>
            <div className="ws-menu__row" title={chatModeDef(chatMode).title}>
              <span>Run work as tickets</span>
              <Toggle
                checked={orchestrated}
                onChange={(on) => onChooseChatMode(on ? "orchestrated" : "direct")}
              />
            </div>
            {/* Filing this chat as a ticket is a one-off action on an existing
                conversation, so it only appears once one exists. */}
            {activeId && (
              <MenuItem icon={<ListPlus size={14} aria-hidden />} onSelect={onFileTicket}>
                File this chat as a ticket
              </MenuItem>
            )}
            <MenuSeparator />
          </>
        )}
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
            {/* ARCHIVE, NOT DELETE, and no confirmation. Closing a chat is a
                thing the operator does constantly; destroying one is rare. The
                common gesture is the reversible one, and a reversible action
                that stops to ask is just friction. The delete lives in the
                archive, behind a dialog, where it belongs. */}
            <MenuItem icon={<Archive size={14} />} onSelect={() => onArchive(activeId)}>
              Archive chat
            </MenuItem>
          </>
        )}
      </Menu>
    </div>
  );
}
