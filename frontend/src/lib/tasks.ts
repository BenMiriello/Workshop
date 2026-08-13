// The Task type + status mapping + the title/description accessors, in ONE
// place. A Task is the canonical unit of work (a "ticket" in UI prose); the
// store, CLI, and API all say `task`, so every identifier here stays `task`.
//
// `title` and `body` are STORED fields on the record (backend services/tasks.py,
// ticket-model-and-external-trackers-2026-08-07). `text` is still returned as a
// projection of the two, so the accessors below prefer the real fields and fall
// back to splitting `text` for a payload cached before the change. Backed by GET
// /api/tasks and /api/tasks/{scope}/{id}.
import type { StatusKind } from "@workshop/ui";
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

// ---- state type: the category above the status -------------------------------
// Two-level status is the convention every tracker in the survey converged on
// (Linear 6 types, Jira `statusCategory`, Shortcut 4, GitHub state+state_reason).
// Workshop keeps its eight named statuses and adds the coarse category above
// them, mirroring backend services/tasks.py `_STATE_TYPE` / `state_type()`.
//
// `blocked` is `started` because the work HAS begun and is stuck. `parked` is
// `backlog` because it is out of the active plan but not finished. The type is
// spelled `canceled` (one L) to match every tracker's API, while the status
// value stays `cancelled` (two Ls) because it is persisted in live records.
//
// The server sends `state_type` on every row (route layer only, never stored).
// The tables below exist anyway for two reasons: a payload cached before
// 2026-08-12 carries no `state_type`, and the UI's groupings are DERIVED from
// this one table rather than hand-listed once per surface.
export type TaskStateType =
  | "backlog"
  | "unstarted"
  | "started"
  | "completed"
  | "canceled";

// Pipeline order: left to right is progress. This is the board's lane order.
export const TASK_STATE_TYPES: TaskStateType[] = [
  "backlog",
  "unstarted",
  "started",
  "completed",
  "canceled",
];

// THE one table. Every status grouping in the frontend derives from it; see the
// header of lib/ticketGroups.ts for the three that must agree.
export const STATUSES_BY_STATE_TYPE: Record<TaskStateType, TaskStatus[]> = {
  backlog: ["draft", "parked"],
  unstarted: ["todo"],
  started: ["blocked", "doing", "in_review"],
  completed: ["done"],
  canceled: ["cancelled"],
};

const TASK_STATE_TYPE_OF: Record<string, TaskStateType> = Object.fromEntries(
  TASK_STATE_TYPES.flatMap((type) =>
    STATUSES_BY_STATE_TYPE[type].map((status) => [status, type] as const)),
);

// An unrecognised status reads as `backlog`, matching the backend's own bias
// toward "not started yet" for a record it cannot classify: a board must never
// drop a row.
export const taskStateType = (status?: string): TaskStateType =>
  TASK_STATE_TYPE_OF[status ?? ""] ?? "backlog";

// Prefer what the server derived, fall back to deriving it from the status. The
// two cannot disagree today; the fallback is for a row that predates the field.
export const taskStateTypeOf = (
  task: { state_type?: string | null; status?: string },
): TaskStateType =>
  TASK_STATE_TYPES.includes((task.state_type ?? "") as TaskStateType)
    ? (task.state_type as TaskStateType)
    : taskStateType(task.status);

export type TaskEvidence = {
  kind?: string;
  ref?: string;
  ts?: string;
  [k: string]: unknown;
};

// A comment is what a person or an agent SAID about the ticket; evidence is what
// the system RECORDED about a run. They are different channels on purpose: a
// comment is attributed and a piece of evidence is not, and mixing them turns
// "the reviewer rejected this" into something that looks like an opinion.
//
// A comment is NOT part of the spec. An answer a worker must act on is written
// into the ticket body by the question path, because a worker is handed the
// ticket text and nothing else.
export type TaskComment = {
  id: string;
  author: string;
  text: string;
  ts: string;
};

// A linked ticket, resolved server-side. The edge fields on the record
// (`depends_on`, `relates_to`, `parent`) hold BARE ids with no scope, so a
// client cannot build a working href from them: every screen that tried used
// the CURRENT ticket's scope and produced a 404 for anything cross-scope. The
// detail endpoint resolves them instead, which is the only place that can.
export type TaskLink = {
  id: string;
  scope: string;
  title: string;
  status: TaskStatus;
};

