// The Task type + status mapping + the title/description convention, in ONE
// place. A Task is the canonical unit of work (a "ticket" in UI prose); the
// store, CLI, and API all say `task`, so every identifier here stays `task`.
//
// There is no separate title/description in the store: a task's `text` is
// multi-line-lossless, so the FIRST line is the title and the remaining lines
// are the description. `title`/`description`/`joinText` are the single source of
// truth for that split so no screen re-derives it. Backed by GET /api/tasks and
// /api/tasks/{scope}/{id}.
import type { StatusKind } from "../ui";

// All seven shipped statuses (backend services/tasks.py STATUSES). ProjectDetail
// previously knew only four of these and rendered a broken StatusDot for the
// rest; importing from here fixes that.
export type TaskStatus =
  | "todo"
  | "doing"
  | "in_review"
  | "blocked"
  | "parked"
  | "done"
  | "cancelled";

export const TASK_STATUSES: TaskStatus[] = [
  "todo",
  "doing",
  "in_review",
  "blocked",
  "parked",
  "done",
  "cancelled",
];

export type TaskEvidence = {
  kind?: string;
  ref?: string;
  ts?: string;
  [k: string]: unknown;
};

// Acceptance gate the worker must clear before a task can finish. Matches the
// store's shape (backend services/tasks.py): reviewer = a reviewer pass judges
// it; command = `spec` is a shell command that must exit 0; operator = the human
// approves. `spec` only carries meaning for the command kind.
export type TaskAcceptanceKind = "reviewer" | "command" | "operator";

export type TaskAcceptance = {
  kind: TaskAcceptanceKind;
  spec?: string | null;
};

// UI labels for the acceptance kinds (kept beside the type so no screen re-derives).
const TASK_ACCEPTANCE_LABEL: Record<TaskAcceptanceKind, string> = {
  reviewer: "reviewer pass",
  command: "command exits 0",
  operator: "operator approves",
};

export const taskAcceptanceLabel = (kind?: string): string =>
  TASK_ACCEPTANCE_LABEL[(kind ?? "") as TaskAcceptanceKind] ?? String(kind ?? "");

// ---- latitude ---------------------------------------------------------------
// What a worker may do WHERE THE TICKET DOES NOT SAY (backend services/latitude.py).
// Not effort (that is depth) and not blast radius (that is `touches` + grants,
// which are enforced and which latitude never widens).
export type Latitude = "strict" | "standard" | "open";

export const LATITUDES: Latitude[] = ["strict", "standard", "open"];
export const DEFAULT_LATITUDE: Latitude = "standard";

const LATITUDE_DESC: Record<Latitude, string> = {
  strict: "Where the ticket does not say, ask. Blocks often.",
  standard: "Decides reversible things and records them; blocks on irreversible ones.",
  open: "Pursues the goal, decides freely within the enforced floor, reports at the end.",
};

export const latitudeDesc = (v?: string): string =>
  LATITUDE_DESC[(v ?? "") as Latitude] ?? "";

// Where an effective latitude came from. This travels WITH the value everywhere
// it is shown: null means inherit, so a value rendered without its source is how
// an inherited setting gets mistaken for a deliberate one.
export type LatitudeSource = "invocation" | "task" | "scope" | "default";

export type EffectiveLatitude = {
  value: Latitude;
  source: LatitudeSource;
};

const LATITUDE_SOURCE_LABEL: Record<LatitudeSource, string> = {
  invocation: "set for this run",
  task: "set on this ticket",
  scope: "inherited from the scope",
  default: "the default",
};

export const latitudeSourceLabel = (source?: string): string =>
  LATITUDE_SOURCE_LABEL[(source ?? "") as LatitudeSource] ?? String(source ?? "");

export type Task = {
  id: string;
  text: string;
  status: TaskStatus;
  owner?: string;
  assignee?: string | null;
  parent?: string | null;
  priority?: number;
  effort?: string | null;
  // The ticket's OWN override. null/absent = inherit from the scope; it is NOT
  // "standard". Read `effective_latitude` to know what a run would actually use.
  latitude?: Latitude | null;
  // Resolved by the API (invocation > task > scope > default), with its source.
  effective_latitude?: EffectiveLatitude;
  depends_on?: string[];
  acceptance?: TaskAcceptance | null;
  max_attempts?: number | null;
  blocked_reason?: string | null;
  job_ids?: string[];
  evidence?: TaskEvidence[];
  source?: { kind?: string; ref?: string | null };
  awaiting?: { question?: string; since?: string } | null;
  created?: string;
  updated?: string;
  // Attached by the cross-scope API (routes/tasks_all): the resolved scope key
  // (e.g. "workshop", "project:birds", "agent:foo", "build:bar").
  scope?: string;
};

// The seven task statuses -> the five design status kinds. Meaning, never a new
// color: todo/parked idle-ish, doing live, in_review needs the operator, blocked
// is stuck, done succeeded, cancelled dropped.
const TASK_STATUS_KIND: Record<TaskStatus, StatusKind> = {
  todo: "stopped",
  doing: "running",
  in_review: "warn",
  blocked: "danger",
  parked: "warn",
  done: "ok",
  cancelled: "stopped",
};

export const taskStatusKind = (status?: string): StatusKind =>
  TASK_STATUS_KIND[(status ?? "") as TaskStatus] ?? "stopped";

// Human labels for the status control / pill. Underscores read poorly in prose.
const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  todo: "todo",
  doing: "doing",
  in_review: "in review",
  blocked: "blocked",
  parked: "parked",
  done: "done",
  cancelled: "cancelled",
};

export const taskStatusLabel = (status?: string): string =>
  TASK_STATUS_LABEL[(status ?? "") as TaskStatus] ?? String(status ?? "");

// ---- title / description convention ----------------------------------------
// First line = title, everything after = description. Saving joins them back
// with a single newline through the existing tasks.update(text=...).

export function title(task: { text?: string } | string | null | undefined): string {
  const text = typeof task === "string" ? task : task?.text ?? "";
  return text.split("\n")[0] ?? "";
}

export function description(task: { text?: string } | string | null | undefined): string {
  const text = typeof task === "string" ? task : task?.text ?? "";
  const nl = text.indexOf("\n");
  return nl === -1 ? "" : text.slice(nl + 1);
}

// Join an edited title + description back into a single `text` for saving. A
// title never carries newlines (collapse any pasted-in ones); an empty
// description yields just the title.
export function joinText(titleLine: string, desc: string): string {
  const t = (titleLine ?? "").replace(/\r?\n/g, " ").trim();
  const d = (desc ?? "").replace(/\r\n/g, "\n").replace(/\s+$/g, "");
  return d ? `${t}\n${d}` : t;
}

// A friendly label for a scope key, for filters and the scope link.
export function scopeLabel(scope?: string): string {
  if (!scope) return "unknown";
  if (scope === "workshop") return "Workshop root";
  const [kind, slug] = scope.split(":", 2);
  if (!slug) return scope;
  const kindLabel = kind === "project" ? "Project" : kind === "agent" ? "Agent" : kind === "build" ? "Build" : kind;
  return `${kindLabel}: ${slug}`;
}

// The in-app route a scope links to (project detail / agent detail), or null
// when the scope has no dedicated page (workshop root, build agents).
export function scopeHref(scope?: string): string | null {
  if (!scope) return null;
  const [kind, slug] = scope.split(":", 2);
  if (kind === "project" && slug) return `/projects/${encodeURIComponent(slug)}`;
  if (kind === "agent" && slug) return `/agents/${encodeURIComponent(slug)}`;
  return null;
}
