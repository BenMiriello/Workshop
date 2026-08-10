// The Job type + status mapping, in ONE place. A Job is the single record for
// every bounded run (cron/loop sweep, per-project or per-agent Ralph sweep, an
// event reaction, a manual "run now"). It was independently re-declared in
// Jobs.tsx, JobDetail.tsx, Home.tsx, and AgentDetail.tsx; those drift. Import
// from here instead. Backed by GET /api/jobs and /api/jobs/{id}.
import type { StatusKind } from "../ui";

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
  parent_id?: string;
  task_id?: string;
  evidence?: unknown;
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

// The statuses a run can still leave on its own. Everything else is terminal:
// `awaiting_input` included, because that run's process is gone and only an
// operator action moves it.
//
// This is the predicate a surface polls on. A run detail page that never polled
// read "running" and "Ended: running" forever, no matter what the run had since
// done; the fix is only correct if "when do I stop" is answered in one place.
const JOB_LIVE_STATES = new Set(["running", "queued"]);

export const jobIsLive = (status?: string): boolean => JOB_LIVE_STATES.has(status ?? "");