export type TaskLinks = {
  parent: TaskLink | null;
  children: TaskLink[];
  depends_on: TaskLink[];
  relates_to: TaskLink[];
  // Ids that resolved in no scope at all. Rendered as dead references rather
  // than as links, so a dangling edge is visible instead of silent.
  missing: string[];
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

// Each line says what the worker DOES when the ticket is silent, and none of
// them implies permission. That is the whole point of the rewording: the old
// copy read as a privilege level ("decides freely", "blocks often"), which is
// how a setting that only shapes whether a worker ASKS came to be described,
// and then believed, as a limit on what it may reach.
// `services/latitude.py` already retracted the "enforced floor" language in its
// docstring; this is the UI agreeing with it.
const LATITUDE_DESC: Record<Latitude, string> = {
  strict: "Asks when the ticket does not say.",
  standard: "Decides reversible things, asks about the rest.",
  open: "Decides and reports at the end.",
};

export const latitudeDesc = (v?: string): string =>
  LATITUDE_DESC[(v ?? "") as Latitude] ?? "";

// ---- execution: can a machine do this at all --------------------------------
// A SPEC field (backend services/tasks.py, autonomy-implementation-2026-08-13
// §1.1), stored flat beside `effort` and `priority`. It answers one question:
// is this ticket the kind of thing a worker can execute, or does it need a
// person?
//
//   code       a change to a tree a worker can write. Eligible for the sweep.
//   judgement  the output is a decision, a design or an audit. Never swept.
//   external   needs root, another host user, a vendor console or a secret.
//   null       not classified yet. Never swept: the sweep must not guess.
//
// null is NOT code. The absence of a classification fails closed, which is why
// the rail says "not classified" rather than leaving the row out.
export type TaskExecution = "code" | "judgement" | "external";

export const TASK_EXECUTIONS: TaskExecution[] = ["code", "judgement", "external"];

// The one place a stored value becomes display text. The words are the stored
// values because they already say the thing; what they MEAN is carried by the
// editor's help text, where a reader who does not know the field is looking.
export const EXECUTION_LABEL: Record<TaskExecution, string> = {
  code: "code",
  judgement: "judgement",
  external: "external",
};

// An unset (or unrecognised) value reads as unclassified, never as `code`.
export const executionLabel = (v?: string | null): string =>
  EXECUTION_LABEL[(v ?? "") as TaskExecution] ?? "not classified";

// ---- the queue triager's mark -----------------------------------------------
// What a triage pass decided about this ticket's SPEC. Not a status and not a
// gate: `underspecified` means it also raised a question, which shows up as the
// ticket's block, and that is the thing worth acting on. This row exists so a
// ticket can say it has been looked at, which is the difference between "nobody
// has reviewed this" and "reviewed, nothing to do".
export type TriageDecision = "ok" | "split" | "underspecified" | "none";

export type TicketTriage = {
  at?: string | null;
  decision?: TriageDecision | string | null;
  reason?: string | null;
  spec_hash?: string | null;
};

const TRIAGE_LABEL: Record<TriageDecision, string> = {
  ok: "reviewed, specified well enough",
  split: "reviewed, proposed splitting it",
  underspecified: "reviewed, asked a question",
  none: "reviewed, nothing to do",
};

// Phrased as an outcome rather than as a verdict word. "ok" alone reads as a
// status the ticket does not have; "reviewed, specified well enough" says who
// did what.
export const triageLabel = (v?: string | null): string =>
  TRIAGE_LABEL[(v ?? "") as TriageDecision] ?? "reviewed";

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
  // Can a machine do this at all (see TaskExecution). null = not classified.
  execution?: TaskExecution | null;
  // The queue triager's mark: what it decided and against which version of the
  // spec. Stored flat AND projected under `exec`; read it flat, like every other
  // execution field. `spec_hash` is deliberately not surfaced in the UI — it is
  // the triager's own bookkeeping, and showing a hash to a person says nothing.
  triage?: TicketTriage | null;
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
  comments?: TaskComment[];
  source?: { kind?: string; ref?: string | null };
  // How many times a worker has attempted this ticket, and the per-ticket cap.
  attempts?: number;
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
  // ---- derived on the way out, never stored (routes/tasks_all._with_triage) --
  // The coarse category above `status` (see TaskStateType).
  state_type?: TaskStateType;
  // Could the scheduler pick this up right now? A PURE predicate over stored
  // state, matching `tasks.is_ready`, so it lags a crashed claim by one watchdog
  // interval and treats a dependency it cannot see as satisfied.
  ready?: boolean;
  // Why not: the non-terminal subset of `depends_on`, resolved ACROSS scopes.
  // Deliberately more honest than `ready`, so a row can read `ready: true` next
  // to a non-empty `blocked_by`. That is the documented scheduler gap
  // (ticket-system-consolidation-2026-08-12 §3.2), not a contradiction.
  blocked_by?: TaskLink[];
  // Attached by the ticket detail endpoint only (it needs the scope's id set to
  // resolve dependencies), so it is absent on list rows.
  readiness?: TicketReadiness;
  // Also detail-only: the graph edges resolved to real, linkable tickets.
  links?: TaskLinks;
};

