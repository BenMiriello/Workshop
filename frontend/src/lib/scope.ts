// The single owner of the scope model on the frontend, matching
// `backend/services/scope_tree.py` (memory-and-documentation-model-2026-08-14.md
// sec 7.1). Before this file, scope parsing/labelling/ordering was split across
// `devMode.ts` (DEVELOPMENT_SCOPE, orderScopes) and `tasks.ts` (scopeLabel,
// scopeHref); both now call this module instead. Every export those two files
// carried keeps working unchanged, so no page or component needed to move.
//
// `src/components/chat/chatScope.ts` keys off a chat's `context_key`, which is
// a DIFFERENT namespace: it carries aliases this tree does not model (bare app
// ids, `general`). The translation between the two namespaces is
// `scopeForContext` below, and it is the only one; chatScope.ts calls it rather
// than carrying a second copy.
//
// The one name that resolves to the platform repo (ADR 0014). There used to be
// five.
export const DEVELOPMENT_SCOPE = "development";
export const WORKSHOP_SCOPE = "workshop";

// The bare context keys that mean "the root organising layer": `general` is the
// unbound chat, `new-project` the pre-creation draft chat, `workshop` names
// itself.
const ROOT_CONTEXT_KEYS = ["", "general", "workshop", "new-project"];

// context_key -> scope. The client half of `scope_tree.scope_for_context`
// (backend/services/scope_tree.py), which is the single owner of this mapping;
// the table there is authoritative and this must not grow a branch of its own.
//
// A bare key is an installed app id here, where the backend checks it against
// the catalog and falls back to `workshop`. The client only ever holds bare keys
// that came from the server's own provider list, so it has no junk to reject.
export function scopeForContext(contextKey: string | null | undefined): string {
  const ck = (contextKey ?? "general").trim();
  if (ck === DEVELOPMENT_SCOPE) return DEVELOPMENT_SCOPE;
  if (ROOT_CONTEXT_KEYS.includes(ck)) return WORKSHOP_SCOPE;
  const i = ck.indexOf(":");
  if (i < 0) return `app:${ck}`;
  const kind = ck.slice(0, i);
  const slug = ck.slice(i + 1);
  if (!slug) return WORKSHOP_SCOPE;
  if (kind === "project" || kind === "app" || kind === "agent") return ck;
  return WORKSHOP_SCOPE;
}

// The scopes a chat gets no task queue for. The workshop root HAS one
// (`shared/queue`), but a chat is not put in front of it just for being
// unbound: showing a general chat the root backlog would make every chat in
// the system look like it owned the same work. `chat_work._QUEUELESS_SCOPES`
// is the same rule on the server.
//
// An app scope is no longer a blanket exclusion (app-is-the-entity-2026-08-20.md
// sec 2.2): an app that owns a linked project tree owns a ticket queue now,
// same as `chat_work.task_scope_for` on the server. `linkedApps` is the set
// of app slugs currently linked (`lib/apps.ts`'s `linkedAppSlugs()`), which a
// caller loads once and passes in -- this stays a pure function and cannot
// fetch it itself. Absent/null means "not loaded yet", and the safe answer
// while unknown is the same one an unlinked app gets: no queue, so a chat
// never shows a Tickets affordance that 400s on `_require_root` the instant
// it is opened.
export function scopeHasQueue(scope: string, linkedApps?: ReadonlySet<string> | null): boolean {
  if (scope === WORKSHOP_SCOPE) return false;
  if (scope.startsWith("app:")) return !!linkedApps && linkedApps.has(scope.slice("app:".length));
  return true;
}

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
  // `role` is the SECOND AXIS, not a tree node (see backend scope_tree's "TWO
  // AXES, NOT ONE"). It labels like any other kind; it just sits at the root of
  // the scope picker because it has no parent.
  // `app` was MISSING here, so the Memory tree read "app: budget" beside
  // "Project: budget" and "Agent: sweeper": three capitalisations in one list,
  // and the one left out is the kind the platform is organised around
  // (app-is-the-entity-2026-08-20.md). `build` is gone rather than relabelled:
  // `build:<slug>` is a deleted spelling (see chatScope.ts and scope.test.ts's
  // "does not recognise the deleted `build:` kind"), so it labelled a kind that
  // can no longer reach this function.
  const kindLabel =
    kind === "app" ? "App"
    : kind === "project" ? "Project"
    : kind === "agent" ? "Agent"
    : kind === "role" ? "Role"
    : kind;
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

// The app a scope belongs to, or null when it belongs to none.
//
// `project:<slug>` and `app:<slug>` name the SAME slug in the two registries --
// App.tsx's /projects/<slug> redirect to /apps/<slug>/overview rests on it --
// so both kinds resolve here. `workshop`, `development`, `agent:` and `role:`
// have no app, and NULL IS THE ANSWER THAT MATTERS: the surfaces that lost
// their global run/change/deploy list (2026-09-01) use it to drop a link
// rather than invent a destination for a record that belongs to no app.
export function appSlugForScope(scope?: string): string | null {
  if (!scope || scope === DEVELOPMENT_SCOPE || scope === WORKSHOP_SCOPE) return null;
  const [kind, slug] = scope.split(":", 2);
  if (!slug) return null;
  return kind === "app" || kind === "project" ? slug : null;
}
