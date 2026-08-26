import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { timeAgo, usePoll } from "@workshop/ui";
import { ContextRing } from "../chat/ContextRing";
import {
  continueRows, fetchThreads, fitRows, moreLabel, type ThreadSummary,
} from "../../lib/dashboard";
import { DashboardCard, DashboardState } from "./DashboardCard";

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

const ROWS = 4;
const POLL_MS = 15_000;

export function ContinueCard() {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setThreads(await fetchThreads());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  usePoll(load, POLL_MS);

  const all = continueRows(threads, 50);
  const { shown, hidden } = fitRows(all, ROWS);

  return (
    <DashboardCard
      label="Pick up where you left off"
      link={{ to: "/chats", label: moreLabel(hidden, "chats") }}
    >
      <DashboardState
        loading={!threads && !err}
        error={err}
        empty={!!threads && all.length === 0}
        emptyText="No conversations yet."
        endpoint="/api/chat/threads"
        rows={ROWS}
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
