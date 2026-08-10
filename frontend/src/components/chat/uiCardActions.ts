// ui-card action registry.
//
// A `ui-card` block (see UiCard.tsx) carries a `kind` and a list of `actions`.
// Each action's `id` maps HERE to a registered async handler. The model can
// only name an action id, never an endpoint, so this registry is the entire
// surface a card can drive. An unknown action id resolves to nothing (inert).
//
// Handler signature: (state, ctx) => Promise<CardResult>. `state` is the card's
// live editable values; `ctx` gives the handler the few things it needs to talk
// to the app (navigation, the current context/thread, the rescope wrapper).
//
// State-on-reload note: cards are stateless server-side in Phase 1. A reloaded
// thread re-renders each card in its edit state from the stored block; that is
// why `createProject` treats 409 (slug already exists) as a success-ish state,
// so a second submit after reload is safe and idempotent.

import type { TaskProposalSpec } from "@shared/types";
import { pickupFor, pickupNote, scopeIsRunnable, type PickupMap } from "../../lib/scopePickup";
import { ticketHref } from "../../lib/tasks";

// ---- Wire shape (matches the A.1 contract) ---------------------------------
export type UiCardFieldType =
  | "text"
  | "slug"
  | "textarea"
  | "template"
  | "sensitivity"
  | "select"
  | "taskscope"; // a select the client fills from GET /api/tasks/scopes

export type UiCardSelectOption = { value: string; label: string };

export type UiCardField = {
  name: string;
  label?: string;
  type?: UiCardFieldType;
  value?: string;
  options?: UiCardSelectOption[];
  help?: string; // optional one-line hint, rendered under the control
};

export type UiCardActionVariant =
  | "default"
  | "primary"
  | "ghost"
  | "danger"
  | "danger-ghost";

export type UiCardAction = {
  id: string;
  label: string;
  variant?: UiCardActionVariant;
};

export type UiCardSpec = {
  id: string;
  kind: string;
  title?: string;
  fields?: UiCardField[];
  actions?: UiCardAction[];
  // Server-emitted `job-status` cards carry these instead of fields: the live
  // run to poll, and where it came from (for the links).
  job_id?: string;
  task_id?: string;
  scope?: string;
};

// Kinds the client knows how to render as a live, interactive card. Anything
// else falls back to an inert (read-only, no-button) card. `job-status` is
// rendered by a dedicated poller (see UiCard.tsx), not the generic form path.
export const KNOWN_KINDS = new Set<string>([
  "project-proposal",
  "task-proposal",
  "job-status",
]);

// ---- Handler contract ------------------------------------------------------
export type CardState = {
  spec: UiCardSpec;
  values: Record<string, string>;
};

export type UiCardCtx = {
  navigate: (to: string) => void;
  contextKey: string;
  threadId: string | null;
  rescopeThread: (id: string, contextKey: string) => Promise<unknown>;
  // Server-backed record of which cards in this thread have already been acted
  // on, and the writer for it. Cards are no longer stateless across a reload:
  // one that was filed and run renders its outcome instead of offering the same
  // buttons again (which is how the same work got queued twice).
  cardStates?: Record<string, { phase: string; note?: string | null; slug?: string | null; title?: string | null }>;
  recordCardState?: (
    key: string,
    state: { phase: string; note?: string | null; slug?: string | null; title?: string | null },
  ) => void;
};

export type CardResult =
  | {
      status: "success";
      slug?: string;
      title?: string;
      note?: string;
      actions?: UiCardAction[];
      // Extra card values to stash so follow-up actions can read them (e.g. the
      // job id a run produced, consumed by `viewRun`).
      values?: Record<string, string>;
    }
  | { status: "error"; message: string }
  | { status: "dismiss" }
  | { status: "noop" };

export type UiCardHandler = (state: CardState, ctx: UiCardCtx) => Promise<CardResult>;

// Shared kebab slugify (ProjectNew imports this too, so the card and the
// builder page can't drift). Collapses runs of separators to one dash; the
// backend accepts `[a-z0-9][a-z0-9-]*` up to 48 chars.
export const slugify = (s: string): string =>
  s
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);

// ---- Phase 1 handlers ------------------------------------------------------
const createProject: UiCardHandler = async (state) => {
  const v = state.values;
  const slug = (v.slug || slugify(v.title || "")).trim();
  const title = (v.title || slug).trim();
  const body = {
    slug,
    title,
    purpose: (v.purpose || "").trim(),
    template: v.template || "blank",
    sensitivity: v.sensitivity || "shareable",
    brief: (v.brief || "").trim(),
  };
  const r = await fetch("/api/projects/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) {
    const p = await r.json();
    return {
      status: "success",
      slug: p.slug || slug,
      title: p.title || title,
      actions: [
        { id: "openProject", label: "Open project", variant: "primary" },
        { id: "moveChatToProject", label: "Move chat into project", variant: "default" },
      ],
    };
  }
  if (r.status === 409) {
    // Slug already exists: treat as an idempotent success so a re-submit after
    // reload does not error. The common 409 is exactly that resubmit, so offer
    // the same follow-ups as a fresh create (Open + Move chat).
    return {
      status: "success",
      slug,
      title,
      note: "Project already exists.",
      actions: [
        { id: "openProject", label: "Open project", variant: "primary" },
        { id: "moveChatToProject", label: "Move chat into project", variant: "default" },
      ],
    };
  }
  return { status: "error", message: `${r.status} ${await r.text()}` };
};

