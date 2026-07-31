import { X } from "lucide-react";
import { scopeLabel } from "../../lib/tasks";

// A one-line note about something the operator just did, shown under the toolbar.
//
// DISCRIMINATED ON PURPOSE. This started as a single "spawn message" shape that
// carried a scope and two booleans, and the renderer INFERRED the sentence from
// which fields were empty — so "not run, no run error" meant "filed as a ticket".
// Compaction then reused the same shape to report progress and inherited the
// ticket sentence, so /compact announced itself as "Filed as a ticket in
// general". A note now says what kind of thing it is instead of leaving the
// renderer to guess from absent fields.
//
// The type and its renderer live in ONE file so a new kind cannot be added
// without deciding how it reads. That separation is what let the drift happen.
export type ChatNotice =
  // Something was filed into the ticket queue. The only kind that links to Work.
  | { kind: "ticket"; text: string; scope: string; ran: boolean; runError?: string }
  // Context reclamation: in flight (`busy`) or its result. Never a ticket.
  | { kind: "compaction"; text: string; busy?: boolean }
  | { kind: "error"; text: string };

export function NoticeBar({ notice, onDismiss }: { notice: ChatNotice; onDismiss: () => void }) {
  return (
    <div className={`chat-spawn-note${notice.kind === "error" ? " is-error" : ""}`} role="status">
      <span className="chat-spawn-note__body">
        {notice.kind === "ticket" ? (
          <>
            {notice.ran ? (
              <>Filed and running in <strong>{scopeLabel(notice.scope)}</strong>. The live status is in the card below.{" "}</>
            ) : notice.runError ? (
              <>Filed in <strong>{scopeLabel(notice.scope)}</strong>, but could not run now: {notice.runError}.{" "}</>
            ) : (
              <>Filed as a ticket in <strong>{scopeLabel(notice.scope)}</strong>.{" "}</>
            )}
            <a href={`/work?scope=${encodeURIComponent(notice.scope)}`}>View in Work</a>.{" "}
            <span className="ws-muted">{notice.text.slice(0, 140)}{notice.text.length > 140 ? "…" : ""}</span>
          </>
        ) : (
          /* Compaction and errors are not tickets: no scope, no Work link. A busy
             note carries the same dots as the working indicator so "something is
             happening" reads the same way everywhere. */
          <>
            {notice.text}
            {notice.kind === "compaction" && notice.busy && (
              <span className="chat-working__dots" aria-hidden><i /><i /><i /></span>
            )}
          </>
        )}
      </span>
      <button className="chat-tb__btn" onClick={onDismiss} title="Dismiss" aria-label="Dismiss">
        <X size={14} aria-hidden />
      </button>
    </div>
  );
}
