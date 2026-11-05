import { Card } from "@workshop/ui";
import type { ThreadSummary } from "../../lib/chat";
import { cardDate, messageCount, plainSnippet } from "../../pages/chatCard";
import { scopeLabel, scopeName } from "./chatScope";
import { ChatMenu, ChatScopeGlyph, RunningDot } from "./ChatRow";

// ONE CHAT AS A CARD, for the chat list's grid. The same thread the row shows
// and the same verbs, laid out to be read rather than scanned: the title, the
// first three lines of the last message as plain text, then the date and how
// many messages it holds. The live dot and the scope's glyph lead the title
// exactly as they lead a row, so the two layouts read as one list.
export default function ChatCard({
  thread: t, ordinal = 0, snippet, onOpen, onArchive, onDelete,
}: {
  thread: ThreadSummary;
  /** A search's passage, shown in place of the last message. */
  snippet?: string | null;
  /** Position among the running cards, for the offset pulse (see ChatRow). */
  ordinal?: number;
  onOpen: (t: ThreadSummary) => void;
  onArchive?: (t: ThreadSummary) => void;
  onDelete?: (t: ThreadSummary) => void;
}) {
  const preview = plainSnippet(snippet ?? t.last_message);
  const count = messageCount(t.message_count);
  const date = cardDate(t.updated_at);
  return (
    <Card interactive compact className="ws-chat-card" onClick={() => onOpen(t)}>
      <div className="ws-chat-row__head">
        <RunningDot running={!!t.running} ordinal={ordinal} />
        <span
          className="ws-chat-row__scope"
          title={scopeName(t.context_key)}
          aria-label={scopeLabel(t.context_key)}
        >
          <ChatScopeGlyph contextKey={t.context_key} />
        </span>
        <strong className="ws-chat-row__title" title={t.title || undefined}>{t.title || "New chat"}</strong>
        <span className="ws-chat-card__menu">
          <ChatMenu thread={t} onArchive={onArchive} onDelete={onDelete} />
        </span>
      </div>
      <p className="ws-muted ws-chat-card__preview">{preview}</p>
      <div className="ws-chat-card__foot ws-card__meta">
        <span>{date}</span>
        {count && <span className="ws-chat-row__sep" aria-hidden>·</span>}
        {count && <span>{count}</span>}
      </div>
    </Card>
  );
}
