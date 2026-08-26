import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { timeAgo, usePoll } from "@workshop/ui";
import { fetchNeedsYou, fitRows, moreLabel, needsRows, type InboxList } from "../../lib/dashboard";
import { DashboardCard, DashboardState } from "./DashboardCard";

// WHAT WANTS THE OPERATOR, across every scope. The one alarm on this page.
//
// SHOWS AND LINKS; IT DOES NOT ANSWER. The mockup's Activity page expands a row
// in place with Approve / Reject / Send, and this card deliberately does not:
// answering already has two homes (the chat's work panel and the ticket page),
// and a third would be the duplication `deciding-on-work-from-a-chat` was
// written to stop. Every row here opens the record that is asking.
//
// The rows come from `GET /api/inbox`, which is the same column a chat's panel
// builds -- same waiting predicate, same row builder, same order -- so this
// card and that panel cannot disagree about what is waiting.

const ROWS = 3;
const POLL_MS = 20_000;

export function NeedsYouCard() {
  const [data, setData] = useState<InboxList | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await fetchNeedsYou());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  usePoll(load, POLL_MS);

  const all = needsRows(data, 50);
  const { shown, hidden } = fitRows(all, ROWS);
  const waiting = data?.total ?? 0;

  return (
    <DashboardCard
      label="Needs you"
      // The count is the whole set, not the page: it is the alarm, and an alarm
      // that silently caps itself at three is one the operator learns to
      // distrust.
      count={waiting > 0 ? waiting : undefined}
      tone={waiting > 0 ? "alert" : "plain"}
      link={{ to: "/tickets", label: moreLabel(hidden, "inbox") }}
    >
      <DashboardState
        loading={!data && !err}
        error={err}
        empty={!!data && all.length === 0}
        emptyText="Nothing waiting on you."
        endpoint="/api/inbox"
        rows={ROWS}
      />
      {shown.length > 0 && (
        <ul className="ws-dashrows ws-dashrows--blocks">
          {shown.map((r) => (
            <li key={r.id}>
              <Link className="ws-dashneed" to={r.to}>
                <span className="ws-dashneed__top">
                  <span className="ws-dashneed__who">{r.who}</span>
                  {r.scope && <span className="ws-dashneed__scope">{r.scope}</span>}
                  {r.when && <span className="ws-dashneed__when">{timeAgo(r.when)}</span>}
                </span>
                <span className="ws-dashneed__ask">{r.ask}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </DashboardCard>
  );
}
