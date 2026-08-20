// WHAT THE DOCK POINTS AT, and which page of it you are on. The rule, not the
// render.
//
// Extracted so it can be executed: this repo has no DOM harness and is not
// getting one, so the target of a test is always the pure rule behind a
// behaviour (`transcriptBlocks`, `workRows`, `branchable`). Everything the
// shell decides that is not layout lives here or in `shellState.ts`.
//
// A PLACE IS NOT AN APP. A place is anything the dock can point at (Home, All
// chats, an installed app); an app is one installed feature module.
//
// THE DOCK'S UNIT IS AN APP, and that question is now settled: "project" is
// retired as an entity and is the build view of an app, so there is no second
// list for the dock to hold. See
// docs/platform-redesign/app-is-the-entity-2026-08-20.md.
//
// The MIGRATION behind that decision is not built. Until it is, apps and
// projects are still two unlinked registries and every collection worth a page
// (tickets, runs, deploys, agents, repo) hangs off the PROJECT half while the
// dock lists the APP half. That is the whole reason an app gets one row below
// and Home gets nine. It is a data-model gap, not a nav decision, and it closes
// in stage C of that doc's §9 — this file is where it lands.
//
// EVERY PAGE HERE RESOLVES TO A ROUTE THAT EXISTS. There is no Files page, no
// per-app Chats/Memory/Deploys and no Activity entry, because none of those has
// anything behind it today (design doc section 8). A row that navigates
// nowhere is worse than a missing row: it teaches the operator the nav lies.

import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";

export type PlaceKind = "home" | "chats" | "app" | "settings";

export type ShellPage = {
  id: string;
  label: string;
  to: string;
  Icon: ComponentType<LucideProps>;
  /** Match the path EXACTLY rather than as a prefix. Only `/` needs this: as a
   *  prefix it matches every route in the app. */
  end?: boolean;
};

export type ShellPlace = {
  id: string;
  kind: PlaceKind;
  /** What the page column and mobile bar one call it. */
  name: string;
  /** Route prefixes that mean "you are in this place". Home carries none: it
   *  is the fallback, which is what makes it the root. */
  prefixes: string[];
  pages: ShellPage[];
  /** A platform place's glyph. Home and All chats are platform nav, so their
   *  icons ARE imports; an app's is not. See `icon` below. */
  Icon?: ComponentType<LucideProps>;
  /** An APP place's declared icon: a token, a url, or absent for a monogram.
   *  Resolved by lib/appIcon at render time, never imported here. */
  icon?: string;
  /** Whether the page column starts open the first time you arrive.
   *
   *  An app starts CLOSED: "each app should open full screen with the
   *  secondary toolbar hidden" (operator, session 2). Home and All chats start
   *  open, because their whole point is the list of pages. */
  defaultColumnOpen: boolean;
};

/** The minimum an installed app has to tell the shell to earn a dock tile.
 *
 *  `icon` is a DECLARED value and it is optional. Absent, the tile falls back to
 *  a two-letter monogram of the name -- two, because one is ambiguous on this
 *  very instance: Budget and Birds both render "B", which shipped and was
 *  immediately visible. See lib/appIcon. */
export type AppDescriptor = {
  id: string;
  name: string;
  home: string;
  icon?: string;
};

function matchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(prefix + "/");
}

/**
 * Which place a path belongs to. Home is the fallback and never matches by
 * prefix, so a route nobody claimed lands on Home rather than nowhere.
 */
export function placeFor(places: ShellPlace[], pathname: string): ShellPlace {
  const home = places.find((p) => p.kind === "home");
  let best: ShellPlace | undefined;
  let bestLen = -1;
  for (const place of places) {
    for (const prefix of place.prefixes) {
      if (matchesPrefix(pathname, prefix) && prefix.length > bestLen) {
        best = place;
        bestLen = prefix.length;
      }
    }
  }
  // `places` always contains a home place (buildPlaces puts it first). The
  // fallback to places[0] exists so a caller who hand-builds a list without one
  // gets a place rather than a crash.
  return best ?? home ?? places[0];
}

/**
 * Which page of a place a path is on, or null when the path is deeper than any
 * page (a record page, say) or the place has no page for it.
 *
 * Longest match wins, which is what makes `/chats/archived` beat `/chats`
 * without either of them needing to know the other exists.
 */
export function pageFor(place: ShellPlace, pathname: string): ShellPage | null {
  let best: ShellPage | null = null;
  let bestLen = -1;
  for (const page of place.pages) {
    const hit = page.end ? pathname === page.to : matchesPrefix(pathname, page.to);
    if (hit && page.to.length > bestLen) {
      best = page;
      bestLen = page.to.length;
    }
  }
  return best;
}

/** The path a place opens on when nothing is remembered about it. */
export function landingFor(place: ShellPlace): string {
  return place.pages[0]?.to ?? "/";
}

