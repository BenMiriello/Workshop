// EVERY QUERY KEY IN THE PLATFORM, IN ONE PLACE.
//
// A query key is the cache's identity for a fact. Two readers of one endpoint
// share a cache entry, an in-flight request, and an invalidation only if they
// spell the key identically -- and TanStack types a key as `unknown[]`, so a
// key that is spelled differently is not a type error, not a lint error, and
// not a test failure. It is simply a second cache entry that nothing keeps in
// step with the first.
//
// That is not hypothetical. `GET /api/chat/threads` had two owners:
// `["chat", "threads", null]` (the dashboard's thread cards and the chat
// toolbar's recent-chats menu) and `["chat", "threads"]` (the Chats page).
// `useThreads.ts` even documented the convention -- "`["chat", "threads",
// <scope>]` is the one they are collapsing onto" -- and the Chats page, written
// afterwards, used the two-element spelling. The result was two polls of one
// URL at two cadences, and an archive on the Chats page that wrote through to
// its own copy while the dashboard kept listing the chat it had just archived.
//
// So keys are not written at call sites any more. They are constructed here,
// by a function per fact, and `check_frontend_state.py` fails a build that
// writes an array literal into a `queryKey` outside this file. A misspelling
// becomes a missing export, which is a compile error -- which is the only kind
// of error that actually stops this recurring.
//
// APPS DO NOT APPEAR HERE. A platform source file may not name a specific app
// (CLAUDE.md, `scripts/check_no_app_names.py`), so an app's keys live in the
// app's own directory beside its fetchers. `apps.runStatus(slug)` below is the
// platform's generic per-app service probe, which takes the slug as data; it
// does not know any app's name.

/** A key, once built. Readonly so a caller cannot mutate a shared tuple. */
export type QueryKey = readonly unknown[];

/**
 * A chat's scope filter. `null` means "every scope", which is what the
 * cross-context readers (the dashboard, the recent-chats menu, the Chats page)
 * ask for. It is an explicit slot rather than an absent one so that "all
 * scopes" and "some scope" are the same shape and cannot collide.
 */
export type ThreadScope = string | null;