const openProject: UiCardHandler = async (state, ctx) => {
  const slug = state.values.slug;
  if (slug) ctx.navigate(`/projects/${slug}`);
  return { status: "noop" };
};

const moveChatToProject: UiCardHandler = async (state, ctx) => {
  const slug = state.values.slug;
  if (!slug) return { status: "error", message: "No project slug to move into." };
  if (ctx.threadId) await ctx.rescopeThread(ctx.threadId, `project:${slug}`);
  ctx.navigate(`/projects/${slug}`);
  return { status: "success", slug, note: "Chat moved into the project." };
};

const dismiss: UiCardHandler = async () => ({ status: "dismiss" });

// ---- task-proposal handlers ------------------------------------------------
// A task-proposal card scopes work into a tracked ticket. `addTasks` files it;
// `addTasksAndRun` files it and starts a worker immediately (the run endpoint).
// The store, CLI, and API all say `task`; the UI prose says "ticket".

// The spawn-task response. `job_id` / `run_error` only appear when `run` was
// requested, and they are mutually exclusive: one run attempt, one outcome.
type CreatedTask = {
  task?: { id?: string; text?: string } | null;
  scope?: string;
  decision?: string;
  message?: string;
  job_id?: string | null;
  run_error?: string | null;
};

// The structured spec sections a task-proposal card may carry, in the order
// they compose into the ticket body. Names mirror TaskProposalSpec
// (shared/types.ts); the label is the human prefix written into the task text.
const SPEC_SECTIONS: { key: keyof TaskProposalSpec; label: string }[] = [
  { key: "objective", label: "Objective" },
  { key: "output", label: "Output" },
  { key: "entry_points", label: "Start from" },
  { key: "boundaries", label: "Boundaries" },
];

// Compose a card's editable values into the filed ticket body: the title as the
// first line, then each filled spec section on its own line. Backward-compatible
// by design: when no structured field is present it falls back to the plain
// `text` field (title line + description), or a bare title, so an older or
// minimal proposal still files unchanged.
export function composeTaskText(v: Record<string, string>): string {
  const sections = SPEC_SECTIONS
    .map((s) => ({ ...s, value: (v[s.key] || "").trim() }))
    .filter((s) => s.value.length > 0);
  const rawText = (v.text || "").trim();
  const title = (v.title || "").trim() || rawText.split("\n")[0]?.trim() || "";
  if (sections.length === 0) {
    // No structured spec: use the plain text body, else just the title.
    return rawText || title;
  }
  const lines = [title || "Untitled task", ""];
  for (const s of sections) lines.push(`${s.label}: ${s.value}`);
  return lines.join("\n").trim();
}

// ONE path for filing a ticket from a chat.
//
// This used to POST /api/tasks directly while the toolbar's ticket dialog posted
// to the chat's spawn-task endpoint: two implementations of one verb, reporting
// differently, and only one of them recording where the ticket came from. A
// ticket filed from a card had no provenance back to the conversation that
// produced it, so the chat -> ticket link the rest of the system relies on was
// simply missing for half the tickets.
//
// spawn-task is the chat-aware one and now takes an explicit scope, so it serves
// both callers. `run` is part of the same request rather than a second round
// trip, which also removes the window where a ticket was filed and the run
// launch failed separately.
async function fileTicket(
  v: Record<string, string>,
  threadId: string,
  run: boolean,
): Promise<CreatedTask> {
  const r = await fetch(`/api/chat/threads/${encodeURIComponent(threadId)}/spawn-task`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      scope: (v.scope || "workshop").trim(),
      text: composeTaskText(v),
      priority: Number(v.priority || "0") || 0,
      effort: (v.effort || "").trim() || null,
      run,
    }),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as CreatedTask;
}

// GET /api/tasks/scopes carries a `pickup` map: per scope, whether a worker can
// be launched by hand and whether ANYTHING picks tickets up unattended. Best
// effort — a failed read falls back to the pure predicate in lib/scopePickup.
async function fetchPickupMap(): Promise<PickupMap | null> {
  try {
    const r = await fetch("/api/tasks/scopes");
    if (!r.ok) return null;
    const j = await r.json();
    return (j.pickup ?? null) as PickupMap | null;
  } catch {
    return null;
  }
}

// The follow-up buttons a filed ticket offers. `openTicket` goes to the ticket
// when the server told us its id, which is the only thing "Open ticket" can
// honestly mean; without an id it is a filtered list and says so.
function filedActions(taskId: string | null): UiCardAction[] {
  return taskId
    ? [{ id: "openTicket", label: "Open ticket", variant: "primary" }]
    : [{ id: "openWork", label: "Open in Work", variant: "primary" }];
}

