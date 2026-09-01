import { useMemo } from "react";
import { useLocation } from "react-router-dom";
import { useBreadcrumbOverride, type Crumb } from "@workshop/ui";
import { appCrumbs } from "../apps/registry";

// What is left of the old top toolbar, which the shell replaced on 2026-08-20.
//
// `BreadcrumbBar` (the responsive collapsing trail) and `SectionNav` (the row
// of ten section links, plus its mobile overflow menu) are GONE with it: the
// dock and the page column do that work now, and the two doing it at once was
// one of the complaints the redesign started from. Their CSS went with them.
//
// What survives is the route HIERARCHY, because a record page still needs to
// say where it sits:
//   • resolveCrumbs / useCrumbs: the trail for a path, read by ShellTrail.
//     `BreadcrumbProvider` / `useCrumbLabel` / `useCrumbTrail`, the generic
//     half of this (a page feeding a dynamic leaf label or a fully custom
//     trail), moved to `@workshop/ui` 2026-08-20 (Stage 4b): the override
//     mechanism has nothing platform-specific in it. This file keeps the
//     RESOLUTION half, because mapping a route to its default trail needs the
//     platform's own route table (NODES, below, merged with every installed
//     app's crumbs).
//   • AppGroupNav (the switcher linking sibling apps) moved OUT of this file
//     entirely, into `apps/registry.tsx`, which already owns `appGroups()`.
//     `apps/registry.tsx` importing this file for it would have made a cycle
//     (this file already imports `appCrumbs` FROM the registry to build
//     NODES below), so the registry renders its own switcher and hands the
//     result down to the four grouped app pages as a `groupNav` prop; those
//     pages no longer import this file at all.

export type { Crumb };

// ---- Route hierarchy -------------------------------------------------------
// Each node maps its exact path to a display label + parent path. The trail for
// any route is built by walking parent links up to the root. The leaf's label
// can be overridden at runtime (see useCrumbLabel) for entity pages.
type RouteNode = { label: string; parent?: string };

const NODES: Record<string, RouteNode> = {
  "/apps": { label: "Apps" },
  "/marketplace": { label: "Marketplace" },
  // Per-app nodes are merged in below from the app registry.
  "/": { label: "Home" },
  // ROLES, NOT AGENTS. The roster is retired; /agents redirects to /roles
  // (App.tsx). A crumb that named a page nobody can reach would be the
  // trail lying about where you are.
  "/roles": { label: "Roles" },
  "/agents/new": { label: "New agent", parent: "/roles" },
  // Route and label agree since ADR 0023. /work still resolves, as a redirect
  // that keeps its query, so old links and bookmarks are unaffected.
  "/tickets": { label: "Tickets" },
  "/memory": { label: "Memory" },
  "/runs": { label: "Runs" },
  "/usage": { label: "Usage" },
  "/chats": { label: "Chats" },
  "/projects": { label: "Projects" },
  "/projects/new": { label: "New project", parent: "/projects" },
  "/settings": { label: "Settings" },
  "/settings/base-apps": { label: "Base Apps", parent: "/settings" },
  "/settings/grants": { label: "Grants", parent: "/settings" },
  "/settings/trackers": { label: "Trackers", parent: "/settings" },
  "/settings/mcp": { label: "MCP servers", parent: "/settings" },
  "/settings/instructions": { label: "Instructions", parent: "/settings" },
};

Object.assign(NODES, appCrumbs());

// Longest-prefix lookup keys, most specific first.
const NODE_KEYS = Object.keys(NODES).sort((a, b) => b.length - a.length);

export function resolveCrumbs(pathname: string): Crumb[] {
  // Focused chat: /chat/<context-key>. The leaf shows the decoded key so
  // "agent:foo" reads as "Chat · agent:foo" rather than the encoded form.
  if (pathname.startsWith("/chat/")) {
    const key = decodeURIComponent(pathname.slice("/chat/".length));
    return [{ label: `Chat · ${key}` }];
  }
  // New agent: /agents/new.
  if (pathname === "/agents/new") {
    return [{ label: "Roles", to: "/roles" }, { label: "New agent" }];
  }
  // Agent work pane: /agents/<slug>/chat (and the legacy /agents/build/<slug>
  // redirect target). Reads Roles / <slug> / Chat.
  const chatMatch = pathname.match(/^\/agents\/([^/]+)\/chat$/);
  const buildMatch = pathname.match(/^\/agents\/build\/([^/]+)$/);
  if (chatMatch || buildMatch) {
    const slug = decodeURIComponent((chatMatch || buildMatch)![1]);
    return [
      { label: "Roles", to: "/roles" },
      { label: slug, to: `/agents/${encodeURIComponent(slug)}` },
      { label: "Chat" },
    ];
  }
  // Agent detail: /agents/<slug>.
  if (pathname.startsWith("/agents/")) {
    return [
      { label: "Roles", to: "/roles" },
      { label: decodeURIComponent(pathname.slice("/agents/".length)) },
    ];
  }
  // Project detail: /projects/<slug>. The page calls useCrumbLabel to swap the
  // leaf for the project title once loaded; until then we show the slug.
  if (pathname.startsWith("/projects/") && pathname !== "/projects/new") {
    return [
      { label: "Projects", to: "/projects" },
      { label: decodeURIComponent(pathname.slice("/projects/".length)) },
    ];
  }
  const key =
    NODES[pathname] != null
      ? pathname
      : NODE_KEYS.find((k) => pathname === k || pathname.startsWith(k + "/")) ?? null;
  if (!key) return [];
  const chain: Crumb[] = [];
  let cur: string | undefined = key;
  while (cur) {
    const node: RouteNode | undefined = NODES[cur];
    if (!node) break;
    chain.unshift({ label: node.label, to: cur });
    cur = node.parent;
  }
  return chain;
}

// The route's default trail with whatever the mounted page announced through
// `useCrumbLabel`/`useCrumbTrail` (@workshop/ui) folded in. One reader: the
// shell's `ShellTrail`, which draws a trail only where one goes deeper than
// the page column already says.
export function useCrumbs(): Crumb[] {
  const { pathname } = useLocation();
  const override = useBreadcrumbOverride();
  return useMemo<Crumb[]>(() => {
    const base = override.trail?.length ? override.trail.slice() : resolveCrumbs(pathname);
    if (override.leafLabel && base.length) {
      base[base.length - 1] = { ...base[base.length - 1], label: override.leafLabel };
    }
    return base;
  }, [pathname, override]);
}
