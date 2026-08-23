import { useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button, Notice, Skeleton, StatusDot } from "@workshop/ui";
import HolderControls from "../../holders/HolderControls";
import { useDirectory } from "../../holders/useHolders";
import {
  activityLine, holderName, holderStatus, subjectLine, type DirectoryEntry,
} from "../../../lib/holders";

// WHO IS HERE, in the sheet that is already here.
//
// The leases design (section 14) puts the directory sheet in "the Tickets
// panel's existing single sheet host", and this is that: a third view of
// `WorkPanel`, alongside the ticket list and the settings, reached from the
// panel's own head. There is no second sheet, no second scrim and no second
// Escape handler, which is the whole reason the host was collapsed to one.
//
// EVERY ROW IS A READ-TIME JOIN AND IS STORED NOWHERE (section 6). Presence is
// a TTL'd row any process can write, the directory joins it to leases, tickets
// and worktrees when it is asked, and nothing here caches it. So polling IS the
// freshness story, and a row that stops beating simply stops appearing.
//
// LEASE STATE IS `held` OR `expired`, AND THAT IS ALL. `idle_since` moves the
// row's dot to `warn` and is spelled out in the detail; it is deliberately not
// a third word, because a word a client keys on is a state the rest of the
// system then has to handle.

// A live directory is a handful of rows. Past this the question stops being
// "who is here" and becomes "why are there so many", which the count answers.
const MAX_ROWS = 12;

export default function DirectoryView({
  selfSessionId, onBack,
}: {
  /** The session this chat IS, when it has one. Its own row reads "Yours" and
   *  offers no verbs against itself. */
  selfSessionId?: string | null;
  onBack: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const { entries, err, reload } = useDirectory();

  const rows = entries ?? [];
  const shown = rows.slice(0, MAX_ROWS);
  const focused = selected ? rows.find((e) => e.session_id === selected) : undefined;

  if (focused) {
    return (
      <>
        <div className="chat-workpanel__head">
          <button type="button" className="chat-workpanel__back" onClick={() => setSelected(null)}>
            <ChevronLeft size={14} strokeWidth={1.8} aria-hidden />
            Who is here
          </button>
          <span className="chat-workpanel__heading">{holderName(focused)}</span>
        </div>
        <div className="chat-workpanel__scroll">
          <HolderControls
            entry={focused}
            selfSessionId={selfSessionId}
            worktreeId={focused.subject_kind === "worktree" ? focused.subject_id : null}
            onChanged={reload}
          />
        </div>
      </>
    );
  }

  return (
    <>
      <div className="chat-workpanel__head">
        <button type="button" className="chat-workpanel__back" onClick={onBack}>
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden />
          Tickets
        </button>
        <span className="chat-workpanel__heading">Who is here</span>
        {entries && <span className="chat-workpanel__count">{rows.length}</span>}
      </div>

      <div className="chat-workpanel__scroll">
        {/* A LINE, NOT A REGION, and only while rows are still on screen: a
            failed poll keeps the last good payload, so the failure is beside
            the list rather than over it. */}
        {err && entries && (
          <Notice tone="warn" detail={err} action={<Button size="sm" onClick={reload}>Retry</Button>}>
            This stopped refreshing, so who is here may be out of date.
          </Notice>
        )}
        {entries === null && err ? (
          <Notice tone="error" detail={err} action={<Button size="sm" onClick={reload}>Retry</Button>}>
            Couldn't read who is here.
          </Notice>
        ) : entries === null ? (
          <div className="chat-workpanel__loading">
            <Skeleton /><Skeleton /><Skeleton />
          </div>
        ) : shown.length === 0 ? (
          // Prose, not a StateBlock: this is a 360px sheet, and a bordered
          // region around one sentence is the shape this app spent a pass
          // removing.
          <p className="chat-workpanel__empty">
            Nobody is working right now. A session appears here while a chat or a
            sweep is holding a ticket or a tree.
          </p>
        ) : (
          <ul className="chat-workpanel__list">
            {shown.map((e) => (
              <li key={e.session_id} className="chat-workpanel__item">
                <SessionRow entry={e} selfSessionId={selfSessionId} onSelect={setSelected} />
              </li>
            ))}
          </ul>
        )}
        {rows.length > MAX_ROWS && (
          <p className="chat-workpanel__empty">
            Showing {MAX_ROWS} of {rows.length} live sessions.
          </p>
        )}
      </div>
    </>
  );
}

/** One live session. The dot carries the lease, the state word sits beside it
 *  as plain text, and the second line says what it is on. Same two-line row the
 *  panel's other lists use. */
function SessionRow({
  entry, selfSessionId, onSelect,
}: {
  entry: DirectoryEntry;
  selfSessionId?: string | null;
  onSelect: (id: string) => void;
}) {
  const status = holderStatus(entry, selfSessionId);
  const subject = subjectLine(entry);
  return (
    <button
      type="button"
      className="chat-workpanel__row chat-workpanel__row--stack"
      onClick={() => onSelect(entry.session_id)}
      title={`${holderName(entry)}. ${activityLine(entry)}`}
    >
      <span className="chat-workpanel__cardline">
        <StatusDot status={status.kind}>{status.label}</StatusDot>
        <span className="ws-truncate chat-workpanel__title">{holderName(entry)}</span>
        <ChevronRight size={14} strokeWidth={1.8} aria-hidden className="chat-workpanel__caret" />
      </span>
      <span className="ws-truncate chat-workpanel__cardtext">
        {activityLine(entry)}
        {subject ? ` · ${subject}` : ""}
      </span>
    </button>
  );
}
