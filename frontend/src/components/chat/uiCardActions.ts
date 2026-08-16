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
import { scopeIsRunnable } from "../../lib/scopePickup";
import { scopeLabel, ticketHref } from "../../lib/tasks";
import { runHref } from "../../lib/jobs";
import { shortHttpError } from "../../lib/httpError";

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
};

// Kinds the client knows how to render as a live, interactive card. Anything
// else falls back to an inert (read-only, no-button) card.
//
// `job-status` was the third, and it is gone. The server stopped appending that
// card in C12 (2026-07-18) and nothing has emitted one since, so the poller
// behind it had no card to poll for; the live run now shows on the ticket card
// the filing produced, which is where the operator was already looking. A
// `job-status` block in an archived thread renders as the inert fallback, which
// is what an unknown kind is for.
export const KNOWN_KINDS = new Set<string>([
  "project-proposal",
  "task-proposal",
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
      /** The identifier of the thing this card created: a project's slug, or a
       *  ticket's id. One of the three fields the card-state sidecar keeps, so
       *  it is also the only way a card still knows its subject after a reload. */
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
  return { status: "error", message: shortHttpError(r.status, r.statusText, await r.text().catch(() => "")) };
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
export const SPEC_SECTIONS: { key: keyof TaskProposalSpec; label: string }[] = [
  { key: "objective", label: "Objective" },
  { key: "output", label: "Output" },
  { key: "entry_points", label: "Start from" },
  { key: "boundaries", label: "Boundaries" },
];

// Compose a card's editable values into the filed ticket text: the title as the
// first line, then the description under it. The store splits that pair back
// into its `title` and `body` fields, which is what a ticket has been since
// 010a9a4.
//
// Three input shapes, one output, because a transcript is permanent. The card
// emits `description` today. Proposals already on disk carry either one field
// per spec section (composed in ORDER, exactly as before) or a plain `text`
// body whose own first line was the title. All three still file, and file the
// same way they did when they were written.
export function composeTaskText(v: Record<string, string>): string {
  const sections = SPEC_SECTIONS
    .map((s) => ({ ...s, value: (v[s.key] || "").trim() }))
    .filter((s) => s.value.length > 0);
  const rawText = (v.text || "").trim();
  const description = (v.description || "").trim();
  const title = (v.title || "").trim() || rawText.split("\n")[0]?.trim() || "";
  if (sections.length === 0) {
    if (!description) return rawText || title; // legacy `text`, else a bare title
    if (!title) return description;
    // A description that already opens with the title must not repeat it: the
    // store would read the second line as the body's first line either way, but
    // the ticket would show its own name twice (3d09386).
    const first = description.split("\n")[0]?.trim() ?? "";
    if (first === title) return description;
    return `${title}\n\n${description}`;
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
  if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  return (await r.json()) as CreatedTask;
}

// The only follow-up a filed ticket still needs from this registry: the case
// where the server filed something and told us no id. The card cannot track a
// ticket it cannot name, so it falls back to the old heading plus this one
// button, and the label says honestly that the destination is a list.
const filedActions: UiCardAction[] = [
  { id: "openWork", label: "Open in Work", variant: "primary" },
];

// THE CARD'S DURABLE HANDLE ON WHAT IT FILED, and why it is spelled `slug`.
//
// A filed card has to still be about its ticket after a reload, and the
// card-state sidecar the server keeps has exactly three string slots: `slug`,
// `title` and `note` (routes/chat.CardState). `slug` means "the identifier of
// the thing this card created": a project's slug on a project card, a ticket's
// id here. So the id rides there and `TicketCard` reads it back. Before the
// first reload the same id is in `values.__task_id`, and the card takes
// whichever it has.

/** The filed ticket's title, for a card whose store has not answered yet. */
function filedTitle(v: Record<string, string>): string {
  return (v.title || "").trim() || composeTaskText(v).split("\n")[0]?.trim() || "";
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
    // The scope the SERVER filed into — see addTasksAndRun.
    const scope = (j.scope || asked).trim();
    const taskId = j.task?.id || "";
    const merged = j.decision === "merge" || j.decision === "exact";
    return {
      status: "success",
      slug: taskId || undefined,
      title: filedTitle(state.values),
      // ONLY WHAT THE TICKET CANNOT SAY FOR ITSELF. The card names the ticket,
      // its scope and its key, so a note repeating "Filed a ticket in X" under
      // it is the duplication this pass exists to remove. A merge is a fact
      // about the FILING rather than about the surviving ticket, so it stays.
      // The pickup warning left too: the card reads it live from the scope map
      // instead of freezing the answer at the moment of filing.
      note: merged ? `Merged into an existing ticket in ${scopeLabel(scope)}.` : undefined,
      values: { scope, __task_id: taskId } as Record<string, string>,
      actions: taskId ? undefined : filedActions,
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
  const taskId = created.task?.id ?? "";
  // A merge folded into an existing ticket, so the server deliberately does not
  // run it: re-running work that already exists would be a surprise.
  const merged = created.decision === "merge" || created.decision === "exact";
  return {
    status: "success",
    slug: taskId || undefined,
    title: filedTitle(state.values),
    // The three sentences the card cannot re-derive from the ticket, and
    // nothing else. "Filed and running" is not one of them: the card's Run
    // group says it, live, and says it correctly a minute later too.
    note: merged
      ? `Merged into an existing ticket in ${scopeLabel(scope)}. It did not start a new run.`
      : created.run_error
        // The ticket IS filed; only the immediate run failed (commonly a busy tree).
        ? `Could not run it now: ${created.run_error}`
        : undefined,
    values: {
      scope,
      __task_id: taskId,
      __job_id: String(created.job_id || ""),
    } as Record<string, string>,
    actions: taskId ? undefined : filedActions,
  };
};

const openWork: UiCardHandler = async (state, ctx) => {
  const scope = (state.values.scope || "").trim();
  ctx.navigate(scope ? `/work?scope=${encodeURIComponent(scope)}` : "/work");
  return { status: "noop" };
};

// The ticket, not the list it is somewhere inside. `slug` is the same id after
// a reload has emptied `__task_id`; see `filedTitle` above for why it lives
// there.
const openTicket: UiCardHandler = async (state, ctx) => {
  const taskId = (state.values.__task_id || state.values.slug || "").trim();
  const scope = (state.values.scope || "workshop").trim();
  ctx.navigate(taskId ? ticketHref(scope, taskId) : `/work?scope=${encodeURIComponent(scope)}`);
  return { status: "noop" };
};

// `runHref`, not a hand-spelled path. This said `/jobs/<id>` while the deleted
// JobStatusCard said `/runs/<id>`: two spellings of one destination, both
// working through a redirect, and a reader could not tell whether the
// difference meant anything. It does not.
const viewRun: UiCardHandler = async (state, ctx) => {
  const jid = (state.values.__job_id || "").trim();
  ctx.navigate(jid ? runHref(jid) : "/work");
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
