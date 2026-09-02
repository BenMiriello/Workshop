import { useRef } from "react";
import { Archive, ArchiveRestore, MoreVertical, Trash2 } from "lucide-react";
import { Menu, MenuItem, MenuSeparator } from "@workshop/ui";
import type { ThreadSummary } from "../../lib/chat";
import { scopeKind, scopeName } from "./chatScope";
import { chatRowFacts } from "../../pages/chatList";
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
  thread, ordinal = 0, showScope = true, time, onOpen, onArchive, onRestore, onDelete,
}: {
  thread: ThreadSummary;
  /** Whether to tag this row with its scope. FALSE on a list that is all one
   *  scope, where the tag is the page's own title repeated once per row. The
   *  caller decides by asking the ROWS (`spansScopes` in pages/chatList), not
   *  by asking the filter. */
  showScope?: boolean;
  /** Position among the RUNNING rows, so their pulses are offset rather than
   *  strobing in unison. 0 when nothing is running. */
  ordinal?: number;
  /** What the row's time column says, when the surface is not ordered by
   *  activity. The archive is the caller: its list is most-recently-archived
   *  first, so "3d" (last activity) beside a row filed yesterday is a time that
   *  disagrees with the row's own position. */
  time?: string;
  /** OPTIONAL, because an archived chat is not a link. There is no way in from
   *  the archive on purpose: opening one would have to either un-archive it
   *  silently or drop you in a conversation you cannot send to. Without this
   *  the row renders its title as plain text and takes no click. */
  onOpen?: (t: ThreadSummary) => void;
  /** The three verbs. Each is omitted where the surface does not offer it, and
   *  the control and its menu item disappear together rather than sitting
   *  there inert. The live list hands in archive and delete; the archive hands
   *  in restore and delete. */
  onArchive?: (t: ThreadSummary) => void;
  onRestore?: (t: ThreadSummary) => void;
  onDelete?: (t: ThreadSummary) => void;
}) {
  const t = thread;
  // PLAIN TEXT, NOT CHIPS. components.css beside `.ws-listrow__meta`: "Scope
  // and age are things you read once you are already looking at a row; a chip
  // is for something you scan FOR."
  const facts = chatRowFacts(t, { showScope, shortModel });
  return (
    <div
      className={"ws-listrow" + (onOpen ? "" : " ws-listrow--static")}
      onClick={onOpen ? () => onOpen(t) : undefined}
    >
      {/* The live dot is the row's mark, exactly where a ticket's status dot
          is. It is the one thing on a chat row worth scanning for. */}
      <span className="ws-listrow__mark">
        <RunningDot running={!!t.running} ordinal={ordinal} />
      </span>
      {/* THE HIT TARGET IS A BUTTON, so the list is reachable by keyboard. The
          row around it carries the shape; a button inside a button would be
          invalid, which is why the actions are siblings and not children. */}
      {onOpen ? (
        <button
          type="button"
          className="ws-listrow__hit"
          title={t.title || "New chat"}
          onClick={(e) => { e.stopPropagation(); onOpen(t); }}
        >
          {t.title || "New chat"}
        </button>
      ) : (
        <span className="ws-listrow__hit" title={t.title || "New chat"}>
          {t.title || "New chat"}
        </span>
      )}
      <span className="ws-listrow__meta">
        {facts.map((f) => (
          <span key={f} className="ws-listrow__scope">{f}</span>
        ))}
        <span className="ws-listrow__time" title={t.updated_at ?? undefined}>
          {time ?? relTime(t.updated_at)}
        </span>
      </span>
      {(onArchive || onRestore || onDelete) && (
        <span
          className="ws-listrow__actions"
          onClick={(e) => e.stopPropagation()}
        >
          {onArchive && (
            <button
              type="button"
              className="ws-listrow__act"
              title="Archive"
              aria-label="Archive chat"
              onClick={() => onArchive(t)}
            >
              <Archive size={14} aria-hidden />
            </button>
          )}
          {onRestore && (
            <button
              type="button"
              className="ws-listrow__act"
              title="Restore"
              aria-label="Restore chat"
              onClick={() => onRestore(t)}
            >
              <ArchiveRestore size={14} aria-hidden />
            </button>
          )}
          <Menu
            label="Chat actions"
            trigger={
              <button type="button" className="ws-listrow__act" title="More" aria-label="More chat actions">
                <MoreVertical size={14} aria-hidden />
              </button>
            }
          >
            {onArchive && (
              <MenuItem icon={<Archive size={14} aria-hidden />} onSelect={() => onArchive(t)}>
                Archive
              </MenuItem>
            )}
            {onRestore && (
              <MenuItem icon={<ArchiveRestore size={14} aria-hidden />} onSelect={() => onRestore(t)}>
                Restore
              </MenuItem>
            )}
            {(onArchive || onRestore) && onDelete && <MenuSeparator />}
            {onDelete && (
              <MenuItem danger icon={<Trash2 size={14} aria-hidden />} onSelect={() => onDelete(t)}>
                Delete
              </MenuItem>
            )}
          </Menu>
        </span>
      )}
      {/* The last message. One line, muted, ellipsized: a conversation's
          preview is the one thing a ticket row has no equivalent of, and it is
          why this list carried its own row shape for so long. It does not get
          to change the row's height. */}
      {t.last_message ? (
        <span className="ws-listrow__sub">{t.last_message}</span>
      ) : null}
    </div>
  );
}
