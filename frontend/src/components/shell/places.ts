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
// AN APP'S PAGE LIST IS THE APP'S, not one fixed set of eight rows (changed
// 2026-09-01, ticket 7d8d733a). Stage C gave every app the same eight -- the
// app plus Chats, Agents, Memory, Files, Tickets, Runs, Deploys -- on the
// reasoning that every one of them is a real route over a real
// /api/apps/{id}/... collection, and that an honestly-empty tab beats a fake
// one. Both halves of that are true and it was still the wrong answer: five of
// the seven can only ever be empty for an app with no linked working tree, so
// the operator opened app after app onto rows that existed to apologise.
//
// Two things decide the list now, and neither of them is a branch on an app:
//
//   * WHAT THE APP CAN HOLD. `tabsForApp` in pages/apps/tabs/appTabState.ts,
//     off the facts the backend resolves once per app (a linked project tree,
//     a file root). A tab that can never be anything but empty is not drawn.
//   * WHAT THE APP DECLARED. `backend/services/app_surface.py` reads a
//     `surface` block from the app's own manifest or its registry record: the
//     tabs it wants, in its order, and the surface it opens on.
//
// AND AN APP OPENS ON WHAT IT CHOSE. `landing` is the app's own answer,
// resolved by the backend so the dock and the launcher grid cannot send the
// operator to two different places. Absent a declaration it is the app's first
// available tab -- which is still a real choice about a real app rather than
// the hardcoded chat list every app used to land on.
//
// EVERY PAGE HERE RESOLVES TO A ROUTE THAT EXISTS. A row that navigates
// nowhere is worse than a missing row: it teaches the operator the nav lies.

import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";
// Which per-app tab rows this app gets, and in which order. One rule, read by
// both this file (the page column) and App.tsx (the route table), so the two
// can never drift apart on what a tab is called or where it lives.
import { tabsForApp, type AppTabFacts } from "../../pages/apps/tabs/appTabState";

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
  /** Where this place opens when nothing is remembered about it, when it is
   *  not simply the first page. An app declares this (see `landingFor`); the
   *  platform places do not, because their first row IS their landing. */
  landing?: string;
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
  /** The app's BASE path: its route prefix, and the stem every tab URL is
   *  built from. Not necessarily where it opens -- see `landing`. */
  home: string;
  icon?: string;
  /** Where the app opens, resolved from its own declaration by
   *  backend/services/app_surface.py. Absent while the platform's answer is
   *  still in flight, in which case the first tab row stands in. */
  landing?: string;
  /** The tab ids this app declared, in its order, or absent for "every tab it
   *  can have, in the platform's order". */
  tabs?: readonly string[];
  /** Whether this app has its OWN screen at `home`. An app that ships UI gets
   *  a row for it above its tabs; one that does not would get a row pointing
   *  at a redirect, which is a row to nowhere. */
  ownScreen?: boolean;
} & Partial<AppTabFacts>;

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

/** The path a place opens on when nothing is remembered about it.
 *
 *  An app's `landing` is its own declaration and outranks its first row: an app
 *  may keep Chats in its column while opening on its tickets. Everything else
 *  opens on its first page, which is the same answer this always gave. */
export function landingFor(place: ShellPlace): string {
  return place.landing ?? place.pages[0]?.to ?? "/";
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
    // is the live SectionNav list with Home swapped for Dashboard.
    //
    // Chats sits second, in the workshop nav, restored 2026-08-26 by operator
    // direction: he was in the home/workshop path and there was no way to the
    // chats list from it. It had been dropped as "two rows to one destination"
    // (the dock rail tile also points at /chats), but the rail tile is a 60px
    // glyph that is easy to miss and absent from the mobile bars, so the home
    // nav had no chats entry at all. The dock tile stays; this is the named,
    // labelled way in from the scope the operator actually browses.
    pages: [
      { id: "dashboard", label: "Dashboard", to: "/", Icon: icons.dashboard, end: true },
      { id: "chats", label: "Chats", to: "/chats", Icon: icons.chats },
      { id: "projects", label: "Projects", to: "/projects", Icon: icons.projects },
      { id: "apps", label: "Apps", to: "/apps", Icon: icons.apps },
      { id: "agents", label: "Agents", to: "/agents", Icon: icons.agents },
      { id: "tickets", label: "Tickets", to: "/tickets", Icon: icons.tickets },
      // The review surface. A row of its own rather than a tab under Tickets:
      // a change is a different record with a different lifecycle (design doc
      // sec 2), and one that nobody thought to open its ticket is exactly the
      // one that sits unreviewed.
      { id: "changes", label: "Changes", to: "/changes", Icon: icons.changes },
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
      { id: "models", label: "Models", to: "/settings/models", Icon: icons.models },
      { id: "mcp", label: "MCP", to: "/settings/mcp", Icon: icons.mcp },
      { id: "tools", label: "Tools", to: "/settings/tools", Icon: icons.tools },
      { id: "access", label: "Access", to: "/settings/grants", Icon: icons.access },
      // Fleet concurrency, the autonomy brake, and the queue runner. This was
      // "This host" until 2026-08-20, when the GPU/broker/Ollama residency rows
      // and the loopback service-probe grid (one machine's daemons, absent from
      // any production deploy) were removed. See pages/Settings.tsx.
      { id: "agents", label: "Agents", to: "/settings/agents", Icon: icons.agents },
    ],
  };

  const appPlaces: ShellPlace[] = apps.map((app) => {
    // THE ROWS THIS APP HAS, not a fixed eight. See the module comment: the
    // rule is `tabsForApp`, the facts come from the app's resolved surface,
    // and no branch here knows which app it is looking at.
    const tabs = tabsForApp(app.tabs, {
      projectSlug: app.projectSlug ?? null,
      hasFiles: app.hasFiles ?? false,
    });
    const pages: ShellPage[] = [
      // An app that ships its own screens gets a row for them, first. One that
      // does not gets no such row: `/apps/<slug>` is only a redirect for it, so
      // the row would point at its own tab list.
      ...(app.ownScreen
        ? [{ id: "app", label: app.name, to: app.home, Icon: icons.apps }]
        : []),
      ...tabs.map((t) => ({
        id: t.id,
        label: t.label,
        to: `${app.home}${t.suffix}`,
        Icon: icons[t.id] ?? icons.apps,
      })),
    ];
    return {
      id: `app:${app.id}`,
      kind: "app" as const,
      name: app.name,
      prefixes: [app.home],
      icon: app.icon,
      defaultColumnOpen: false,
      landing: app.landing,
      pages,
    };
  });

  return [home, chats, ...appPlaces, settings];
}