/**
 * Build the dock's list.
 *
 * `home` and `chats` are fixed; the app tiles come from whatever is installed.
 * Order is the dock's order, top to bottom.
 */
export function buildPlaces(
  apps: AppDescriptor[],
  icons: Record<string, ComponentType<LucideProps>>,
): ShellPlace[] {
  const home: ShellPlace = {
    id: "home",
    kind: "home",
    name: "Workshop",
    prefixes: [],
    Icon: icons.home,
    defaultColumnOpen: true,
    // ADR-008 order: use first, build second, workers third, config last. This
    // is the live SectionNav list with Home swapped for Dashboard, and Chats
    // removed because the dock owns it now (two rows to one destination is the
    // duplication the operator already called out about search).
    pages: [
      { id: "dashboard", label: "Dashboard", to: "/", Icon: icons.dashboard, end: true },
      { id: "projects", label: "Projects", to: "/projects", Icon: icons.projects },
      { id: "apps", label: "Apps", to: "/apps", Icon: icons.apps },
      { id: "agents", label: "Agents", to: "/agents", Icon: icons.agents },
      { id: "tickets", label: "Tickets", to: "/tickets", Icon: icons.tickets },
      { id: "deploys", label: "Deploys", to: "/deploys", Icon: icons.deploys },
      { id: "memory", label: "Memory", to: "/memory", Icon: icons.memory },
      { id: "usage", label: "Usage", to: "/usage", Icon: icons.usage },
    ],
  };

  const chats: ShellPlace = {
    id: "chats",
    kind: "chats",
    name: "All chats",
    // `/chat/` (singular) is one open conversation. It belongs to this place so
    // the dock stays lit while you are reading one, but it is not a PAGE: the
    // shell gives it the whole screen (see useShell's `fullScreen`).
    prefixes: ["/chats", "/chat"],
    Icon: icons.chats,
    defaultColumnOpen: true,
    pages: [
      { id: "all", label: "All chats", to: "/chats", Icon: icons.chats },
      { id: "archived", label: "Archived", to: "/chats/archived", Icon: icons.archived },
    ],
  };

  // SETTINGS IS A PLACE, not a row on Home. Operator, 2026-08-20: "clicking
  // settings just goes to the workshop home page and then settings, which is
  // actually not correct. Settings should be its own page, and the secondary
  // toolbar should have all the tabs for all the different sections."
  //
  // It has no dock TILE, because the avatar at the foot of the dock is already
  // the way in and a second control to the same place is the duplication the
  // dock was built to remove. `prefixes` is what makes the avatar land here
  // with this column open.
  const settings: ShellPlace = {
    id: "settings",
    kind: "settings",
    name: "Settings",
    prefixes: ["/settings"],
    Icon: icons.settings,
    defaultColumnOpen: true,
    pages: [
      { id: "account", label: "Account", to: "/settings", Icon: icons.account, end: true },
      { id: "appearance", label: "Appearance", to: "/settings/appearance", Icon: icons.appearance },
      { id: "chat", label: "Chat", to: "/settings/chat", Icon: icons.chats },
      { id: "instructions", label: "Instructions", to: "/settings/instructions", Icon: icons.instructions },
      { id: "memory", label: "Memory", to: "/settings/memory", Icon: icons.memory },
      { id: "apps", label: "Apps", to: "/settings/apps", Icon: icons.apps },
      { id: "trackers", label: "Trackers", to: "/settings/trackers", Icon: icons.trackers },
      { id: "mcp", label: "MCP", to: "/settings/mcp", Icon: icons.mcp },
      { id: "access", label: "Access", to: "/settings/grants", Icon: icons.access },
      // Fleet concurrency, the autonomy brake, and the queue runner. This was
      // "This host" until 2026-08-20, when the GPU/broker/Ollama residency rows
      // and the loopback service-probe grid (one machine's daemons, absent from
      // any production deploy) were removed. See pages/Settings.tsx.
      { id: "agents", label: "Agents", to: "/settings/agents", Icon: icons.agents },
    ],
  };

  const appPlaces: ShellPlace[] = apps.map((app) => ({
    id: `app:${app.id}`,
    kind: "app" as const,
    name: app.name,
    prefixes: [app.home],
    icon: app.icon,
    defaultColumnOpen: false,
    // ONE ROW, and that is honest. Not a nav choice: an app has no ticket queue
    // (`chat_work._QUEUELESS_PREFIXES = ("app:",)`), no runs and no deploys
    // (both keyed by `project_slug`), and no scope-filtered chat listing exists
    // at all. Those rows become real in stage C of
    // app-is-the-entity-2026-08-20.md §9, once the collections hang off an app.
    // Adding them before then would be a nav that lies.
    pages: [{ id: "app", label: app.name, to: app.home, Icon: icons.apps }],
  }));

  return [home, chats, ...appPlaces, settings];
}
