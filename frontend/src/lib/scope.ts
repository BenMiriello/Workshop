// The single owner of the scope model on the frontend, matching
// `backend/services/scope_tree.py` (memory-and-documentation-model-2026-08-14.md
// sec 7.1). Before this file, scope parsing/labelling/ordering was split across
// `devMode.ts` (DEVELOPMENT_SCOPE, orderScopes) and `tasks.ts` (scopeLabel,
// scopeHref); both now call this module instead. Every export those two files
// carried keeps working unchanged, so no page or component needed to move.
//
// This does NOT cover `src/components/chat/chatScope.ts`. That module keys off
// a chat's `context_key` (which includes aliases this tree does not model, like
// bare app ids and `general`), and its own docstring already says "unifying
// them means settling the context_key/scope axis, which is a migration, not a
// rename. Do not add a third naming function in the meantime." Phase 1 leaves
// it alone on purpose.
//
// The one name that resolves to the platform repo (ADR 0014). There used to be
// five.
export const DEVELOPMENT_SCOPE = "development";

// Scope keys that exist only in a developer build. Empty otherwise -- the
// scope is absent, not present-and-disabled, because a disabled control still
// tells every user about a capability they do not have and cannot get.
export function devScopes(devMode: boolean): string[] {
  return devMode ? [DEVELOPMENT_SCOPE] : [];
}

// Order a list of scope keys for a picker, and drop dev-only scopes from a
// non-dev build.
//
// Development sorts ABOVE Workshop. Workshop is the widest scope and normally
// leads, but Development is where a developer already is, and putting the root
// organising layer above it would make the common case the second entry. The
// server orders `GET /api/tasks/scopes` the same way (`_scope_roots`); this is
// the client-side half of the same rule, applied to whatever the server sent so
// a non-dev bundle talking to a dev backend still shows a non-dev list.
export function orderScopes(scopes: string[], devMode: boolean): string[] {
  const visible = devMode ? scopes : scopes.filter((s) => s !== DEVELOPMENT_SCOPE);
  const rank = (s: string) => (s === DEVELOPMENT_SCOPE ? 0 : s === "workshop" ? 1 : 2);
  return [...visible].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
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
