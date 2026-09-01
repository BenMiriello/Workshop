import { useRef } from "react";
import { Archive, GitBranch, MoreVertical, Trash2 } from "lucide-react";
import { Card, Menu, MenuItem, MenuSeparator, Tag, catFor } from "@workshop/ui";
import type { ThreadSummary } from "../../lib/chat";
import { scopeKind, scopeLabel, scopeName } from "./chatScope";
import { chatTags } from "../../pages/chatList";
import { pulseDelay } from "../../pages/chatPulse";
import ScopeIcon from "./ScopeIcon";
import { appIcon } from "../../apps/registry";
import { AppGlyph } from "../../lib/appIcon";

// ONE CHAT ROW, for every surface that lists chats.
//
// There were two. The global list at /chats rendered this: a live-state dot,
// the scope's glyph, the title, the model, the turn count, the age, and the
// last message underneath. The per-app Chats tab rendered a three-column grid
// of cards holding a title and nothing else, so the same conversation looked
// like a different kind of object depending on which page you opened, and the
// app's version could not answer "when" or "how much" at all. Reported
// 2026-08-29: "why does it still take me to the wrong chats page? Some old
// chats page why does it still exist even?"
//
// So the row lives here and both pages render it. A second chat list is not a
// feature of the app view; it was a copy that stopped being updated.
//
// The row OWNS no data and no requests. Archive and delete are handed in,
// because who may archive and what a delete confirms differ by page, and a row
// that fetched for itself would make a list of fifty rows fifty callers.

/** The model that answered, shortened to family and version: "opus 4.8". */
export function shortModel(m?: string | null): string {
  if (!m) return "";
  const s = m.replace(/^claude-/, "").replace(/-\d{6,}$/, "");
  const parts = s.split("-").filter(Boolean);
  if (parts.length === 0) return s;
  const ver = parts.slice(1).join(".");
  return ver ? `${parts[0]} ${ver}` : parts[0];
}

/** How long ago a chat was last active, at the resolution a list is scanned at:
 *  recent conversations want minutes and hours, older ones settle to days and
 *  weeks. Reads the field the list is sorted by, so the time on a row and its
 *  position agree. */
