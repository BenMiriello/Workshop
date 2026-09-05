// GENERATED, not hand-written. The wire shapes below alias into the backend's
// own OpenAPI schema (`npm run gen:api-types`), which knows them because
// `routes/api_models.py` describes what the ticket routes return.
//
// FOUR NARROWINGS SURVIVE, and each one is TRUE rather than hopeful.
// `services/tasks.py::_normalize` COERCES an unrecognised status to "todo" or
// "done" before it ever reaches the wire, so the eight-value union below is
// what a client can actually receive, not a guess about it. The backend types
// these four as `str` because a Literal on a response model 500s on any
// historical record outside the set, which is a real cost for no client gain;
// the narrowing therefore lives on this side, where it is a read and not a
// validation. Everything not narrowed is the schema verbatim.
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
import type { components } from "../api/schema.gen";
import { DEVELOPMENT_SCOPE, scopeLabel as _scopeLabel, scopeHref as _scopeHref } from "./scope";

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

export type TaskEvidence = components["schemas"]["TaskEvidenceEntry"];

// A comment is what a person or an agent SAID about the ticket; evidence is what
// the system RECORDED about a run. They are different channels on purpose: a
// comment is attributed and a piece of evidence is not, and mixing them turns
// "the reviewer rejected this" into something that looks like an opinion.
//
// A comment is NOT part of the spec. An answer a worker must act on is written
// into the ticket body by the question path, because a worker is handed the
// ticket text and nothing else.
export type TaskComment = components["schemas"]["TaskComment"];

// A linked ticket, resolved server-side. The edge fields on the record
// (`depends_on`, `relates_to`, `parent`) hold BARE ids with no scope, so a
// client cannot build a working href from them: every screen that tried used
// the CURRENT ticket's scope and produced a 404 for anything cross-scope. The
// detail endpoint resolves them instead, which is the only place that can.
export type TaskLink =
  Omit<components["schemas"]["TaskLink"], "status"> & { status: TaskStatus };

export type TaskLinks =
  Omit<components["schemas"]["TaskLinks"], "parent" | "children" | "depends_on" | "relates_to"> & {
    parent: TaskLink | null;
    children: TaskLink[];
    depends_on: TaskLink[];
    relates_to: TaskLink[];
  };

// Acceptance gate the worker must clear before a task can finish. Matches the
// store's shape (backend services/tasks.py): reviewer = a reviewer pass judges
// it; command = `spec` is a shell command that must exit 0; operator = the human
// approves. `spec` only carries meaning for the command kind.
//
// The list is the definition and the type is derived from it, so a fourth kind
// can only be added in one place. lib/ticketEdit.ts used to restate the same
// three as TICKET_ACCEPTANCE_KINDS; that copy is gone.
export const TASK_ACCEPTANCE_KINDS = ["reviewer", "command", "operator"] as const;

export type TaskAcceptanceKind = typeof TASK_ACCEPTANCE_KINDS[number];

// The words a picker shows for each kind. Keyed by the type rather than written
// as a free list, so adding a fourth kind above fails to compile until it has a
// label. TaskPanel restated these three as hardcoded <option> elements until
// 2026-08-17; JSX is invisible to check_one_definition.py, which is exactly why
// that copy outlived the array copies it was drifting alongside.
export const TASK_ACCEPTANCE_LABELS: Record<TaskAcceptanceKind, string> = {
  reviewer: "reviewer pass",
  command: "command exits 0",
  operator: "operator approves",
};

// Intersected, NOT `Omit`-ed. The generated type carries an index signature
// (the backend's `extra="allow"`, which is what stops a preserved-verbatim key
// being dropped), and `Omit` on a type with an index signature widens `keyof`
// to `string | number` and throws every named property away. The intersection
// narrows `kind` and keeps `spec` and `criteria` readable.
export type TaskAcceptance =
  components["schemas"]["TaskAcceptance"] & { kind: TaskAcceptanceKind };

// ---- effort: how much depth the ticket is worth -----------------------------
// The store's effort hint (backend services/tasks.py EFFORTS), a hint to the
// worker about depth. Not execution (that is whether a machine can do it at
// all).
//
// It lives here rather than in a component or in lib/ticketEdit because two
// unrelated screens read it: the filter row and the ticket editor. It was
// declared in both of them, verbatim, until 2026-08-17. The list is the
// definition; the type is derived from it.
export const TASK_EFFORTS = ["quick", "standard", "deep"] as const;

export type TaskEffort = typeof TASK_EFFORTS[number];

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

