// The ticket editor's draft, and the PATCH body it produces.
//
// The detail page edits a ticket as a set of strings (that is what an <input>
// holds) and the store wants typed fields with three different meanings for
// "empty": `null` = leave unchanged, `""` = clear the value, `[]` = clear the
// list. Getting that mapping wrong is silent — a field the operator cleared
// comes back on the next load — so it lives here as a pure function with tests
// rather than inline in the page.
//
// The contract mirrored here is backend/routes/tasks_all.py `TaskPatchIn` +
// services/tasks.py `update()`:
//   - every field is optional; an ABSENT key leaves the stored value alone
//   - effort "" clears the hint; assignee "" clears the assignment
//   - execution "" clears the classification back to "not classified"
//   - acceptance {kind: ""} clears the gate; a real kind is one of three
//   - labels / depends_on / relates_to take a whole list, and [] clears it
//   - max_attempts takes an int and has NO clear path, so a blank input leaves
//     the stored cap alone. 0 is stored and read as "no per-ticket cap"
//     (tasks.py: `cap = t.get("max_attempts") or attempt_cap`).
//
// Only CHANGED fields are sent. That keeps a title-only edit from re-pushing
// labels and body to a linked tracker (the PATCH route write-throughs on those
// three), and it keeps `assignee` out of a patch that is not about assignment.
import {
  cleanTitle, description as descriptionOf, title as titleOf,
  TASK_EXECUTIONS, type Task,
} from "./tasks";

// The store's execution vocabulary. Re-exported from lib/tasks so the editor and
// every reader name the same closed set (backend services/tasks.py).
export const TICKET_EXECUTIONS = TASK_EXECUTIONS;

// The effort vocabulary is TASK_EFFORTS and the acceptance kinds are
// TASK_ACCEPTANCE_KINDS, both in lib/tasks. They were restated here verbatim
// until 2026-08-17; import them from there instead of re-adding a copy.

export type TicketDraft = {
  title: string;
  body: string;
  status: string;
  priority: string;
  // "" = no effort hint.
  effort: string;
  // "" = not classified. Otherwise one of TICKET_EXECUTIONS. Part of the spec a
  // worker executes, so it commits on Save with the title and the body.
  execution: string;
  // "" = unassigned. Otherwise "operator" or "agent:<slug>".
  assignee: string;
  // "" = no completion gate.
  acceptanceKind: string;
  // Only carries meaning for the "command" kind.
  acceptanceSpec: string;
  // "" = leave the stored cap alone (the store cannot clear one).
  maxAttempts: string;
  // Comma separated.
  labels: string;
  dependsOn: string;
  relatesTo: string;
};

// The draft of a ticket that has not loaded yet. Editing is not offered until
// one has, so this is only ever the initial state of the form.
export const EMPTY_TICKET_DRAFT: TicketDraft = {
  title: "", body: "", status: "todo",
  priority: "0", effort: "", execution: "", assignee: "", acceptanceKind: "",
  acceptanceSpec: "", maxAttempts: "", labels: "", dependsOn: "", relatesTo: "",
};

export function draftFromTask(t: Task): TicketDraft {
  return {
    // Through the accessors, so a payload cached before title/body were stored
    // fields still opens with its text in the right two boxes.
    title: titleOf(t),
    body: descriptionOf(t),
    status: t.status ?? "todo",
    priority: String(t.priority ?? 0),
    effort: t.effort ?? "",
    execution: t.execution ?? "",
    assignee: t.assignee ?? "",
    acceptanceKind: t.acceptance?.kind ?? "",
    acceptanceSpec: t.acceptance?.spec ?? "",
    maxAttempts: t.max_attempts != null ? String(t.max_attempts) : "",
    labels: (t.labels ?? []).join(", "),
    dependsOn: (t.depends_on ?? []).join(", "),
    relatesTo: (t.relates_to ?? []).join(", "),
  };
}

