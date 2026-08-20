// WHAT THE DOCK POINTS AT, and which page of it you are on. The rule, not the
// render.
//
// Extracted so it can be executed: this repo has no DOM harness and is not
// getting one, so the target of a test is always the pure rule behind a
// behaviour (`transcriptBlocks`, `workRows`, `branchable`). Everything the
// shell decides that is not layout lives here or in `shellState.ts`.
//
// A PLACE IS NOT AN APP. The design sessions used one word for both and the
// two turned out to be different things: a place is anything the dock can
// point at (Home, All chats, an installed app), while an app is one installed
// feature module. See docs/platform-redesign/navigation-shell-2026-08-20.md
// section 7.1 — whether the dock should also list PROJECTS is the open
// question there, and this file is the one place that changes when it is
// answered.
//
// EVERY PAGE HERE RESOLVES TO A ROUTE THAT EXISTS. There is no Files page, no
// per-app Chats/Memory/Deploys and no Activity entry, because none of those has
// anything behind it today (design doc section 8). A row that navigates
// nowhere is worse than a missing row: it teaches the operator the nav lies.

import type { ComponentType } from "react";
import type { LucideProps } from "lucide-react";

export type PlaceKind = "home" | "chats" | "app";

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
  /** The dock tile's glyph, or its uppercase letter when it has none. */
  Icon?: ComponentType<LucideProps>;
  letter?: string;
  /** Whether the page column starts open the first time you arrive.
   *
   *  An app starts CLOSED: "each app should open full screen with the
   *  secondary toolbar hidden" (operator, session 2). Home and All chats start
   *  open, because their whole point is the list of pages. */
  defaultColumnOpen: boolean;
};

/** The minimum an installed app has to tell the shell to earn a dock tile.
 *  `Icon` is optional and the uppercase first letter is the fallback, which is
 *  what the design called for. In practice every installed app declares a
 *  glyph, and it should be used: two apps whose names start with the same
 *  letter (Budget and Birds) are indistinguishable as letters. */
export type AppDescriptor = {
  id: string;
  name: string;
  home: string;
  Icon?: ComponentType<LucideProps>;
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
      { id: "tickets", label: "Tickets", to: "/work", Icon: icons.tickets },
      { id: "deploys", label: "Deploys", to: "/deploys", Icon: icons.deploys },
      { id: "memory", label: "Memory", to: "/memory", Icon: icons.memory },
      { id: "usage", label: "Usage", to: "/usage", Icon: icons.usage },
      { id: "settings", label: "Settings", to: "/settings", Icon: icons.settings },
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

  const appPlaces: ShellPlace[] = apps.map((app) => ({
    id: `app:${app.id}`,
    kind: "app" as const,
    name: app.name,
    prefixes: [app.home],
    Icon: app.Icon,
    letter: app.Icon ? undefined : app.name.slice(0, 1).toUpperCase(),
    defaultColumnOpen: false,
    // ONE ROW, and that is honest. No installed app has chats, memory, deploys
    // or agents of its own, and Birds (the only app with several screens) ships
    // its own inner nav, so listing its screens here would put two page
    // switchers on one screen. See design doc section 8.6.
    pages: [{ id: "app", label: app.name, to: app.home, Icon: icons.apps }],
  }));

  return [home, chats, ...appPlaces];
}
