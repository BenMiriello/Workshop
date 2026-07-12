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

// ---- Wire shape (matches the A.1 contract) ---------------------------------
export type UiCardFieldType =
  | "text"
  | "slug"
  | "textarea"
  | "template"
  | "sensitivity"
  | "select";

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
export const KNOWN_KINDS = new Set<string>(["project-proposal"]);

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
  | { status: "success"; slug?: string; title?: string; note?: string; actions?: UiCardAction[] }
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

export const UI_CARD_ACTIONS: Record<string, UiCardHandler> = {
  createProject,
  openProject,
  moveChatToProject,
  dismiss,
};

export function hasCardHandler(id: string): boolean {
  return Object.prototype.hasOwnProperty.call(UI_CARD_ACTIONS, id);
}