export type TicketTriage = components["schemas"]["TicketTriage"];

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

// What a stopped task is asking for. One shape for every reason work stops — a
// permission, a missing resource, an ambiguous ticket — so one surface answers
// all of them. `kind` is an open slug, not a union: the named kinds are the ones
// with a producer today, not the boundary of what can stop a job.
export type BlockRequest =
  Omit<components["schemas"]["Awaiting"], "context"> & {
    // The backend leaves `context` a free-form dict, so the schema renders it as
    // an empty record. Narrowed here by the client that reads it, exactly as the
    // chat client narrows the event log; see scripts/check_opaque_fields.py for
    // why a deliberately open payload is allowed to stay open.
    context?: Record<string, unknown>;
  };

export type Task =
  Omit<components["schemas"]["Task"],
       "status" | "execution" | "state_type" | "acceptance"
       | "triage" | "evidence" | "comments" | "awaiting" | "blocked_request"
       | "blocked_by" | "links" | "readiness"> & {
    status: TaskStatus;
    execution?: TaskExecution | null;
    state_type?: TaskStateType;
    acceptance?: TaskAcceptance | null;
    triage?: TicketTriage | null;
    // Required, matching the schema: `_normalize` always emits both, and the
    // ticket routes do not use `response_model_exclude_unset`, so a default is
    // always on the wire. Re-adding them as optional would be weaker than what
    // the server actually promises.
    evidence: TaskEvidence[];
    comments: TaskComment[];
    awaiting?: BlockRequest | null;
    blocked_request?: BlockRequest | null;
    blocked_by?: TaskLink[];
    links?: TaskLinks;
    readiness?: TicketReadiness;
  };

// ---- source: WHERE THE TICKET CAME FROM -------------------------------------
// Mirrors backend services/tasks.SOURCE_KINDS. It has been on every ticket since
// the store existed and no view read it.
//
// IT IS PROVENANCE, NOT INTENT, and the difference is the whole reason `unknown`
// is in the list. Until 2026-09-01 a ticket with no recorded source read as
// `operator`, and the two filing doors disagreed (the API stamped `operator`
// when told nothing, the CLI stamped `agent`). So 34 tickets said the operator
// filed them and he said he had filed "maybe one or two"; twelve were an agent
// writing up his complaints through the door that defaults to his name. Both
// doors now record `unknown` when nobody says, and the older records are NOT
// re-attributed, because deciding after the fact what someone meant is the
// failure that produced this.
//
// So this field answers "where did this come from", and a filter over it is a
// filter over a stored value. It does not answer "what did the operator ask
// for": that needs a concept this system has not designed yet.

export type TaskSource = "operator" | "chat" | "agent" | "autogen" | "unknown";

export const TASK_SOURCES: TaskSource[] =
  ["operator", "chat", "agent", "autogen", "unknown"];

// An unrecorded or unrecognised source reads as `unknown`, never as a person.
export const sourceOf = (task: { source?: { kind?: string | null } | null }): TaskSource => {
  const kind = (task.source?.kind ?? "") as TaskSource;
  return TASK_SOURCES.includes(kind) ? kind : "unknown";
};

// Each one says what was RECORDED, in the words that claim exactly that much.
const SOURCE_LABEL: Record<TaskSource, string> = {
  operator: "Filed by the operator",
  chat: "Filed from a chat",
  agent: "Filed by an agent",
  autogen: "Filed by a program",
  unknown: "Provenance not recorded",
};

export const sourceLabel = (kind?: string | null): string =>
  SOURCE_LABEL[(kind ?? "") as TaskSource] ?? String(kind ?? "");

// The same fact as ONE WORD, for a list row's meta line.
//
// `sourceLabel` is a sentence because a filter menu has the width for one and a
// row does not: `.ws-listrow__meta` neither wraps nor scrolls, so "Filed by an
// agent" is not shown small there, it pushes the facts after it off the edge.
//
// `unknown` returns "", which DROPS the fact rather than printing a word. The
// same rule as `sourceOf`: a ticket nobody recorded a source for must not read
// as having come from somewhere, and "unknown" on two rows out of three is a
// column of noise. The ticket page still says "Provenance not recorded" in
// full, where there is room to say it properly.
const SOURCE_SHORT: Record<TaskSource, string> = {
  operator: "operator",
  chat: "chat",
  agent: "agent",
  autogen: "program",
  unknown: "",
};

export const sourceShort = (kind?: string | null): string =>
  SOURCE_SHORT[(kind ?? "") as TaskSource] ?? "";