const addTasks: UiCardHandler = async (state, ctx) => {
  const asked = (state.values.scope || "workshop").trim();
  if (!composeTaskText(state.values).trim()) {
    return { status: "error", message: "Add a ticket title first." };
  }
  if (!ctx.threadId) {
    return { status: "error", message: "This card is not attached to a chat yet." };
  }
  try {
    const j = await fileTicket(state.values, ctx.threadId, false);
    const decided = j.decision && j.decision !== "distinct" ? ` (${j.message ?? j.decision})` : "";
    // The scope the SERVER filed into — see addTasksAndRun.
    const scope = (j.scope || asked).trim();
    const taskId = j.task?.id || "";
    // A ticket filed to a project scope is picked up by nothing today. Saying
    // "Filed" and stopping there lets it read as queued when it is not.
    const warn = pickupNote(pickupFor(await fetchPickupMap(), scope));
    return {
      status: "success",
      note: `Filed a ticket in ${scope}${decided}.${warn ? ` ${warn}` : ""}`,
      values: { scope, __task_id: taskId ?? "" } as Record<string, string>,
      actions: filedActions(taskId || null),
    };
  } catch (e) {
    return { status: "error", message: String(e) };
  }
};

const addTasksAndRun: UiCardHandler = async (state, ctx) => {
  const asked = (state.values.scope || "workshop").trim();
  if (!composeTaskText(state.values).trim()) {
    return { status: "error", message: "Add a ticket title first." };
  }
  if (!ctx.threadId) {
    return { status: "error", message: "This card is not attached to a chat yet." };
  }
  // BEFORE the ticket is filed. `scope_is_runnable` exists precisely so the UI
  // can say this in advance (routes/tasks_all.py), and TicketDraftsDialog
  // already honours it; this path did not, so a general chat scope filed the
  // ticket and then reported the failure as a footnote on a success message.
  if (!scopeIsRunnable(asked)) {
    return {
      status: "error",
      message: `${asked} cannot run tickets. File it with "File ticket", or choose a scope that can run work.`,
    };
  }
  let created: CreatedTask;
  try {
    created = await fileTicket(state.values, ctx.threadId, true);
  } catch (e) {
    return { status: "error", message: String(e) };
  }
  if (!created.task?.id) {
    return { status: "error", message: "Ticket filed but no id was returned." };
  }
  // WHERE IT ACTUALLY WENT, not where the card asked for it to go. spawn-task
  // falls back to the chat's own context, then to `workshop`, whenever the
  // requested scope does not resolve — so the card could say "Filed and running
  // in <a scope the ticket is not in>" and then hand the operator a Work link
  // that does not contain it. The server returns the scope it used; that is the
  // only one worth repeating. (TicketDraftsDialog already reads it this way.)
  const scope = (created.scope || asked).trim();
  const taskId = created.task?.id ?? null;
  // A merge folded into an existing ticket, so the server deliberately does not
  // run it: re-running work that already exists would be a surprise.
  if (created.decision === "merge" || created.decision === "exact") {
    return {
      status: "success",
      note: `Merged into an existing ticket in ${scope}; did not start a new run.`,
      values: { scope, __task_id: taskId ?? "" } as Record<string, string>,
      actions: filedActions(taskId),
    };
  }
  if (created.run_error) {
    // The ticket IS filed; only the immediate run failed (commonly a busy tree).
    return {
      status: "success",
      note: `Filed the ticket in ${scope}, but could not run it now: ${created.run_error}`,
      values: { scope, __task_id: taskId ?? "" } as Record<string, string>,
      actions: filedActions(taskId),
    };
  }
  return {
    status: "success",
    note: `Filed and running in ${scope}.`,
    values: {
      scope,
      __task_id: taskId ?? "",
      __job_id: String(created.job_id || ""),
    } as Record<string, string>,
    actions: [
      { id: "viewRun", label: "View run", variant: "primary" },
      ...filedActions(taskId),
    ],
  };
};

const openWork: UiCardHandler = async (state, ctx) => {
  const scope = (state.values.scope || "").trim();
  ctx.navigate(scope ? `/work?scope=${encodeURIComponent(scope)}` : "/work");
  return { status: "noop" };
};

// The ticket, not the list it is somewhere inside.
const openTicket: UiCardHandler = async (state, ctx) => {
  const taskId = (state.values.__task_id || "").trim();
  const scope = (state.values.scope || "workshop").trim();
  ctx.navigate(taskId ? ticketHref(scope, taskId) : `/work?scope=${encodeURIComponent(scope)}`);
  return { status: "noop" };
};

const viewRun: UiCardHandler = async (state, ctx) => {
  const jid = (state.values.__job_id || "").trim();
  ctx.navigate(jid ? `/jobs/${encodeURIComponent(jid)}` : "/work");
  return { status: "noop" };
};

export const UI_CARD_ACTIONS: Record<string, UiCardHandler> = {
  createProject,
  openProject,
  moveChatToProject,
  addTasks,
  addTasksAndRun,
  openWork,
  openTicket,
  viewRun,
  dismiss,
};

export function hasCardHandler(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(UI_CARD_ACTIONS, id);
}
