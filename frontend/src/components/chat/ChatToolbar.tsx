import {
  Archive, ChevronLeft, Maximize2, MessagesSquare,
  FolderTree, MoreVertical, Plus, Share2, Ticket,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@workshop/ui";
import type { Provider, ThreadSummary } from "../../lib/chat";
import {
  SCOPE_GROUPS, scopeKind, scopeLabel, scopeName,
  type ChatMode, type ChatVariant,
} from "./chatScope";
import type { ChatDrawer } from "./drawers";
import { getPolicy, type MemoryPolicy } from "../../lib/memoryPolicy";
import { keepFocusOnTap } from "./keepFocus";
import { ChatShareDialog } from "./ChatShare";
import RecentChatsMenu from "./RecentChatsMenu";
import ScopeIcon from "./ScopeIcon";
import { workTitle } from "./work/workRows";

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
//    apart. The bell and the Tasks drawer became ONE drawer named Tickets, and
//    the mode toggle moved into the overflow.
//  - ANY work control at all, as of 2026-08-15. The overflow used to hold the
//    last two, and both are gone from it:
//
//      "Run work as tickets", a Toggle. It moved into Work settings, one tap
//      inside the panel that lists what the policy produces, rather than
//      sitting on the bar as a switch with no room to say what either side
//      does. (It was briefly a three-way choice, when "draft it and ask me"
//      and "file it and run it" were separate modes; they were one behaviour
//      under two names and were collapsed on 2026-08-27.)
//
//      "File this chat as a ticket". An action on this chat's work, sitting two
//      controls away from the list of this chat's work. It is the panel header's
//      action now. The dialog it opens (TicketDraftsDialog) is unchanged: only
//      the entry point moved.
//
// So the Tickets button is the ONLY work affordance on this bar. It opens the
// sheet (WorkPanel, mounted in the log-wrap) at every width via `onWorkClick`.
// The toolbar keeps the badge, and the badge's load-bearing rule is unchanged:
// it counts what is ABOUT THIS CONVERSATION, never the scope's open tickets.
//
// The overflow menu now holds only what is genuinely rare or destructive:
// All chats, Open full screen, Archive chat. Actions you take ON a conversation
// stay on the row where you can see them.

export default function ChatToolbar({
  variant, mode,
  activeId, activeThreadTitle, branchPicker, branchPill,
  showProviderPicker, providers, activeProvider, onChooseProvider, turnActive,
  drawer, workCount, needsYouCount, onWorkClick,
  onBack, onOpenFiles, onEditTitle, onOpenThread,
  onNewChat, onArchive, onPopOut, onNavigate,
}: {
  variant: ChatVariant;
  mode: ChatMode;
  activeId: string | null;
  activeThreadTitle?: string;
  showProviderPicker: boolean;
  // The scopes this chat can be in. Still the provider directory, because a
  // provider key IS a scope key (`general`, an app id, `project:<slug>`,
  // `agent:<slug>`) — what changes here is that they are named and grouped as
  // the places they are, instead of listed as assistants to talk to.
  providers: Provider[];
  activeProvider: string;
  onChooseProvider: (key: string) => void;
  // A turn is live. Rescope drops the session it's speaking through, so every
  // OTHER row is disabled (not hidden — the picker still shows where you'd go)
  // rather than left selectable and refused after the fact.
  turnActive: boolean;
  /** Which drawer is open. Read for the button's `is-active` tone only: the
   *  panel owns opening and closing itself, through `workAnchor`. */
  drawer: ChatDrawer;
  /** The BADGE. What this conversation is carrying: the work it filed and has
   *  not finished, plus whatever is waiting on the operator here, counted as a
   *  union so one blocked ticket is one. Built by `chatBadgeCount`, which holds
   *  the reasoning.
   *
   *  IT IS NOT THE SCOPE'S OPEN-TICKET COUNT AND MUST NOT BECOME ONE. A badge
   *  is an alarm, forty open tickets is the normal state of a working queue,
   *  and a scope-wide number is the same in every chat of that scope: the
   *  operator reported exactly that on 2026-08-23, an unchanging 13 beside a
   *  button that claimed to be about the conversation he was in. */
  workCount: number;
  /** How much of `workCount` is actually waiting on the operator. Used for the
   *  tooltip's wording only, so that "3" can say which kind of three it is. */
  needsYouCount: number;
  /** Wraps the Tickets button in the Work panel. A render prop rather than
   *  eight forwarded props, because the toolbar's business is the BUTTON: it
   *  builds the trigger and knows nothing about what hangs off it. */
  /** Opens the sheet panel. Passed through rather than held here so Chat.tsx
   *  can apply requestAnimationFrame to avoid layout thrash on tap. */
  onWorkClick?: () => void;
  onBack: () => void;
  onOpenFiles: (() => void) | null;
  onEditTitle: () => void;
  /** Switch to another conversation from the name menu. It may live under a
   *  different scope, so the caller routes rather than setting local state. */
  onOpenThread: (t: ThreadSummary) => void;
  onNewChat: () => void;
  /** Close this chat: it goes to the archive, from where it can be restored
   *  or deleted. Nothing here destroys a conversation. */
  /** Which LINE OF THE CONVERSATION this is, and the way to the others (ADR
   *  0020). A node rather than three props: the toolbar does not need to know
   *  what a line is, only where the control goes. Null until the thread has
   *  more than one line, which is most threads. */
  branchPicker?: ReactNode;
  /** Which BRANCH the next turn runs on, and the way to move it
   *  (threads-branches-and-pages-2026-08-29.md section 3). A node for the same
   *  reason `branchPicker` is: the toolbar's business is where the control
   *  goes, not what a lease is. Null for every chat with no project tree of its
   *  own, which the pill decides for itself. */
  branchPill?: ReactNode;
  onArchive: (id: string) => void;
  onPopOut: () => void;
  onNavigate: (to: string) => void;
}) {
  const workOpen = drawer === "work";
  // The sharing dialog, and the policy it edits. Fetched on FIRST OPEN and not
  // on mount: this toolbar is on screen in every chat and most chats are never
  // shared, so the request would be paid for on every conversation to serve a
  // dialog almost nobody opens. Failing to load is not surfaced, because the
  // dialog still works without it: an absent policy resolves to the stored
  // default, which is what the backend would apply anyway.
  const [sharing, setSharing] = useState(false);
  const [policy, setPolicy] = useState<MemoryPolicy | null>(null);
  useEffect(() => {
    if (!sharing || policy) return;
    let live = true;
    void getPolicy().then((p) => { if (live) setPolicy(p); }).catch(() => {});
    return () => { live = false; };
  }, [sharing, policy]);

  return (
    // Same rule as the composer's action row: with the keyboard up, a tap here
    // presses the button instead of spending itself closing the keyboard.
    <div className="chat-toolbar" onPointerDown={keepFocusOnTap}>
      {variant === "full" && (
        <button className="chat-tb__btn" onClick={onBack} title="Back" aria-label="Back">
          <ChevronLeft size={16} aria-hidden />
        </button>
      )}
      {/* The working tree this chat writes in, in the app that owns it. There
          used to be a second chat surface here (a "workbench" wrapping the same
          transcript in a file tree, an editor, a diff and a preview) and it was
          the last place a chat and an agent were two different things. It is
          gone; this is the link to where those four things actually live, and
          the chat you are reading is the only chat. */}
      {onOpenFiles && variant === "full" && (
        <button
          className="chat-tb__btn"
          onClick={onOpenFiles}
          title="Open this app's files"
          aria-label="Open this app's files"
        >
          <FolderTree size={16} aria-hidden />
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
                const locked = turnActive && !isCurrent;
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
            onEditTitle={onEditTitle}
          />
        </span>
      )}

      {/* Beside the chat's name, because it is the same kind of fact: which
          conversation you are in, then which line of it. Absent entirely until
          there is a second line to be on. */}
      {branchPicker}
      {/* Which BRANCH OF THE CODE, next to which line of the conversation.
          English calls both a branch, and threads-branches-and-pages-2026-08-29
          section 3 spends the word HERE: "the interface says branch". So this
          pill says branch and wears the branch glyph, and its neighbour above
          says "line" and wears a different one. The two used to be the same
          word and the same icon, six pixels apart. */}
      {branchPill}
      {/* Pushes the actions right on desktop; on a narrow viewport the title
          just left of it has already given up the room instead. */}
      <span className="chat-tb__spacer" />

      {/* ONE work button. `data-chat-workpanel-trigger` is how the sheet finds
          its way back here on Escape. ALWAYS SHOWN, including on a blank chat
          with no thread and no queue: the panel also holds the policy (where the
          first message's work goes), so gating it on thread existence hides it
          until after that decision has been made. */}
      {onWorkClick && (
        <button
          data-chat-workpanel-trigger=""
          className={`chat-tb__btn${workOpen ? " is-active" : ""}`}
          title={workTitle(workCount, needsYouCount)}
          aria-label="Tickets"
          aria-haspopup="dialog"
          aria-expanded={workOpen}
          onClick={onWorkClick}
        >
          <Ticket size={16} aria-hidden />
          {workCount > 0 && <span className="chat-tb__badge">{workCount}</span>}
        </button>
      )}
      <Menu
        label="Chat actions"
        trigger={
          <button className="chat-tb__btn" title="More" aria-label="More chat actions">
            <MoreVertical size={16} aria-hidden />
          </button>
        }
      >
        <MenuItem icon={<Plus size={14} />} onSelect={onNewChat}>
          New chat
        </MenuItem>
        <MenuSeparator />
        <MenuItem icon={<MessagesSquare size={14} />} onSelect={() => onNavigate("/chats")}>
          All chats
        </MenuItem>
        {variant !== "full" && (
          <MenuItem icon={<Maximize2 size={14} />} onSelect={onPopOut}>
            Open full screen
          </MenuItem>
        )}
        {activeId && (
          <>
            <MenuSeparator />
            {/* WHO ELSE FINDS THIS CHAT. A property of the conversation, so it
                is filed with the rest of what you can do to one. It arrived here
                on 2026-08-27 from a badge overlaid on a card in the Memory
                page's Chats tab, which went with that tab; see
                components/chat/ChatShare.tsx for the whole history. */}
            <MenuItem icon={<Share2 size={14} />} onSelect={() => setSharing(true)}>
              Sharing
            </MenuItem>
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

      {/* OUTSIDE the Menu: selecting an item closes it, which would unmount a
          dialog rendered as its child before it ever painted. The policy is
          fetched by the dialog's first open rather than on every toolbar mount,
          because most chats are never shared. */}
      {sharing && activeId && (
        <ChatShareDialog
          threadId={activeId}
          scope={activeProvider}
          title={activeThreadTitle || "Chat"}
          policy={policy}
          onSaved={setPolicy}
          onClose={() => setSharing(false)}
        />
      )}
    </div>
  );
}
