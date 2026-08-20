import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { NavLink, useLocation } from "react-router-dom";
import { appCrumbs, appGroups } from "../apps/registry";

// What is left of the old top toolbar, which the shell replaced on 2026-08-20.
//
// `BreadcrumbBar` (the responsive collapsing trail) and `SectionNav` (the row
// of ten section links, plus its mobile overflow menu) are GONE with it: the
// dock and the page column do that work now, and the two doing it at once was
// one of the complaints the redesign started from. Their CSS went with them.
//
// What survives is the route HIERARCHY, because a record page still needs to
// say where it sits:
//   • resolveCrumbs / useCrumbs — the trail for a path, read by ShellTrail.
//   • BreadcrumbProvider / useCrumbLabel / useCrumbTrail — let a page feed a
//     dynamic leaf label (an entity name) or a fully custom trail.
//   • AppGroupNav — the switcher linking sibling apps, rendered inline in the
//     PageHeader actions slot by four app pages.

export type Crumb = { label: string; to?: string };

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
  "/agents": { label: "Agents" },
  "/agents/new": { label: "New agent", parent: "/agents" },
  // Route stays /work so every existing link and bookmark resolves; the LABEL
  // is the ticket, which is the ratified noun (entity-model-2026-07-18 §1).
  "/work": { label: "Tickets" },
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
    return [{ label: "Agents", to: "/agents" }, { label: "New agent" }];
  }
  // Agent work pane: /agents/<slug>/chat (and the legacy /agents/build/<slug>
  // redirect target). Reads Agents / <slug> / Chat.
  const chatMatch = pathname.match(/^\/agents\/([^/]+)\/chat$/);
  const buildMatch = pathname.match(/^\/agents\/build\/([^/]+)$/);
  if (chatMatch || buildMatch) {
    const slug = decodeURIComponent((chatMatch || buildMatch)![1]);
    return [
      { label: "Agents", to: "/agents" },
      { label: slug, to: `/agents/${encodeURIComponent(slug)}` },
      { label: "Chat" },
    ];
  }
  // Agent detail: /agents/<slug>.
  if (pathname.startsWith("/agents/")) {
    return [
      { label: "Agents", to: "/agents" },
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

// ---- Dynamic-label context -------------------------------------------------
// A page mounts and (optionally) tells the toolbar its real leaf label or a
// fully custom trail. Only the mounted route writes; unmount clears it.
type Override = { trail?: Crumb[]; leafLabel?: string };
const SetCtx = createContext<(o: Override) => void>(() => {});
const ValueCtx = createContext<Override>({});

export function BreadcrumbProvider({ children }: { children: ReactNode }) {
  const [override, setOverride] = useState<Override>({});
  return (
    <SetCtx.Provider value={setOverride}>
      <ValueCtx.Provider value={override}>{children}</ValueCtx.Provider>
    </SetCtx.Provider>
  );
}

// Override the leaf (current-page) crumb label with an entity name. Pass a
// falsy value while the entity is still loading — the route default shows until
// then. Safe to call unconditionally (hooks rule).
export function useCrumbLabel(label?: string | null) {
  const setOverride = useContext(SetCtx);
  useEffect(() => {
    if (!label) return;
    setOverride({ leafLabel: label });
    return () => setOverride({});
  }, [label, setOverride]);
}

// Supply a fully custom trail (everything after "Workshop"). Use when the path
// hierarchy alone can't express the trail — e.g. a bird scoped to a location.
export function useCrumbTrail(trail: Crumb[]) {
  const setOverride = useContext(SetCtx);
  const key = trail.map((c) => `${c.label}:${c.to ?? ""}`).join("|");
  useEffect(() => {
    setOverride({ trail });
    return () => setOverride({});
    // key captures the trail's content; `trail` identity changes every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setOverride]);
}

// The route's trail with whatever the mounted page said about its own leaf
// folded in. One reader: the shell's `ShellTrail`, which draws a trail only
// where one goes deeper than the page column already says.
export function useCrumbs(): Crumb[] {
  const { pathname } = useLocation();
  const override = useContext(ValueCtx);
  return useMemo<Crumb[]>(() => {
    const base = override.trail?.length ? override.trail.slice() : resolveCrumbs(pathname);
    if (override.leafLabel && base.length) {
      base[base.length - 1] = { ...base[base.length - 1], label: override.leafLabel };
    }
    return base;
  }, [pathname, override]);
}

// ---- Linked-app switcher ---------------------------------------------------
// Each app belongs to at most one group; the group's siblings render as the
// switcher. Lives inline in the PageHeader `actions` slot (left of Refresh).
type AppLink = { label: string; to: string };
const APP_GROUPS: AppLink[][] = appGroups();

function groupFor(pathname: string): AppLink[] | null {
  return (
    APP_GROUPS.find((g) => g.some((a) => pathname === a.to || pathname.startsWith(a.to + "/"))) ?? null
  );
}

export function AppGroupNav() {
  const { pathname } = useLocation();
  const group = groupFor(pathname);
  if (!group) return null;
  return (
    <nav className="ws-nav ws-appgroup" aria-label="Related apps">
      {group.map((a) => {
        const active = pathname === a.to || pathname.startsWith(a.to + "/");
        return (
          <NavLink key={a.to} to={a.to} className={active ? "is-active" : undefined}>
            {a.label}
          </NavLink>
        );
      })}
    </nav>
  );
}
