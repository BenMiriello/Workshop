// The Task type + status mapping + the title/description accessors, in ONE
// place. A Task is the canonical unit of work (a "ticket" in UI prose); the
// store, CLI, and API all say `task`, so every identifier here stays `task`.
//
// `title` and `body` are STORED fields on the record (backend services/tasks.py,
// ticket-model-and-external-trackers-2026-08-07). `text` is still returned as a
// projection of the two, so the accessors below prefer the real fields and fall
// back to splitting `text` for a payload cached before the change. Backed by GET
// /api/tasks and /api/tasks/{scope}/{id}.
import type { StatusKind } from "../ui";
import { DEVELOPMENT_SCOPE } from "./devMode";

// All eight shipped statuses (backend services/tasks.py STATUSES). ProjectDetail
// previously knew only four of these and rendered a broken StatusDot for the
// rest; importing from here fixes that.
export type TaskStatus =
  | "draft"
  | "todo"
  | "doing"
  | "in_review"
  | "blocked"
  | "parked"
  | "done"
  | "cancelled";

export const TASK_STATUSES: TaskStatus[] = [
  "draft",
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
// What a worker is TOLD to do where the ticket does not say (backend
// services/latitude.py). Not effort (that is depth).
//
// This is prompt text. The resolved value is written into the sweep's
// instructions and the model is asked to behave accordingly; the one thing
// downstream that reads it is the rule that a `strict` run whose completion gate
// came back ambiguous blocks for the operator instead of retrying. Nothing
// refuses a write because of a latitude value.
//
// This comment used to call `touches` and grants an enforced blast radius that
// latitude could never widen. They are not that. `touches` is checked after the
// run against the committed diff, so it flags a stray and blocks the ticket
// rather than stopping the write. Both are worth having; neither is a wall, and
// the copy below must not imply one.
export type Latitude = "strict" | "standard" | "open";

export const LATITUDES: Latitude[] = ["strict", "standard", "open"];
export const DEFAULT_LATITUDE: Latitude = "standard";

const LATITUDE_DESC: Record<Latitude, string> = {
  strict: "Where the ticket does not say, ask. Blocks often.",
  standard: "Decides reversible things and records them; blocks on irreversible ones.",
  open: "Pursues the goal, decides freely, reports the decisions at the end.",
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

// What a stopped task is asking for. One shape for every reason work stops — a
// permission, a missing resource, an ambiguous ticket — so one surface answers
// all of them. `kind` is an open slug, not a union: the named kinds are the ones
// with a producer today, not the boundary of what can stop a job.
export type BlockRequest = {
  question?: string;
  since?: string;
  kind?: string;
  ask?: string;
  why?: string | null;
  unblocks_if?: string | null;
  options?: string[] | null;
  default?: string | null;
  // Present => the ask names a resource and a verb, so a grant can be prefilled
  // from it. Absent => the answer is a judgment, so the review offers a rule.
  proposal?: { uri: string; verb: string } | null;
  raised_by?: string | null;
  // Groups a mass-block: every task carrying the same key is one answer.
  dedupe_key?: string | null;
  context?: Record<string, unknown>;
  dismissed?: boolean;
};

export type Task = {
  id: string;
  // Canonical pair. `text` is the server's projection of the two and is kept for
  // readers that still show one blob.
  title: string;
  body: string;
  text: string;
  labels?: string[];
  // Who wrote the scope (operator | agent | import). When a run fails on a bad
  // spec, this is the question being asked.
  scoped_by?: string;
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
  // Soft link (entity-model 2026-07-18 §4.2). No scheduling effect; visual
  // grouping only. blocks is DERIVED from the inverse of depends_on and
  // computed client-side from the current ticket list.
  relates_to?: string[];
  acceptance?: TaskAcceptance | null;
  max_attempts?: number | null;
  blocked_reason?: string | null;
  job_ids?: string[];
  // Where this ticket came from, resolved server-side into something linkable.
  // Provenance was always recorded and never shown, which made a ticket opened
  // from the inbox a dead end: the conversation that produced it existed and
  // was unreachable. `context_key` is null when the chat has since been deleted.
  origin?: {
    kind: "chat";
    id: string;
    title: string | null;
    context_key: string | null;
  } | null;
  evidence?: TaskEvidence[];
  source?: { kind?: string; ref?: string | null };
  awaiting?: BlockRequest | null;
  // The SAME record as `awaiting`, under the name the Review surface uses for it
  // (the API projects it; the store keeps one record). Present whenever the task
  // carries a block request.
  blocked_request?: BlockRequest | null;
  created?: string;
  updated?: string;
  // Attached by the cross-scope API (routes/tasks_all): the resolved scope key
  // (e.g. "workshop", "project:birds", "agent:foo", "build:bar").
  scope?: string;
  // Attached by the ticket detail endpoint only (it needs the scope's id set to
  // resolve dependencies), so it is absent on list rows.
  readiness?: TicketReadiness;
};

// The seven task statuses -> the five design status kinds. Meaning, never a new
// color: todo/parked idle-ish, doing live, in_review needs the operator, blocked
// is stuck, done succeeded, cancelled dropped.
const TASK_STATUS_KIND: Record<TaskStatus, StatusKind> = {
  draft: "stopped",
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
  draft: "draft",
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

// ---- title / description accessors ------------------------------------------
// The record carries `title` and `body`. These accessors exist so no screen
// reaches for one or the other directly, and so a bare string (a draft line, a
// proposal) can be read with the same call. The `text` fallback covers a payload
// produced before the fields existed; it splits on the first line the way the
// store's own `split_text` does.

type TaskTextish = { title?: string; body?: string; text?: string };

export function title(task: TaskTextish | string | null | undefined): string {
  if (typeof task === "string") return task.split("\n")[0] ?? "";
  if (task?.title !== undefined) return task.title;
  return (task?.text ?? "").split("\n")[0] ?? "";
}

export function description(task: TaskTextish | string | null | undefined): string {
  if (typeof task === "string") {
    const nl = task.indexOf("\n");
    return nl === -1 ? "" : task.slice(nl + 1);
  }
  if (task?.title !== undefined) return task.body ?? "";
  const text = task?.text ?? "";
  const nl = text.indexOf("\n");
  return nl === -1 ? "" : text.slice(nl + 1);
}

// A title never carries newlines: a pasted-in multi-line string collapses to one
// line before it is sent. Every writer of a title goes through this, so the rule
// lives here and not in each form.
export function cleanTitle(titleLine: string): string {
  return (titleLine ?? "").replace(/\r?\n/g, " ").trim();
}

// What a ticket does not yet say (backend services/ticket_readiness). Advisory:
// only a BROKEN ticket (no title, an unresolvable dependency) is refused a run,
// because 0 of 40 live tickets passed every check when this shipped and a full
// gate would have stopped the queue rather than improved it. Attached by
// GET /api/tasks/{scope}/{id}.
export type TicketReadiness = {
  ok: boolean;
  missing: { check: string; why: string }[];
  warnings: string[];
};

// The checks that refuse a run, mirroring ticket_readiness.BLOCKING. Everything
// else is advice, and the UI has to show the difference or the checklist reads
// as a wall of equally-urgent failures.
const BLOCKING_CHECKS = new Set(["title", "dependencies"]);

export const isBlockingCheck = (check: string): boolean => BLOCKING_CHECKS.has(check);

// Human labels for each check, so the checklist reads as prose and not as keys.
const READINESS_LABEL: Record<string, string> = {
  title: "Title",
  objective: "Objective",
  acceptance: "What done looks like",
  entry_points: "Where to start",
  boundaries: "Boundaries",
  dependencies: "Dependencies",
};

export const readinessLabel = (check: string): string =>
  READINESS_LABEL[check] ?? check;

// A friendly label for a scope key, for filters and the scope link.
export function scopeLabel(scope?: string): string {
  if (!scope) return "unknown";
  // Work ON the Workshop: the platform repo and this installation's host. The
  // only name that resolves there, and only in a developer build (ADR 0014).
  if (scope === DEVELOPMENT_SCOPE) return "Development";
  if (scope === "workshop") return "Workshop root";
  const [kind, slug] = scope.split(":", 2);
  if (!slug) return scope;
  const kindLabel = kind === "project" ? "Project" : kind === "agent" ? "Agent" : kind === "build" ? "Build" : kind;
  return `${kindLabel}: ${slug}`;
}

// The canonical route for one ticket. Four screens were building this string by
// hand and a fifth (RunDetail's "Linked ticket") built a DIFFERENT one —
// `/work?ticket=<id>` — which nothing reads: Work parses only `scope`, so the
// link silently landed on an unfiltered ticket list. A route spelled in five
// places is a route that will be spelled wrong in one of them.
//
// A scope is required rather than defaulted, because guessing `workshop` for a
// project ticket produces a link that resolves to a 404 detail page instead of
// failing where the mistake is.
export function ticketHref(scope: string, id: string): string {
  return `/tickets/${encodeURIComponent(scope || "workshop")}/${encodeURIComponent(id)}`;
}

// The scope a Run's ticket lives in. A Run is bound to a project or it is not,
// and its ticket sits in that project's queue or the development queue — the
// same rule task_scope.root_for_context applies server-side.
//
// `workshop` and `development` both map to Development. Every sweep against the
// platform repo before ADR 0014 stamped its job `project_slug: "workshop"`, and
// those tickets did not move — the queue was renamed, not relocated — so the old
// slug has to keep resolving or a year of run history loses its ticket link.
// A job with no project binding is a platform-repo run too, which is what it
// always meant.
export function runTicketScope(job: { project_slug?: string }): string {
  if (!job.project_slug || job.project_slug === "workshop") return DEVELOPMENT_SCOPE;
  if (job.project_slug === DEVELOPMENT_SCOPE) return DEVELOPMENT_SCOPE;
  return `project:${job.project_slug}`;
}

// The in-app route a scope links to (project detail / agent detail), or null
// when the scope has no dedicated page (development, workshop root, build
// agents).
export function scopeHref(scope?: string): string | null {
  if (!scope) return null;
  if (scope === DEVELOPMENT_SCOPE) return null;
  const [kind, slug] = scope.split(":", 2);
  if (kind === "project" && slug) return `/projects/${encodeURIComponent(slug)}`;
  if (kind === "agent" && slug) return `/agents/${encodeURIComponent(slug)}`;
  return null;
}
