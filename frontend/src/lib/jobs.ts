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
