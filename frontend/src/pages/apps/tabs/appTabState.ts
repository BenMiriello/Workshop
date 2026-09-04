// Pure rules behind the per-app page column's tabs (app-is-the-entity-2026-08-20.md
// sec 9 stage C). Extracted so the "why is this empty" decision is a rule that
// can be read and tested, not a paragraph of JSX.
//
// THE TAB LIST IS PER-APP NOW. It used to be one fixed order for every app, on
// the reasoning that the backend serves all seven nested collections for all of
// them (services/apps.py). It does, but five of the seven can only ever be
// EMPTY for an app with no linked project tree, and `unlinkedReason` below was
// already computing exactly that -- and spending the answer on the wording of
// an empty state, inside a tab that should not have been drawn.
//
// So the same predicate decides both now (`canHaveContent`), and an app on top
// of that DECLARES the tabs it wants and the surface it opens on
// (backend/services/app_surface.py). Reported by the operator at least four
// times over several chats, in these words: an app opens to "a generic chat
// list with seven tabs" including "a Memory tab nobody asked for". Three
// previous rounds improved how those tabs RENDER, which was never the
// complaint.
export type AppTabId = "app" | "work" | "overview" | "chats" | "agents" | "memory" | "files" | "tickets" | "runs";

export type AppTabDef = { id: Exclude<AppTabId, "app">; label: string; suffix: string };

// FOUR TABS, AND WORK IS FIRST.
//
// It was seven (chats, agents, memory, files, tickets, runs, deploys) until
// 2026-09-01. threads-branches-and-pages-2026-08-29.md section 6 makes Work the
// app-level view and has it absorb tickets, runs and changes: "A
// branch is a row. Collapsed it shows the name, the ticket, and the holder."
// Section 1 retires `agents` as a kind of object entirely -- an agent is a
// thread that is armed, so it is a filter on Chats, not a tab.
//
// An app opens on the FIRST tab it can have (backend/services/app_surface.py,
// `_resolve_home`), so putting Work first is also what stops an app with a
// working tree from opening on a chat list. That is the 2026-08-29 / 08-31 /
// 09-01 report, three rounds of which changed how the chat list renders rather
// than where the app lands.
//
// The retired ids stay in `AppTabId` and keep their routes: a bookmark and a
// deep link are still real. What changed is which tabs are OFFERED.
//
// `deploys` is the exception and is GONE from every list below rather than
// retired into one: the deploy subsystem was deleted on 2026-09-04 (ticket
// `726d75a9`), so there is no route left to keep and no collection left to
// explain the emptiness of.
export const APP_TABS: AppTabDef[] = [
  { id: "work", label: "Work", suffix: "/work" },
  // SECOND, not first, though section 6 lists Overview first. An app opens on
  // its FIRST tab, and what the operator wants an app to open on is the work
  // (reported 2026-08-29, 08-31 and twice on 09-01). Overview is where the
  // autonomy brake, the services and the environment live now that /projects
  // is gone, and none of those is a thing you open an app to look at.
  { id: "overview", label: "Overview", suffix: "/overview" },
  { id: "chats", label: "Chats", suffix: "/chats" },
  { id: "memory", label: "Memory", suffix: "/memory" },
  { id: "files", label: "Files", suffix: "/files" },
];

// Every tab that draws a COLLECTION and can therefore be empty for a reason
// worth stating. Wider than `APP_TABS` on purpose: tickets, runs and agents
// are no longer offered as tabs but their routes still resolve, and a deep
// link that lands on one must still say why it is empty rather than look
// broken.
export type CollectionTabId =
  | "work" | "overview" | "chats" | "memory" | "tickets" | "runs" | "agents";