// ---- ordering ---------------------------------------------------------------
// Mirrors backend tasks.SORT_KEYS. The list endpoint sorts; the client only
// names the key, so the two cannot drift into two different ideas of "priority".

export type TaskSort =
  | "updated" | "created" | "priority" | "title" | "status" | "queue" | "source";

export const TASK_SORTS: { value: TaskSort; label: string }[] = [
  { value: "updated", label: "Last updated" },
  { value: "created", label: "Newest" },
  { value: "priority", label: "Priority" },
  { value: "status", label: "Status" },
  { value: "title", label: "Title" },
  // The scheduler's own order (priority desc, then oldest first), so the top of
  // the list is what a sweep would actually pick up next rather than a guess.
  { value: "queue", label: "Next to run" },
  // WHERE IT CAME FROM: operator, then chat, then agent, then program, then
  // whatever recorded nothing; inside each, the scheduler's order. On a queue
  // that is two thirds agent-filed, grouping by the door a ticket came through
  // is what lets the rest of the list be read at all.
  { value: "source", label: "Source" },
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
//
// `doing` reads as "in progress" (design handoff 2026-08-14). The STORED value
// is untouched: this is the one place a status becomes display text, so the
// rename costs one line here and nothing anywhere else.
const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  draft: "draft",
  todo: "todo",
  doing: "in progress",
  in_review: "in review",
  blocked: "blocked",
  parked: "parked",
  done: "done",
  cancelled: "cancelled",
};

export const taskStatusLabel = (status?: string): string =>
  TASK_STATUS_LABEL[(status ?? "") as TaskStatus] ?? String(status ?? "");

// The same word as a HEADING: a board column, a mobile group header. Lowercase
// is right inline ("to do · workshop · 2h") and wrong as the title of a column,
// and uppercasing in CSS is wrong too ("IN PROGRESS" shouts a lane name).
//
// Derived from the label rather than a second table, so a rename cannot land in
// one of them and not the other.
export const taskStatusTitle = (status?: string): string => {
  const s = taskStatusLabel(status);
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
};

// ---- the ticket key ---------------------------------------------------------
// The short, mono, human-quotable handle for a ticket: `a3f19c`.
//
// The design handoff drew a per-scope sequence (`IT-06`) and the store has no
// such field — an id is a 32-char uuid4 hex with no ordering in it, so there is
// nothing to derive a sequence FROM without adding a counter and migrating 188
// live records. The first six characters of the id are stable, unique enough to
// name a row out loud, and already what every `git` short hash does.
//
// Six, not eight: at 188 tickets the chance of any collision is ~0.05%, and the
// key is a visual anchor beside the title, not an identifier anything resolves.
// Every link still carries the full id (see `ticketHref`).
export const TICKET_KEY_LEN = 6;

export const ticketKey = (id?: string | null): string =>
  (id ?? "").slice(0, TICKET_KEY_LEN);

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
export type ReadinessGap = components["schemas"]["ReadinessItem"];

export type TicketReadiness =
  Omit<components["schemas"]["Readiness"], "missing"> & { missing: ReadinessGap[] };

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
// opener is what turns a line into a list.
//
// THE SHARED PART IS THE VERB, NOT WHATEVER WORDS HAPPEN TO MATCH. This used to
// strip the longest common word prefix, gated on "two or more words is a real
// opener". `objective` and `acceptance` share FOUR:
//
//   does not say what this is trying to achieve
//   does not say what done looks like
//
// so the second was cut to "done looks like" and the line read "Does not say
// what this is trying to achieve, or done looks like." Those two are the most
// common pair the checker emits, and it is the first sentence a task-proposal
// card shows. Matching on the verb list can only ever remove a verb.
function withoutSharedOpener(clause: string, previous: string): string {
  for (const v of READINESS_VERBS) {
    if (previous.startsWith(v) && clause.startsWith(v)) return clause.slice(v.length);
  }
  return clause;
}

// The checks that REFUSE a run, mirroring `ticket_readiness.BLOCKING`. Everything
// else is advice. They are separated here because they are a different kind of
// statement and reading them as one was wrong twice over:
//
//  1. Grammar. The dependencies `why` is "it depends on a ticket that does not
//     exist: <id>" — it has no strippable verb, so listing it produced "Three
//     things are unspecified: what done looks like, ... and depends on a ticket
//     that does not exist: T-4821."
//  2. Signal. `POST /run` returns 400 on these two and on nothing else, so
//     rendering them in the same ink as five advisory sentences told the
//     operator nothing about which one stops the button.
const READINESS_BLOCKING = ["title", "dependencies"];

