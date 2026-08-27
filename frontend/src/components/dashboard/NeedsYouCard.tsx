import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { timeAgo } from "@workshop/ui";
import { fetchNeedsYou, moreLabel, needsRows } from "../../lib/dashboard";
import { DashboardCard, DashboardState } from "./DashboardCard";
import { queryKeys } from "../../lib/queryKeys";

// WHAT WANTS THE OPERATOR, across every scope.
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

const POLL_MS = 20_000;
// The page cap this card asks for, and now the ONLY thing that limits what it
// shows: every row fetched is drawn and the card scrolls. Fifty is above any
// waiting count this instance has produced, so in practice the card holds the
// whole column and `moreLabel` stays quiet. Named because it is part of the
// query key: a different limit is a different read.
const LIMIT = 50;

export function NeedsYouCard() {
  const q = useQuery({
    queryKey: queryKeys.inbox.list(LIMIT),
    queryFn: () => fetchNeedsYou(LIMIT),
    refetchInterval: POLL_MS,
    // Owns its own cadence, so no staleness window over it: that would swallow
    // the catch-up read `usePoll` did on the way back from hidden.
    staleTime: 0,
    // `usePoll` does not retry. A failed tick is followed by the next tick.
    retry: false,
  });
  // A failed read keeps the last good payload on screen, as before, and the
  // error clears on the next SUCCESSFUL read rather than at the start of the
  // next attempt -- which is what `isError` already means.
  const data = q.data ?? null;
  const err = q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null;

  const shown = needsRows(data);
  // `total` is the WHOLE waiting set, counted by the server before its own page
  // cap, so the count is honest and the "→" names only what the READ left out.
  const waiting = data?.total ?? 0;
  const hidden = Math.max(0, waiting - shown.length);

  return (
    <DashboardCard
      label="Needs you"
      // The count is the whole set, not the page: a count that silently caps
      // itself is one the operator learns to distrust. It is also the ONLY
      // thing this card does differently from the other three. It used to wear
      // a warn border and a warn label as well, which put a permanent yellow
      // box on a dashboard whose steady state is "some things are waiting" --
      // an alarm that is always on is not an alarm. Removed 2026-08-26 at the
      // operator's call; the number says it.
      count={waiting > 0 ? waiting : undefined}
      link={{ to: "/tickets", label: moreLabel(hidden, "inbox") }}
    >
      <DashboardState
        loading={!data && !err}
        error={err}
        empty={!!data && shown.length === 0}
        emptyText="Nothing waiting on you."
        endpoint="/api/inbox"
      />
      {shown.length > 0 && (
        <ul className="ws-dashrows ws-dashrows--blocks">
          {shown.map((r) => (
            <li key={r.id}>
              <Link className="ws-dashneed" to={r.to}>
                <span className="ws-dashneed__top">
                  <span className="ws-dashneed__who">{r.who}</span>
                  <span className="ws-dashneed__wants">{r.wants}</span>
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