export const queryKeys = {
  chat: {
    /** Everything under `chat`. The prefix an invalidation uses to reach it all. */
    all: () => ["chat"] as const,
    /**
     * `GET /api/chat/threads` -- the LIVE list. The scope slot is REQUIRED,
     * including the `null` that means every scope: an omitted slot is what made
     * the Chats page miss the dashboard's entry.
     *
     * `"live"` is a discriminant, not decoration. The same route also serves the
     * archive (`?archived=true`), which is a DIFFERENT list rather than a wider
     * one, so it needs its own entry -- and putting the archive's marker in the
     * scope slot would collide with a scope that happened to be called
     * "archived". A fixed discriminant ahead of the scope cannot collide, and
     * both lists still sit under the `["chat", "threads"]` prefix, so one
     * invalidation after a write reaches every copy of either.
     */
    threads: (scope: ThreadScope) => ["chat", "threads", "live", scope] as const,
    /** `GET /api/chat/threads?archived=true`. Its own list; see above. */
    threadsArchived: () => ["chat", "threads", "archived"] as const,
    /** The prefix over BOTH lists and every scope, for invalidating after a write. */
    threadsAll: () => ["chat", "threads"] as const,
    context: (threadId: string | null) => ["chat", "context", threadId] as const,
    work: (threadId: string | null) => ["chat", "work", threadId] as const,
    branches: (threadId: string | null) => ["chat", "branches", threadId] as const,
    /**
     * `GET /api/chat/threads/{id}/tree` -- which WORKING TREE this chat's turns
     * run in, and who holds its lease. Not `chat.branches`, which is the
     * conversation's own lines (ADR 0020): one is a git worktree and the other
     * is a fork of the transcript, and giving them one key would make switching
     * conversation branch invalidate the ref pill and vice versa.
     */
    tree: (threadId: string | null) => ["chat", "tree", threadId] as const,
    mentionable: (threadId: string | null) => ["chat", "mentionable", threadId] as const,
    prefs: () => ["chat", "prefs"] as const,
    /**
     * `GET /api/chat/providers`. THIS KEY CARRIES TRIGGERS, and that is
     * deliberate rather than an oversight: the read replaces an effect whose
     * dependency array was all six of these, it is the gate the thread resolver
     * waits on, and it runs with `staleTime: 0` / `gcTime: 0` so that returning
     * to a scope re-reads rather than resolving from a list minutes old.
     * Dropping a trigger would serve a cached list where the code this replaced
     * made a request. See the call site for the per-trigger reasoning.
     */
    providers: (t: {
      scope: string | null;
      mode: string;
      initialThreadId: string | null;
      startNew: boolean;
      nonce: number;
    }) => ["chat", "providers", t.scope, t.mode, t.initialThreadId, t.startNew, t.nonce] as const,
  },

  jobs: {
    all: () => ["jobs"] as const,
    feed: (filter: string) => ["jobs", "feed", filter] as const,
    running: (limit: number) => ["jobs", "running", limit] as const,
    detail: (id: string) => ["jobs", "detail", id] as const,
    children: (id: string) => ["jobs", "children", id] as const,
  },

  agents: {
    all: () => ["agents"] as const,
    list: () => ["agents", "list"] as const,
    maker: (agentId: string) => ["agents", agentId, "maker"] as const,
    autonomyStatus: () => ["agents", "autonomy", "status"] as const,
    autonomyControls: () => ["agents", "autonomy", "status", "controls"] as const,
  },

  tasks: {
    all: () => ["tasks"] as const,
    list: (scope: string) => ["tasks", scope] as const,
    detail: (scope: string, id: string) => ["tasks", scope, id, "detail"] as const,
    filteredList: (paramsStr: string) => ["tasks", "filtered-list", paramsStr] as const,
    scopes: () => ["tasks", "scopes"] as const,
    labels: (scope: string) => ["tasks", "labels", scope] as const,
    assignees: (scope: string) => ["tasks", "assignees", scope] as const,
  },

  usage: {
    all: () => ["usage"] as const,
    page: (window: string) => ["usage", "page", window] as const,
    /** The dashboard's rolling window tile, which is not the Usage page's read. */
    window: (window: string) => ["usage", window] as const,
  },

  queue: {
    all: () => ["queue"] as const,
    runnerTargets: () => ["queue", "runner", "targets"] as const,
    runnerDir: (scope: string) => ["queue", "runner", "dir", scope] as const,
  },

  projects: {
    autonomy: (slug: string) => ["projects", slug, "autonomy"] as const,
  },

  /**
   * The platform's own per-app reads. The slug is DATA -- it arrives from the
   * app registry at runtime -- so this names no app.
   */
  apps: {
    runStatus: (slug: string) => ["apps", slug, "run", "status"] as const,
    /**
     * The launcher's status LINES, as one fact: `appStatuses()` in
     * `src/apps/registry.tsx` fans out to whatever the installed apps declare
     * and returns a record keyed by app id. No slug slot, because the read
     * takes no argument -- which app it asked is the registry's business, not
     * the cache's.
     */
    launcherStatuses: () => ["apps", "launcher", "statuses"] as const,
    chats: (appId: string) => ["apps", appId, "chats"] as const,
    agents: (appId: string) => ["apps", appId, "agents"] as const,
    tickets: (appId: string) => ["apps", appId, "tickets"] as const,
    runs: (appId: string) => ["apps", appId, "runs"] as const,
    deploys: (appId: string) => ["apps", appId, "deploys"] as const,
    notes: (appId: string) => ["apps", appId, "notes"] as const,
  },

  presence: {
    directory: (subjectKind: string | null) => ["presence", "directory", subjectKind] as const,
    /**
     * `addr` is the query string the read was addressed with, so the app-scoped
     * and project-scoped reads cannot be served each other's answer. The
     * Branches page asks for every tree at once and passes the literal `"all"`,
     * which is a real address rather than an omitted slot: "every branch" and
     * "this app's branches" are different questions with different answers, and
     * an empty string already means "this caller has no address and expects
     * nothing".
     */
    branches: (addr: string) => ["branches", addr] as const,
    control: (sessionId: string) => ["control", sessionId] as const,
  },

  /**
   * The lease table itself, which is NOT `presence.directory`. The directory
   * joins outward from live presence and so cannot produce a lease whose holder
   * has stopped beating; this read starts from the leases and can. Two
   * different facts, two different keys.
   */
  leases: {
    all: () => ["leases"] as const,
    list: (subjectKind: string | null) => ["leases", subjectKind] as const,
  },

  /**
   * Reads about one working tree on disk, keyed by its absolute path because
   * that is what the route is addressed by. `treeState` is the dirty/clean
   * answer plus how far the branch has drifted from its base, which is two
   * endpoints behind one key: a caller wants both or neither, and splitting
   * them would double the rows in flight to say one thing.
   */
  git: {
    treeState: (repo: string, base: string) => ["git", "tree-state", repo, base] as const,
  },

  inbox: {
    list: (limit: number) => ["inbox", limit] as const,
  },
} as const;