// A comma (or newline) separated list, trimmed, empties dropped, order kept,
// duplicates dropped. Used for ids and for labels: the store dedupes labels on
// write anyway, and a repeated dependency id is never meaningful.
export function parseList(raw: string): string[] {
  const out: string[] = [];
  for (const part of (raw ?? "").split(/[,\n]/)) {
    const v = part.trim();
    if (v && !out.includes(v)) out.push(v);
  }
  return out;
}

// An integer, or null when the input is blank or not a number. A blank number
// input must not read as 0: for max_attempts that would be a real edit.
export function parseCount(raw: string): number | null {
  const v = (raw ?? "").trim();
  if (!v) return null;
  const n = Number(v);
  if (!Number.isFinite(n)) return null;
  return Math.trunc(n);
}

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

// The spec the store will hold for a given kind. Only "command" carries one, so
// text typed against "command" and then switched to "reviewer" must not count
// as a change to the gate.
function specFor(kind: string, spec: string): string {
  return kind === "command" ? (spec ?? "").trim() : "";
}

// The PATCH body for the difference between two drafts. Empty object = nothing
// changed (the route answers 400 "no fields to update" for that, which is why
// Save is disabled when this is empty).
export function ticketPatch(draft: TicketDraft, base: TicketDraft): Record<string, unknown> {
  const out: Record<string, unknown> = {};

  const title = cleanTitle(draft.title);
  if (title !== cleanTitle(base.title)) out.title = title;
  if (draft.body !== base.body) out.body = draft.body;
  if (draft.status !== base.status) out.status = draft.status;

  const priority = parseCount(draft.priority) ?? 0;
  if (priority !== (parseCount(base.priority) ?? 0)) out.priority = priority;

  if (draft.effort !== base.effort) out.effort = draft.effort;

  // "" clears the classification back to null, the same way "" clears effort.
  // null is not `code`: an unclassified ticket is never picked up unattended.
  if (draft.execution !== base.execution) out.execution = draft.execution;

  const assignee = (draft.assignee ?? "").trim();
  if (assignee !== (base.assignee ?? "").trim()) out.assignee = assignee;

  const kind = draft.acceptanceKind;
  const spec = specFor(kind, draft.acceptanceSpec);
  if (kind !== base.acceptanceKind || spec !== specFor(base.acceptanceKind, base.acceptanceSpec)) {
    out.acceptance = { kind, spec: kind === "command" ? spec || null : null };
  }

  // Blank leaves the stored cap alone: the store has no clear path for it.
  const cap = parseCount(draft.maxAttempts);
  if (cap !== null && cap !== parseCount(base.maxAttempts)) out.max_attempts = Math.max(0, cap);

  const labels = parseList(draft.labels);
  if (!sameList(labels, parseList(base.labels))) out.labels = labels;

  const dependsOn = parseList(draft.dependsOn);
  if (!sameList(dependsOn, parseList(base.dependsOn))) out.depends_on = dependsOn;

  const relatesTo = parseList(draft.relatesTo);
  if (!sameList(relatesTo, parseList(base.relatesTo))) out.relates_to = relatesTo;

  return out;
}

export function ticketDirty(draft: TicketDraft, base: TicketDraft): boolean {
  return Object.keys(ticketPatch(draft, base)).length > 0;
}

// The ids on the record that the server could not resolve to a real ticket, for
// ONE edge list. The detail endpoint returns `links.missing` for the whole
// record, which cannot say whether a dead id was a dependency or a soft link, so
// each editor works its own list out by difference.
export function unresolvedIds(
  ids: string[] | undefined, resolved: { id: string }[] | undefined,
): string[] {
  const known = new Set((resolved ?? []).map((l) => l.id));
  return (ids ?? []).filter((id) => !known.has(id));
}

// A ticket must keep a title: the route answers 400 "title required" for a blank
// one, and an untitled ticket is unreadable in every list that shows it.
export function ticketTitleMissing(draft: TicketDraft): boolean {
  return cleanTitle(draft.title) === "";
}