// ---- ordering ---------------------------------------------------------------
// Mirrors backend tasks.SORT_KEYS. The list endpoint sorts; the client only
// names the key, so the two cannot drift into two different ideas of "priority".

export type TaskSort =
  | "updated" | "created" | "priority" | "title" | "status" | "queue";

export const TASK_SORTS: { value: TaskSort; label: string }[] = [
  { value: "updated", label: "Last updated" },
  { value: "created", label: "Newest" },
  { value: "priority", label: "Priority" },
  { value: "status", label: "Status" },
  { value: "title", label: "Title" },
  // The scheduler's own order (priority desc, then oldest first), so the top of
  // the list is what a sweep would actually pick up next rather than a guess.
  { value: "queue", label: "Next to run" },
];

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
export type ReadinessGap = { check: string; why: string };

export type TicketReadiness = {
  ok: boolean;
  missing: ReadinessGap[];
  warnings: string[];
};

// ---- the readiness line -----------------------------------------------------
// ONE line of prose, never a checklist. A readiness checklist was deleted from
// the ticket page on 2026-08-12 as a nag panel, and rebuilding it with different
// markup would be the same mistake.
//
// THE CHECKER OWNS THE WORDING. Every phrase below is DERIVED from the `why`
// strings `ticket_readiness.check` already returns (backend
// services/ticket_readiness.py), by stripping the subject and, in a list, the
// verb. Nothing here invents a second vocabulary for a gap: a second set of
// wordings is how the format and its checker drifted apart the last time, and
// that is written up at the top of services/ticket_format.py.
//
// The subjects a `why` opens with. Dropping one leaves a clause that can take
// the subject the sentence needs ("Does not say ...", "..., or ...").
const READINESS_SUBJECTS = ["the ticket ", "the body ", "it "];

// The verb a clause opens with, dropped so the clause can be LISTED as a thing
// rather than read as a sentence: "does not say what done looks like" becomes
// "what done looks like".
//
// `names` is still here on purpose even though no current check uses it. The
// entry-points check USED to say "the body names no file, command or symbol to
// start from", which strips to "no file, command or symbol to start from" and
// then reads as nonsense in a list: "... and no file, command or symbol to
// start from." Rather than special-case it here (a second vocabulary, which is
// exactly what this module must not grow), the CHECKER was reworded to "the
// body does not say where to start". Keep the verb listed so a future `why`
// that opens with it degrades to a clause instead of a sentence fragment.
const READINESS_VERBS = ["does not say ", "has ", "names "];

function readinessClause(why: string): string {
  const s = (why ?? "").trim();
  for (const p of READINESS_SUBJECTS) if (s.startsWith(p)) return s.slice(p.length);
  return s;
}

function readinessItem(why: string): string {
  const c = readinessClause(why);
  for (const v of READINESS_VERBS) if (c.startsWith(v)) return c.slice(v.length);
  return c;
}

// Two gaps usually open the same way ("does not say ..."), and repeating that
// opener is what turns a line into a list. Dropped only when the run of shared
// words is long enough to be a real opener rather than a coincidental "the".
function withoutSharedOpener(clause: string, previous: string): string {
  const a = previous.split(" ");
  const b = clause.split(" ");
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i++;
  return i >= 2 ? b.slice(i).join(" ") : clause;
}

const capitalize = (s: string): string => s.charAt(0).toUpperCase() + s.slice(1);

