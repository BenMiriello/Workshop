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

type CreatedTask = {
  task?: { id?: string; text?: string } | null;
  decision?: string;
  message?: string;
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

async function fileTicket(v: Record<string, string>): Promise<CreatedTask> {
  const scope = (v.scope || "workshop").trim();
  const text = composeTaskText(v);
  const priority = Number(v.priority || "0") || 0;
  const effort = (v.effort || "").trim();
  const r = await fetch("/api/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope, text, priority, effort: effort || null }),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return (await r.json()) as CreatedTask;
}

const addTasks: UiCardHandler = async (state) => {
  const scope = (state.values.scope || "workshop").trim();
  if (!composeTaskText(state.values).trim()) {
    return { status: "error", message: "Add a ticket title first." };
  }
  try {
    const j = await fileTicket(state.values);
    const decided = j.decision && j.decision !== "distinct" ? ` (${j.message ?? j.decision})` : "";
    return {
      status: "success",
      note: `Filed a ticket in ${scope}${decided}.`,
      values: { scope } as Record<string, string>,
      actions: [{ id: "openWork", label: "Open in Work", variant: "primary" }],
    };
  } catch (e) {
    return { status: "error", message: String(e) };
  }
};

const addTasksAndRun: UiCardHandler = async (state) => {
  const scope = (state.values.scope || "workshop").trim();
  if (!composeTaskText(state.values).trim()) {
    return { status: "error", message: "Add a ticket title first." };
  }
  let created: CreatedTask;
  try {
    created = await fileTicket(state.values);
  } catch (e) {
    return { status: "error", message: String(e) };
  }
  const taskId = created.task?.id;
  if (!taskId) {
    return { status: "error", message: "Ticket filed but no id was returned." };
  }
  if (created.decision === "merge" || created.decision === "exact") {
    return {
      status: "success",
      note: `Merged into an existing ticket in ${scope}; did not start a new run.`,
      values: { scope } as Record<string, string>,
      actions: [{ id: "openWork", label: "Open in Work", variant: "primary" }],
    };
  }
  const rr = await fetch(
    `/api/tasks/${encodeURIComponent(scope)}/${encodeURIComponent(taskId)}/run`,
    { method: "POST" },
  );
  if (!rr.ok) {
    let detail = `${rr.status}`;
    try {
      const e = await rr.json();
      detail = e.detail || detail;
    } catch {
      /* non-JSON body */
    }
    // The ticket IS filed; only the immediate run failed (commonly a busy tree).
    return {
      status: "success",
      note: `Filed the ticket in ${scope}, but could not run it now: ${detail}`,
      values: { scope } as Record<string, string>,
      actions: [{ id: "openWork", label: "Open in Work", variant: "primary" }],
    };
  }
  const run = await rr.json();
  return {
    status: "success",
    note: `Filed and running in ${scope}.`,
    values: { scope, __job_id: String(run.job_id || "") } as Record<string, string>,
    actions: [
      { id: "viewRun", label: "View run", variant: "primary" },
      { id: "openWork", label: "Open in Work" },
    ],
  };
};

const openWork: UiCardHandler = async (state, ctx) => {
  const scope = (state.values.scope || "").trim();
  ctx.navigate(scope ? `/work?scope=${encodeURIComponent(scope)}` : "/work");
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
  viewRun,
  dismiss,
};

export function hasCardHandler(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(UI_CARD_ACTIONS, id);
}
