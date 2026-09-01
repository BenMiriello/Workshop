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

// THE SERVER'S OWN STATEMENT OF THIS RECORD, not a second copy of it.
//
// This was 25 fields written out by hand, and it was wrong in a way nothing
// could see: every one of them said `?: string`, while the backend serialises
// `string | null`. So `job.title` was `string | undefined` to the compiler and
// `null` on the wire, and every `if (job.title)` guard in the app was checking
// for a value the server never sends. Same defect the four Location fields had
// (frontend/CLAUDE.md), one layer up. It also missed `kind`, `pid` and
// `binding` entirely, which the route has returned all along.
//
// `schema.gen.ts` is generated from routes/api_models.Job and gated by
// scripts/check_generated_client.py, so a field moving there is a red typecheck
// here rather than a run row that silently renders nothing.
//
// `budget` arrives as `Record<string, never>` because the backend still types it
// `dict[str, Any]` (api_models.py: "`limits` and `budget` remain open"). That is
// an opaque field, counted by scripts/check_opaque_fields.py, and it is fixed in
// the model rather than by widening it back here.
export type Job = components["schemas"]["Job"];

// Job status -> one of the five design status kinds.
const JOB_STATUS_KIND: Record<string, StatusKind> = {
  running: "running",
  done: "ok",
  queued: "stopped",
  cancelled: "stopped",
  awaiting_input: "warn",
  failed: "danger",
};

export const jobStatusKind = (status?: string | null): StatusKind =>
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
  status?: string | null,
  refusal?: JobRefusal | null,
): { label: string; kind: StatusKind } {
  if (refusal) return { label: REFUSED_LABEL, kind: REFUSED_KIND };
  return { label: (status || "unknown").replace(/_/g, " "), kind: jobStatusKind(status) };
}

/** What a sweep worked ("3 done", "3 done, 1 failed"), or null when it worked
 *  nothing.
 *
 *  ZERO IS NOT PRINTED, and that is frontend/CLAUDE.md's "a constant is not a
 *  signal" rather than a shortcut. Measured 2026-09-01: 66 of the 100 most
 *  recent runs carry `0 done, 0 failed`, so printing it would add the same four
 *  characters to two thirds of every runs list and tell the reader nothing. A
 *  sweep that actually moved tickets is the exception, so it is what gets said.
 *  A run's own PAGE still states the zero, because there one record is the
 *  subject and "it did nothing" is the answer to the question being asked. */
export function sweepCounts(job: Pick<Job, "tasks_done" | "tasks_failed">): string | null {
  const done = typeof job.tasks_done === "number" ? job.tasks_done : 0;
  const failed = typeof job.tasks_failed === "number" ? job.tasks_failed : 0;
  const bits: string[] = [];
  if (done) bits.push(`${done} done`);
  if (failed) bits.push(`${failed} failed`);
  return bits.length ? bits.join(", ") : null;
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

export const jobIsLive = (status?: string | null): boolean => JOB_LIVE_STATES.has(status ?? "");

// The route to a run. `/jobs/:id` still redirects here, so both spellings work
// and that is exactly the problem: the path was hand-written in nine places in
// two spellings, and a reader could not tell whether the difference meant
// anything. It does not. One helper, the way `ticketHref` is the only way to
// build a ticket URL.
export const runHref = (id: string): string => `/runs/${encodeURIComponent(id)}`;
