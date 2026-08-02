import { X } from "lucide-react";
import { scopeLabel } from "../../lib/tasks";

// The one thing that gets a bar above the transcript: something was filed into
// the ticket queue and now lives somewhere else. That is what earns the position
// — it points OUT of this chat, to Work, and it has no place in the message log
// because it is not a thing that was said.
//
// NOTHING ELSE BELONGS HERE. This was briefly a general "chat notice" with three
// kinds, and both of the others were mistakes:
//
//  - It started as a "spawn message" carrying a scope and two booleans, and the
//    renderer INFERRED the sentence from which fields were empty — so "not run,
//    no run error" meant "filed as a ticket".
//  - Compaction then reused that shape to report progress and inherited the
//    ticket sentence, so /compact announced itself as "Filed as a ticket in
//    general". Progress on something that happens IN the conversation belongs in
//    the conversation: it is a StatusNote under its own message now, which also
//    fixed a message sent mid-compaction reading as though it came first.
//  - An `error` kind was declared and never constructed. Errors have their own
//    line under the log (`chat-err`).
//
// So: one kind, one meaning, one placement. If some new note wants this bar, the
// question to settle first is whether it points out of the chat. If it doesn't,
// it goes in the log.
export type FiledNotice = {
  text: string;
  scope: string;
  ran: boolean;
  runError?: string;
};

export function NoticeBar({ notice, onDismiss }: { notice: FiledNotice; onDismiss: () => void }) {
  return (
    <div className="chat-spawn-note" role="status">
      <span className="chat-spawn-note__body">
        {notice.ran ? (
          <>Filed and running in <strong>{scopeLabel(notice.scope)}</strong>. The live status is in the card below.{" "}</>
        ) : notice.runError ? (
          <>Filed in <strong>{scopeLabel(notice.scope)}</strong>, but could not run now: {notice.runError}.{" "}</>
        ) : (
          <>Filed as a ticket in <strong>{scopeLabel(notice.scope)}</strong>.{" "}</>
        )}
        <a href={`/work?scope=${encodeURIComponent(notice.scope)}`}>View in Work</a>.{" "}
        <span className="ws-muted">{notice.text.slice(0, 140)}{notice.text.length > 140 ? "…" : ""}</span>
      </span>
      <button className="chat-tb__btn" onClick={onDismiss} title="Dismiss" aria-label="Dismiss">
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}
