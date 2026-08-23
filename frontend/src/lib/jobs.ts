// The Job type + status mapping, in ONE place. A Job is the single record for
// every bounded run (cron/loop sweep, per-project or per-agent Ralph sweep, an
// event reaction, a manual "run now"). It was independently re-declared in
// Jobs.tsx, JobDetail.tsx, Home.tsx, and AgentDetail.tsx; those drift. Import
// from here instead. Backed by GET /api/jobs and /api/jobs/{id}.
import type { StatusKind } from "@workshop/ui";
import type { components } from "../api/schema.gen";

// What a run produced, straight off the backend's own model. Hand-writing this
// is what let the raw-dump era persist: `evidence` was `unknown` here and four
// separate readers each guessed at it. See routes/api_models.JobEvidence.
export type JobEvidence = components["schemas"]["JobEvidence"];
export type JobAwaitingQuestion = components["schemas"]["JobAwaitingQuestion"];
export type JobRefusal = components["schemas"]["JobRefusal"];

export type Job = {
  id: string;
  agent_id?: string;
  project_slug?: string;
  chat_id?: string;
  trigger?: string;
  status?: string;
  started_at?: string;
  ended_at?: string;
  exit_code?: number;
  tasks_done?: number;
  tasks_failed?: number;
  // What the run cost, from the usage ledger rather than from the worker's own
  // receipt. `tokens` includes the child runs, because a coordinator sweep
  // spends almost nothing itself; `tokens_own` is this record alone. Both are
  // null when nothing was billed against the job id.
  tokens?: number | null;
  tokens_own?: number | null;
  parent_id?: string;
  task_id?: string;
  evidence?: JobEvidence | JobAwaitingQuestion[] | null;
  budget?: Record<string, unknown>;
  // Display fields computed by the API (routes/jobs._enrich); absent on raw
  // records but always present from GET /api/jobs and /api/jobs/{id}.
  title?: string;
  agent_name?: string;
  trigger_label?: string;
  error?: string;
  summary?: string;
  // Joined from the task store at read time (routes/jobs._enrich): what the job
  // was actually for, and which chat spawned it.
  task_text?: string;
  task_status?: string;
  chat_title?: string;
  chat_context_key?: string;
  context_label?: string;
};

// Job status -> one of the five design status kinds.
const JOB_STATUS_KIND: Record<string, StatusKind> = {
  running: "running",
  done: "ok",
  queued: "stopped",
  cancelled: "stopped",
  awaiting_input: "warn",
  failed: "danger",
};

export const jobStatusKind = (status?: string): StatusKind =>
  JOB_STATUS_KIND[status ?? ""] ?? "stopped";

// A REFUSED RUN IS ITS OWN OUTCOME, and this is the one place that says so.
//
// The store has five terminal spellings and none of them is "refused": a run an
// admission guard turned away closes `cancelled`, which is the same word an
// operator's own cancellation would leave. On 2026-08-23 that cost the operator
// ten minutes: he pressed "File and run" twice, the card read `cancelled 0s`
// both times, and the actual reason (the development scope's 5h token ceiling)
// only ever existed in `.runtime/queue-runner.log` on the host.
//
// A SIXTH STATUS WAS NOT ADDED. `refused` would have to be threaded through
// jobs.STATUSES, TERMINAL_STATUSES, the reconcile paths, the feed's filters and
// this map, and the append-only job log already holds thousands of `cancelled`
// halts that would keep the old value regardless -- so the store would end up
// holding both spellings of one fact anyway. The record instead carries
// `evidence.refusal`, which the writer had to attach in any case to say WHAT
// refused it, and "refused" is derived from that here. No parallel field: the
// sentence lives in `refusal.reason` exactly once, composed by
// services/usage_ledger, and nothing on this side re-words it.
//
// WARN, NOT DANGER. Nothing is broken when a budget guard refuses a run. It is
// the guard working, and the design system's `danger` is for things that are
// wrong.
export const REFUSED_LABEL = "refused";
export const REFUSED_KIND: StatusKind = "warn";

/** The refusal a guard attached to a run, or null for every other outcome.
 *
 *  `evidence` is a union: the structured record, an array of awaiting-input
 *  questions, or absent. Only the first shape can carry one. */
export function jobRefusal(evidence?: Job["evidence"]): JobRefusal | null {
  if (!evidence || Array.isArray(evidence)) return null;
  const r = (evidence as JobEvidence).refusal;
  return r ?? null;
}

/** A run's state word and status kind together, because they must agree. Every
 *  surface that prints a run's outcome calls this rather than pairing
 *  `job.status` with `jobStatusKind` and forgetting the refusal case. */
export function runOutcome(
  status?: string,
  refusal?: JobRefusal | null,
): { label: string; kind: StatusKind } {
  if (refusal) return { label: REFUSED_LABEL, kind: REFUSED_KIND };
  return { label: (status || "unknown").replace(/_/g, " "), kind: jobStatusKind(status) };
}

/** When the rolling window next lets a run in, in the reader's own clock.
 *
 *  The SENTENCE is composed on the server (one wording, one set of numbers);
 *  this is the one thing it cannot carry, because an instant has to be rendered
 *  in the timezone of whoever is looking. Null once the instant has passed:
 *  "admissible again at a time in the past" is noise, and it is an estimate that
 *  assumed nothing else would spend in the meantime. */
export function nextAdmissibleText(
  iso?: string | null,
  nowMs: number = Date.now(),
): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  if (!Number.isFinite(at) || at <= nowMs) return null;
  const clock = new Date(at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `Admissible again about ${clock} if nothing else spends.`;
}

// The statuses a run can still leave on its own. Everything else is terminal:
// `awaiting_input` included, because that run's process is gone and only an
// operator action moves it.
//
// This is the predicate a surface polls on. A run detail page that never polled
// read "running" and "Ended: running" forever, no matter what the run had since
// done; the fix is only correct if "when do I stop" is answered in one place.
const JOB_LIVE_STATES = new Set(["running", "queued"]);

export const jobIsLive = (status?: string): boolean => JOB_LIVE_STATES.has(status ?? "");

// The route to a run. `/jobs/:id` still redirects here, so both spellings work
// and that is exactly the problem: the path was hand-written in nine places in
// two spellings, and a reader could not tell whether the difference meant
// anything. It does not. One helper, the way `ticketHref` is the only way to
// build a ticket URL.
export const runHref = (id: string): string => `/runs/${encodeURIComponent(id)}`;
