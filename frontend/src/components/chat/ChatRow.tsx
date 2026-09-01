import { useRef } from "react";
import { Archive, GitBranch, MoreVertical, Trash2 } from "lucide-react";
import { Card, Menu, MenuItem, MenuSeparator } from "@workshop/ui";
import type { ThreadSummary } from "../../lib/chat";
import { branchName } from "./chatBranch";
import { scopeKind, scopeLabel, scopeName } from "./chatScope";
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
          {/* THE WORD IS BRANCH. This said "In a worktree", which is the one
              string `REVIEW-plan-vs-reality-2026-08-29.md` §7.3 cited by name
              as drift from `threads-branches-and-pages-2026-08-29.md` §3 ("the
              interface says branch"). It was also the only place a person could
              read it here, because the tag is an icon and the label is what a
              screen reader gets. The hover text was the raw slug, which is 32
              characters of hex on half the branches this install serves;
              `branchName` is the same rule the toolbar's pill and its move list
              use. */}
          {t.branch_slug && (
            <>
              <span className="ws-chat-row__sep" aria-hidden>·</span>
              <span
                className="ws-card__meta"
                title={branchName(t)}
                aria-label={`Branch: ${branchName(t)}`}
              >
                <GitBranch size={12} aria-hidden />
              </span>
            </>
          )}
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
      <p className="ws-muted ws-chat-row__snippet">{t.last_message || " "}</p>
    </Card>
  );
}