export function relTime(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const secs = Math.max(0, (Date.now() - t) / 1000);
  if (secs < 60) return "now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  if (days < 7) return `${days}d`;
  if (days < 30) return `${Math.floor(days / 7)}w`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

// The glyph that leads a row. An app chat shows the app's OWN declared icon,
// resolved through the apps registry. Every other scope, and any app that
// declared no icon, shows the kind icon, which is the reliable backup.
export function ChatScopeGlyph({ contextKey }: { contextKey: string }) {
  const kind = scopeKind(contextKey);
  if (kind === "app") {
    const declared = appIcon(scopeName(contextKey));
    if (declared) return <AppGlyph icon={declared} name={scopeName(contextKey)} size={14} />;
  }
  return <ScopeIcon kind={kind} />;
}

// The live-state dot: a hollow ring at rest, filled and pulsing while a turn
// runs, on the orchestrated, offset phase from chatPulse.
export function RunningDot({ running, ordinal }: { running: boolean; ordinal: number }) {
  // Compute the delay once, on the transition into running, and hold it; clear
  // it on the way out so a later restart gets a fresh phase. Every other render
  // reuses the same string, so the inline style is stable and the running
  // animation is never disturbed by a poll re-render or an ordinal shift.
  const delay = useRef<string | undefined>(undefined);
  if (running && delay.current === undefined) delay.current = pulseDelay(ordinal);
  else if (!running && delay.current !== undefined) delay.current = undefined;
  return (
    <span
      className="ws-chat-row__state"
      data-live={running ? "" : undefined}
      style={running ? { animationDelay: delay.current } : undefined}
      aria-hidden
    />
  );
}

export default function ChatRow({
  thread, ordinal = 0, onOpen, onArchive, onDelete,
}: {
  thread: ThreadSummary;
  /** Position among the RUNNING rows, so their pulses are offset rather than
   *  strobing in unison. 0 when nothing is running. */
  ordinal?: number;
  onOpen: (t: ThreadSummary) => void;
  /** Omitted where the surface does not offer archiving; the control and the
   *  menu item both disappear with it rather than sitting there inert. */
  onArchive?: (t: ThreadSummary) => void;
  onDelete?: (t: ThreadSummary) => void;
}) {
  const t = thread;
  const tags = chatTags(t);
  return (
    <Card
      interactive
      compact
      className="ws-chat-row"
      onClick={() => onOpen(t)}
      // min-width:0 lets the two ellipsizing lines shrink instead of setting
      // the card's min-content width and overflowing the viewport on mobile.
      style={{ cursor: "pointer", minWidth: 0 }}
    >
      {/* Line one: the live-state dot, the scope's own icon (its name on
          hover), the chat name, then the meta right-justified. Line two: the
          last message. Two lines at every width. */}
      <div className="ws-chat-row__head">
        <RunningDot running={!!t.running} ordinal={ordinal} />
        <span
          className="ws-chat-row__scope"
          title={scopeName(t.context_key)}
          aria-label={scopeLabel(t.context_key)}
        >
          <ChatScopeGlyph contextKey={t.context_key} />
        </span>
        <strong className="ws-chat-row__title">{t.title || "New chat"}</strong>
        <div className="ws-chat-row__meta">
          {t.model && (
            <>
              <span className="ws-card__meta ws-chat-row__model">{shortModel(t.model)}</span>
              <span className="ws-chat-row__sep ws-chat-row__sep--model" aria-hidden>·</span>
            </>
          )}
          {/* A count is only shown when there is one. A surface whose source
              does not carry it must not print "undefined turns" or claim 0. */}
          {typeof t.message_count === "number" && t.message_count > 0 && (
            <>
              <span className="ws-card__meta">{t.message_count} turns</span>
              <span className="ws-chat-row__sep" aria-hidden>·</span>
            </>
          )}
          <span className="ws-card__meta ws-chat-row__when">{relTime(t.updated_at)}</span>
          {/* THE BRANCH IS A TAG NOW, on the line below with scope and role
              (`threads-branches-and-pages-2026-08-29.md` §6: "A branch is a
              small tag on the row, next to scope and role"). It was an icon
              here, with the name only on the title attribute, so on a phone
              there was no hover and the row could not answer which branch at
              all. §3 is the reason it is a name and not a glyph: the interface
              says branch, and a branch has one. */}
          {onArchive && (
            <button
              type="button"
              className="ws-chat-row__act ws-chat-row__archive"
              title="Archive"
              aria-label="Archive chat"
              onClick={(e) => { e.stopPropagation(); onArchive(t); }}
            >
              <Archive size={14} aria-hidden />
            </button>
          )}
          {/* The menu opens on its own; the stop keeps its trigger from also
              opening the chat behind it. */}
          {(onArchive || onDelete) && (
            <span onClick={(e) => e.stopPropagation()}>
              <Menu
                label="Chat actions"
                trigger={
                  <button type="button" className="ws-chat-row__act" title="More" aria-label="More chat actions">
                    <MoreVertical size={14} aria-hidden />
                  </button>
                }
              >
                {onArchive && (
                  <MenuItem icon={<Archive size={14} aria-hidden />} onSelect={() => onArchive(t)}>
                    Archive
                  </MenuItem>
                )}
                {onArchive && onDelete && <MenuSeparator />}
                {onDelete && (
                  <MenuItem
                    danger
                    icon={<Trash2 size={14} aria-hidden />}
                    onSelect={() => onDelete(t)}
                  >
                    Delete
                  </MenuItem>
                )}
              </Menu>
            </span>
          )}
        </div>
      </div>
      {/* Line two: the last message, and the row's tags trailing it.
          WHAT A THREAD IS, IN THREE TAGS: its scope, its role, its branch, in
          that order, each omitted when the thread does not have one (§6, and
          §1 for why a null role prints nothing rather than a default). The
          derivation is `chatTags`, so both chat lists tag a thread identically
          and the rule can be executed in a test.
          `ws-tagstrip` is the kit's row-of-tags container and it is what keeps
          the narrow column safe: one line that scrolls, never a wrap, so a row
          cannot change height or overflow because a branch has a long name.
          The snippet keeps a floor and gives up the rest (see below); it
          ellipsizes as it always did. */}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", minWidth: 0 }}>
        {/* `1 1 8rem`, and the basis is the whole point: at `auto` the flex
            basis is the message's own length, which on a 390px phone is far
            longer than the line, so the snippet claimed the width and the tags
            were shrunk to a clipped dot. A fixed basis makes the snippet ASK
            for 8rem and take the rest only when there is a rest. Measured in a
            390px shot, which is the only thing that catches this. */}
        <p className="ws-muted ws-chat-row__snippet" style={{ flex: "1 1 8rem", width: "auto" }}>
          {t.last_message || " "}
        </p>
        {tags.length > 0 && (
          <span className="ws-tagstrip" style={{ flex: "0 1 auto" }}>
            {tags.map((tag) => (
              <Tag key={`${tag.kind}:${tag.label}`} hue={catFor(tag.label)} variant="neutral">
                {tag.kind === "branch" && <GitBranch size={11} aria-hidden />}
                {tag.label}
              </Tag>
            ))}
          </span>
        )}
      </div>
    </Card>
  );
}
