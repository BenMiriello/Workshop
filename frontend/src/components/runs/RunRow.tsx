// ONE ROW SHAPE FOR A RUN, wherever a run is listed.
//
// There were two, and only one of them read the payload. The global feed
// (components/JobsFeed.tsx) rendered the trigger LABEL, the binding, a duration
// and the cross-links; the per-app Runs tab rendered a card with the raw
// trigger enum, an absolute timestamp and nothing else, off the same enriched
// record. Measured 2026-09-01 on the app with 50 runs: every row read "done
// manual Sweep <app>", one distinct string, while `trigger_label`, `ended_at`,
// `tasks_done` and `tasks_failed` sat unread in the same object.
//
// So the row is a component, not a shape each surface re-draws. A field added
// here appears everywhere a run is listed, which is the only way the two stay
// the same row.
import { Link } from "react-router-dom";
import { formatDuration, timeAgo, StatusDot, agencyFor } from "@workshop/ui";

import { type Job, runOutcome, jobRefusal, runHref, sweepCounts } from "../../lib/jobs";
import { runTicketScope, ticketHref } from "../../lib/tasks";

// The row's own link takes the row's ink and no underline, the way every other
// whole-row link in the kit does (`.ws-listrow`, `.ws-runrow`, `.ws-ticketrow`):
// the row shape is the affordance. Only the secondary links in the meta line
// read as links.
const ROW_LINK = { color: "inherit", textDecoration: "none" } as const;

// Relative time for the meta line, matching the ticket list: no suffix, the
// row's position already reads as relative. lib/time is the one formatter.
const ago = (iso?: string | null): string => timeAgo(iso, { suffix: false });

/** The human name for what this run is bound to, or null when the caller is
 *  already inside that binding. */
export function bindingText(job: Job): string | null {
  return job.project_slug || job.agent_name || null;
}

// The two links a row already has the data for and used to throw away: the
// ticket this run is working (`task_id`) and the chat that launched it
// (`chat_context_key` + `chat_id`).
//
// They are secondary: the row's own target is the run, so they render in the
// meta line as siblings of the row's title link. Nesting them inside it would
// be an <a> inside an <a>, which the browser silently unpicks. `ws-muted` is
// what keeps them the meta line's own ink: an <a> with no class takes the
// browser's link colour, and a link is underlined, never tinted.
export function crossLinks(job: Job): JSX.Element[] {
  const out: JSX.Element[] = [];
  if (job.task_id) {
    out.push(
      <Link key="ticket" className="ws-muted" to={ticketHref(runTicketScope(job), job.task_id)}>
        Ticket
      </Link>,
    );
  }
  if (job.chat_context_key && job.chat_id) {
    out.push(
      <Link
        key="chat"
        className="ws-muted"
        to={`/chat/${encodeURIComponent(job.chat_context_key)}?thread=${encodeURIComponent(job.chat_id)}`}
      >
        Chat
      </Link>,
    );
  }
  return out;
}

export function RunRow({
  job,
  hideBinding = false,
}: {
  job: Job;
  /** Drop the project/agent name from the meta line. Set on a surface that IS
   *  that binding (an app's own Runs tab), where the name is the same on every
   *  row and so labels nothing. */
  hideBinding?: boolean;
}) {
  const outcome = runOutcome(job.status, jobRefusal(job.evidence));
  const dur = formatDuration(job.started_at, job.ended_at);
  const counts = sweepCounts(job);
  const binding = hideBinding ? null : bindingText(job);
  return (
    <div className="ws-listrow" data-agency={agencyFor(job.trigger) ?? undefined}>
      <span className="ws-listrow__mark" title={outcome.label} aria-label={outcome.label}>
        <StatusDot status={outcome.kind}>{null}</StatusDot>
      </span>
      <Link className="ws-listrow__title" style={ROW_LINK} to={runHref(job.id)}>
        {job.title || job.task_text || job.context_label || bindingText(job) || job.id}
      </Link>
      <span className="ws-listrow__meta">
        {/* The state WORD, not just the dot. Same rule the ticket list follows:
            a colour answers "how did it end" only for someone who has already
            learned the five hues. */}
        <span>{outcome.label}</span>
        {(job.trigger_label || job.trigger) && <span>{job.trigger_label || job.trigger}</span>}
        {binding && <span className="ws-listrow__scope">{binding}</span>}
        {dur && <span>{dur}</span>}
        {counts && <span>{counts}</span>}
        {crossLinks(job)}
        <span className="ws-listrow__time">{ago(job.started_at)}</span>
      </span>
    </div>
  );
}