// The whole readiness report as one sentence. Pure: no DOM, no fetch, so the
// wording is unit tested rather than eyeballed.
//
//   1 gap   "Does not say what done looks like."
//   2 gaps  "Does not say what done looks like, or which files it may change."
//   3 gaps  "Three things are unspecified: a, b, and c."
//   4+      the first two, then "and N more."
//
// Empty for no gaps, so a caller can render on truthiness.
export function readinessLine(gaps: ReadinessGap[] | undefined | null): string {
  const list = (gaps ?? []).filter((g) => (g?.why ?? "").trim());
  if (list.length === 0) return "";
  const first = readinessClause(list[0].why);
  if (list.length === 1) return `${capitalize(first)}.`;
  if (list.length === 3) {
    const [a, b, c] = list.map((g) => readinessItem(g.why));
    return `Three things are unspecified: ${a}, ${b}, and ${c}.`;
  }
  const second = withoutSharedOpener(readinessClause(list[1].why), first);
  const head = `${capitalize(first)}, or ${second}`;
  return list.length === 2 ? `${head}.` : `${head}, and ${list.length - 2} more.`;
}

// ---- readiness for a ticket that does not exist yet -------------------------
// The chat's file-a-ticket card is where a gap is cheapest to close: the fields
// are on screen and nothing has been filed. There is no server report to read
// at that point, so the four advisory checks are evaluated against the card's
// own fields here.
//
// This is NOT a second checker. The card carries one field per labelled section
// (Objective / Output / Start from / Boundaries), so "does the body state this
// field" collapses to "is this field filled" and no label matching is needed.
// The `why` strings are the checker's own, copied verbatim; if one changes in
// services/ticket_readiness.py, change it here.
const READINESS_WHY = {
  title: "the ticket has no title",
  objective: "the body does not say what this is trying to achieve",
  acceptance: "the ticket does not say what done looks like",
  entry_points: "the body does not say where to start",
  boundaries: "the ticket does not say which files it may change",
} as const;

// The checker's `_ENTRY_POINT`, ported: a repo-relative path, an inline-code
// span, a fenced block, or a shell-looking line all count as somewhere to
// begin. Kept in step with services/ticket_readiness.py.
const ENTRY_POINT_RE =
  /(?:[\w.\-/]+\/[\w.\-]+\.\w+)|(?:`[^`\n]+`)|(?:^\s{0,3}```)|(?:\b(?:run|npm|pytest|python3?|bash|curl|git|make)\s+\S+)/m;

export type TicketProposal = {
  title?: string;
  objective?: string;
  output?: string;
  entry_points?: string;
  boundaries?: string;
  effort?: string;
  // The unstructured fallback body, for a proposal that carries no sections.
  text?: string;
};

export function proposalGaps(v: TicketProposal): ReadinessGap[] {
  const val = (k: keyof TicketProposal): string => (v[k] ?? "").trim();
  const gaps: ReadinessGap[] = [];
  const add = (check: keyof typeof READINESS_WHY) =>
    gaps.push({ check, why: READINESS_WHY[check] });

  const body = [val("objective"), val("output"), val("entry_points"),
    val("boundaries"), val("text")].filter(Boolean).join("\n");

  if (!val("title") && !val("text")) add("title");
  // The checker also passes a body of 40 characters or more without the label.
  if (!val("objective") && body.length < 40) add("objective");
  if (!val("output")) add("acceptance");
  // A one-edit ticket does not have to name its entry points.
  if (val("effort") !== "quick" && !val("entry_points") && !ENTRY_POINT_RE.test(body)) {
    add("entry_points");
  }
  if (!val("boundaries")) add("boundaries");
  return gaps;
}

// A friendly label for a scope key, for filters and the scope link.
export function scopeLabel(scope?: string): string {
  if (!scope) return "unknown";
  // Work ON the Workshop: the platform repo and this installation's host. The
  // only name that resolves there, and only in a developer build (ADR 0014).
  if (scope === DEVELOPMENT_SCOPE) return "Development";
  // "Workshop", not "Workshop root". The word `root` is the implementation
  // detail that this scope's store lives at the top of the tree; to a reader it
  // reads as a second, more important Workshop somewhere. Operator, 2026-08-12.
  if (scope === "workshop") return "Workshop";
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