export const readinessBlockers = (gaps: ReadinessGap[] | undefined | null) =>
  (gaps ?? []).filter((g) => READINESS_BLOCKING.includes(g?.check));

// `error` when a gap actually refuses the run, `warn` when it is advice. Feeds
// `Notice tone=`, which is a different vocabulary from the five status kinds.
export const readinessTone = (gaps: ReadinessGap[] | undefined | null):
  "warn" | "error" => (readinessBlockers(gaps).length > 0 ? "error" : "warn");

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
  const all = (gaps ?? []).filter((g) => (g?.why ?? "").trim());
  if (all.length === 0) return "";

  // A blocking gap leads, as its own sentence, and says that it blocks. Its
  // `why` is already a full clause with a subject ("the ticket has no title",
  // "it depends on a ticket that does not exist: T-4821"), so it reads after a
  // colon and does not survive being cut into a list of noun phrases.
  const blocking = readinessBlockers(all);
  if (blocking.length > 0) {
    const lead = `Cannot run yet: ${blocking[0].why.trim()}.`;
    const rest = all.length - 1;
    if (rest === 0) return lead;
    return `${lead} ${rest} other thing${rest === 1 ? "" : "s"} unspecified.`;
  }

  const list = all;
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
// This is NOT a second checker. The `why` strings are the checker's own, copied
// verbatim; if one changes in services/ticket_readiness.py, change it here.
//
// It used to collapse to "is this field filled", because the card carried one
// editable field per labelled section. It no longer does: a ticket is a title
// and a description (commit 010a9a4), the ticket page owns every other field,
// and a card that re-edits them is the second editor the tickets-in-chat plan
// rules out (§3). So the sections are structure INSIDE the description, and
// this asks the same question the server asks: does the body state the field.
// The per-section keys stay accepted for the proposals already sitting in
// transcripts on disk, which re-render from their stored block.
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

// The labels a section may be written under, ported from services/
// ticket_format.py FIELDS. Same synonyms, same tolerance for the punctuation a
// model actually emits ("**Output:**", "## Done when -"), because the producer
// on both sides is a prompt. Adding a synonym is one edit in each file.
const SECTION_LABELS = {
  objective: ["objective", "goal", "why", "problem", "outcome"],
  output: ["output", "done", "done when", "done looks like", "acceptance",
    "accept", "verify", "proof", "definition of done"],
  entry_points: ["start from", "entry points", "entrypoints", "begin with",
    "starting point", "files"],
  boundaries: ["boundaries", "out of scope", "do not touch", "don't touch",
    "non-goals", "not in scope"],
} as const;

function labelRe(alts: readonly string[]): RegExp {
  const escaped = [...alts]
    .sort((a, b) => b.length - a.length)
    .map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");
  return new RegExp(
    `^\\s{0,3}(?:#{1,6}\\s*)?(?:\\*\\*|__)?\\s*(?:${escaped})\\s*(?:\\*\\*|__)?\\s*[:\\-–—]`,
    "im",
  );
}

const SECTION_RE: Record<keyof typeof SECTION_LABELS, RegExp> = {
  objective: labelRe(SECTION_LABELS.objective),
  output: labelRe(SECTION_LABELS.output),
  entry_points: labelRe(SECTION_LABELS.entry_points),
  boundaries: labelRe(SECTION_LABELS.boundaries),
};

/** Does this body state the given section? `ticket_format.states`, ported. */
export function statesSection(body: string, key: keyof typeof SECTION_LABELS): boolean {
  return SECTION_RE[key].test(body || "");
}

export type TicketProposal = {
  title?: string;
  // The ticket's description: the spec a worker acts on, with its sections
  // written as labelled lines. This is the card's one body field.
  description?: string;
  effort?: string;
  // Legacy: one field per section, and the unstructured fallback body. Kept for
  // proposals stored in transcripts before the card became title + description.
  objective?: string;
  output?: string;
  entry_points?: string;
  boundaries?: string;
  text?: string;
};