// Mirrors services/apps.py collection by collection: tickets/runs/agents
// resolve through `app_links.linked_project_slug`; chats and notes
// (memory) resolve through the app's slug alone and need no link. Files is a
// separate question -- an app may declare its own root with a manifest instead
// of a project link (services/app_files.py) -- so it is carried by `hasFiles`
// rather than derived here.
const REQUIRES_LINKED_PROJECT: Record<CollectionTabId, boolean> = {
  // Work is branches, tickets, runs and changes. Every one of those resolves
  // through the linked tree, so an app without one can never fill it.
  work: true,
  // Every panel on Overview reads /api/projects/<slug>/..., so an app with no
  // linked tree can have none of them.
  overview: true,
  chats: false,
  memory: false,
  tickets: true,
  runs: true,
  agents: true,
};

const COLLECTION_NOUN: Record<CollectionTabId, string> = {
  work: "branches",
  // Overview is four controls over a working tree, not a collection, so it
  // never reaches `unlinkedReason`. The entry exists because the record is
  // exhaustive over CollectionTabId; the page writes its own sentence.
  overview: "settings",
  chats: "chats",
  memory: "notes",
  tickets: "tickets",
  runs: "runs",
  agents: "agents",
};

/** What the platform knows about one app's ability to fill a tab. Both fields
 *  come off the app's resolved surface (GET /api/base-apps/launcher), which is
 *  where the backend answers them once for the launcher and the dock together. */
export type AppTabFacts = {
  /** The app's linked working tree, or null when it has none. */
  projectSlug: string | null;
  /** Whether the app has a file root at all (a declared one or a linked tree). */
  hasFiles: boolean;
};

/**
 * Whether this tab could ever show something for this app.
 *
 * AVAILABILITY, NOT POPULATION. A linked app with no tickets yet keeps its
 * Tickets tab, because the next ticket filed against it lands there and a tab
 * that comes and goes with the row count is a nav that moves under you. What
 * this drops is the tab that can never be anything but empty.
 */
export function canHaveContent(tab: Exclude<AppTabId, "app">, facts: AppTabFacts): boolean {
  if (tab === "files") return facts.hasFiles;
  return !REQUIRES_LINKED_PROJECT[tab] || !!facts.projectSlug;
}

/**
 * The tabs one app actually gets, in order.
 *
 * `declared` is the app's own list off its surface declaration, or null when it
 * makes none. Declaring is narrowing plus ordering: a tab the app names but
 * cannot hold is still dropped, and a tab it does not name is not offered.
 * Declaring nothing means every tab it can have, in the platform's order.
 */
export function tabsForApp(
  declared: readonly string[] | null | undefined,
  facts: AppTabFacts,
): AppTabDef[] {
  const available = APP_TABS.filter((t) => canHaveContent(t.id, facts));
  if (!declared) return available;
  const byId = new Map(available.map((t) => [t.id as string, t]));
  const out: AppTabDef[] = [];
  for (const id of declared) {
    const tab = byId.get(id);
    // Each tab at most once: a declaration that repeats one must not draw two
    // rows to the same route.
    if (tab && !out.includes(tab)) out.push(tab);
  }
  return out;
}

/**
 * Why a collection tab is showing nothing, or null when the ordinary "nothing
 * here yet" empty state applies (or the list is not empty at all).
 *
 * Still reachable on a tab the rule above would not draw, because a route is
 * still a route: a deep link or a stale bookmark lands on the page directly,
 * and it must say why it is empty rather than look broken.
 *
 * `projectSlug` is the app's own `project_slug` field, straight off GET
 * /api/apps/{id} -- null means "no linked project tree", exactly the case
 * services/apps.py returns an honest empty list for rather than a 404 or
 * another app's rows.
 */
export function unlinkedReason(
  tab: CollectionTabId,
  projectSlug: string | null | undefined,
  count: number,
): string | null {
  if (count > 0) return null;
  if (canHaveContent(tab, { projectSlug: projectSlug ?? null, hasFiles: false })) return null;
  return `This app has no linked project tree, so it has no ${COLLECTION_NOUN[tab]} yet.`;
}
