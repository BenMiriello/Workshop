// Pure rules behind the per-app page column's tabs (app-is-the-entity-2026-08-20.md
// sec 9 stage C). Extracted so the "why is this empty" decision is a rule that
// can be read and tested, not a paragraph of JSX.
//
// The tab list itself is one fixed order for every app -- there is no per-app
// derivation, because the backend gives every app the same seven nested
// collections whether or not it has anything in them (services/apps.py). What
// DOES vary per app is whether five of those seven can ever be non-empty: they
// resolve through the app's linked project tree (services/app_links.py), and
// an installed-only app with no project genuinely has none. Chats and Memory
// are the two exceptions -- they work off the app's slug alone, never a
// project link, so they are never "empty because unlinked".
export type AppTabId = "app" | "chats" | "agents" | "memory" | "files" | "tickets" | "runs" | "deploys";

export type AppTabDef = { id: Exclude<AppTabId, "app">; label: string; suffix: string };

// Suffix order matches the operator's own list, verbatim: "chats agents
// memory files and everything ... tickets runs deploys". This is the one
// place that order is written down; the page column and the route table both
// read it from here rather than each spelling it out again.
export const APP_TABS: AppTabDef[] = [
  { id: "chats", label: "Chats", suffix: "/chats" },
  { id: "agents", label: "Agents", suffix: "/agents" },
  { id: "memory", label: "Memory", suffix: "/memory" },
  { id: "files", label: "Files", suffix: "/files" },
  { id: "tickets", label: "Tickets", suffix: "/tickets" },
  { id: "runs", label: "Runs", suffix: "/runs" },
  { id: "deploys", label: "Deploys", suffix: "/deploys" },
];

export type CollectionTabId = "chats" | "memory" | "tickets" | "runs" | "deploys" | "agents";

// Mirrors services/apps.py collection by collection: tickets/runs/deploys/
// agents/files resolve through `app_links.linked_project_slug`; chats and
// notes (memory) resolve through the app's slug alone and need no link.
const REQUIRES_LINKED_PROJECT: Record<CollectionTabId, boolean> = {
  chats: false,
  memory: false,
  tickets: true,
  runs: true,
  deploys: true,
  agents: true,
};

const COLLECTION_NOUN: Record<CollectionTabId, string> = {
  chats: "chats",
  memory: "notes",
  tickets: "tickets",
  runs: "runs",
  deploys: "deploys",
  agents: "agents",
};

/**
 * Why a collection tab is showing nothing, or null when the ordinary "nothing
 * here yet" empty state applies (or the list is not empty at all).
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
  if (!REQUIRES_LINKED_PROJECT[tab]) return null;
  if (projectSlug) return null;
  return `This app has no linked project tree, so it has no ${COLLECTION_NOUN[tab]} yet.`;
}