export function proposalGaps(v: TicketProposal): ReadinessGap[] {
  const val = (k: keyof TicketProposal): string => (v[k] ?? "").trim();
  const gaps: ReadinessGap[] = [];
  const add = (check: keyof typeof READINESS_WHY) =>
    gaps.push({ check, why: READINESS_WHY[check] });

  const body = [val("description"), val("objective"), val("output"),
    val("entry_points"), val("boundaries"), val("text")].filter(Boolean).join("\n");
  // A section is stated when its own legacy field is filled, or when the
  // description writes it under a recognized label.
  const has = (k: keyof typeof SECTION_LABELS): boolean =>
    !!val(k) || statesSection(body, k);

  if (!val("title") && !val("text")) add("title");
  // The checker also passes a body of 40 characters or more without the label.
  if (!has("objective") && body.length < 40) add("objective");
  if (!has("output")) add("acceptance");
  // A one-edit ticket does not have to name its entry points.
  if (val("effort") !== "quick" && !has("entry_points") && !ENTRY_POINT_RE.test(body)) {
    add("entry_points");
  }
  if (!has("boundaries")) add("boundaries");
  return gaps;
}

// A friendly label for a scope key, for filters and the scope link.
// Moved into `./scope`, the single owner of the scope model
// (memory-and-documentation-model-2026-08-14.md sec 7.1); re-exported here so
// every existing import of `scopeLabel` from `lib/tasks` keeps working.
export const scopeLabel = _scopeLabel;

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

// ---- what a list row says beside the title ----------------------------------
// The small facts on a ticket row, in reading order, as plain words.
//
// This is the rule frontend/CLAUDE.md states for every list in this app: the
// status DOT is not enough on its own, so the WORD goes in the meta line as
// plain text ("to do · workshop · 2h"), because "is this blocked or just
// queued" is what a ticket list is read for and a colour answers that only for
// a reader who has already learned the five hues. It is a function rather than
// four conditionals inside the row so the ordering and the dropping rules are
// executed in tasks.test.ts.
//
// CAPPED AT THREE, for the same reason `lib/workView.rowFacts` is:
// `.ws-listrow__meta` neither wraps nor scrolls, so a fourth fact is not shown
// small, it is silently clipped. Time is NOT one of the three — it has its own
// slot at the end of the row (`.ws-listrow__time`) and is drawn separately.
//
// The order is what survives clipping first, most useful first:
//   1. the status word      — always; it is the question the list is asked
//   2. the scope            — only when the list actually spans more than one,
//                             because on a list already filtered to one scope
//                             it is the filter repeated on every row
//   3. who filed it         — dropped when nothing was recorded (`sourceShort`)
//
// Priority, effort and tags are deliberately absent. Measured on the live
// store, priority is unset on 45% of tickets, effort on 70% and labels on 95%,
// so they are three mostly-empty facts competing with the three above for a
// line that clips. All three remain filterable, and the ticket page shows them.
export const TICKET_ROW_FACTS = 3;

// Whether the scope is worth printing on these rows at all.
//
// IT IS A QUESTION ABOUT THE DATA, NOT ABOUT THE FILTER. The rule used to be
// "show the scope unless a scope filter is set", which is true but not enough:
// the unfiltered list on this installation is ~210 active tickets of which
// almost every one is in the same scope, so the column printed one identical
// word on fifty rows. frontend/CLAUDE.md's second test for spending anything on
// a row is "does the thing it marks actually vary on this screen?" -- and a
// constant is not a signal, whether it is constant because of a filter or
// because of what is in the store.
//
// So the caller asks about the rows it is holding. A page whose rows are all
// one scope drops it; the next page picks it back up if that page spans two,
// which is the honest answer to "does this tell me anything here".
export function spansScopes(tasks: { scope?: string | null }[]): boolean {
  const seen = new Set<string>();
  for (const t of tasks) {
    seen.add(t.scope ?? "workshop");
    if (seen.size > 1) return true;
  }
  return false;
}

export function ticketRowFacts(
  task: { status?: string; scope?: string | null; source?: { kind?: string | null } | null },
  opts?: { showScope?: boolean },
): string[] {
  const facts = [taskStatusLabel(task.status)];
  if (opts?.showScope) facts.push(_scopeLabel(task.scope ?? undefined));
  const filedBy = sourceShort(sourceOf(task));
  if (filedBy) facts.push(filedBy);
  return facts.slice(0, TICKET_ROW_FACTS);
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
export function runTicketScope(job: { project_slug?: string | null }): string {
  if (!job.project_slug || job.project_slug === "workshop") return DEVELOPMENT_SCOPE;
  if (job.project_slug === DEVELOPMENT_SCOPE) return DEVELOPMENT_SCOPE;
  return `project:${job.project_slug}`;
}

// The in-app route a scope links to (project detail / agent detail), or null
// when the scope has no dedicated page (development, workshop root, build
// agents). Moved into `./scope`; see `scopeLabel` above.
export const scopeHref = _scopeHref;
