import { Link } from "react-router-dom";
import { timeAgo } from "@workshop/ui";
import { ContextRing } from "../chat/ContextRing";
import { continueRows } from "../../lib/dashboard";
import { DashboardCard, DashboardState } from "./DashboardCard";
import type { ThreadsRead } from "./useThreads";

// WHERE YOU WERE. The chat list's own order (when the operator last spoke), so
// this card and /chats cannot disagree about what is most recent.
//
// Each row carries two things the list route now answers without a per-chat
// follow-up: whether a turn is in flight, and how full the window is. The dots
// are the chat's own working motif and the ring is the composer's own ring --
// the same drawing, at 15px, so one chat cannot read 26% here and 31% there.
//
// A ring is drawn only when there IS a measurement. A thread with no completed
// turn has no server count, and a list row does not estimate: the estimate
// needs the unsent draft and only the composer has that. Absent, not zero.


export function ContinueCard({ read }: { read: ThreadsRead }) {
  const { threads, error: err } = read;
  // EVERY conversation the list route returned, and the card scrolls. That
  // route is not paged, so there is no remainder to name on the link: what is
  // here is all of them.
  const shown = continueRows(threads);

  return (
    <DashboardCard
      label="Pick up where you left off"
      count={shown.length > 0 ? shown.length : undefined}
      link={{ to: "/chats", label: "chats →" }}
    >
      <DashboardState
        loading={!threads && !err}
        error={err}
        empty={!!threads && shown.length === 0}
        emptyText="No conversations yet."
        endpoint="/api/chat/threads"
      />
      {shown.length > 0 && (
        <ul className="ws-dashrows">
          {shown.map((r) => (
            <li key={r.id}>
              <Link className="ws-dashline" to={r.to}>
                <span className="ws-dashline__title">{r.title}</span>
                {r.running && (
                  <span className="chat-working__dots" aria-label="running">
                    <i /><i /><i />
                  </span>
                )}
                <span className="ws-dashline__tail">{r.tail}</span>
                <span className="ws-dashline__end">
                  {r.used != null && r.win != null && (
                    <span className="chat-context-meter ws-dashline__ring">
                      <ContextRing used={r.used} win={r.win} size={15} />
                    </span>
                  )}
                  <span className="ws-dashline__when">{timeAgo(r.when, { suffix: false })}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </DashboardCard>
  );
}
