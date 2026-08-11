import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { Menu as MenuIcon, Home as HomeIcon, Bot, FolderGit2, LayoutGrid, ListChecks, Brain, MessagesSquare, Gauge, Rocket, Settings as SettingsIcon } from "lucide-react";
import { Menu, MenuItem } from "@workshop/ui";
import { appCrumbs, appGroups } from "../apps/registry";

// Single source of truth for in-app navigation chrome:
//   • BreadcrumbBar — the toolbar breadcrumb trail "Workshop / Apps / … / Leaf".
//     Route-driven and globally responsive: it collapses on narrow widths by
//     first shortening "Workshop"→"W", then folding the highest-level crumbs
//     into a "…" that links to the deepest collapsed level.
//   • AppGroupNav — the switcher linking sibling apps, grouped in the app
//     registry, rendered inline in the PageHeader actions slot.
//   • BreadcrumbProvider / useCrumbLabel / useCrumbTrail — let a page feed a
//     dynamic leaf label (entity name) or a fully custom trail to the toolbar.

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
  "/work": { label: "Work" },
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
  // "build:foo" reads as "Chat · build:foo" rather than the encoded form.
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

// ---- BreadcrumbBar ---------------------------------------------------------
type Plan = { brandShort: boolean; from: number; ellipsisTo: string | null };

export function BreadcrumbBar() {
  const { pathname } = useLocation();
  const override = useContext(ValueCtx);

  const crumbs = useMemo<Crumb[]>(() => {
    const base = override.trail?.length ? override.trail.slice() : resolveCrumbs(pathname);
    if (override.leafLabel && base.length) {
      base[base.length - 1] = { ...base[base.length - 1], label: override.leafLabel };
    }
    return base;
  }, [pathname, override]);

  const barRef = useRef<HTMLElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);
  const [plan, setPlan] = useState<Plan>({ brandShort: false, from: 0, ellipsisTo: null });

  useLayoutEffect(() => {
    const bar = barRef.current;
    const m = measureRef.current;
    if (!bar || !m) return;
    const widthOf = (sel: string) => {
      const el = m.querySelector<HTMLElement>(`[data-m="${sel}"]`);
      return el ? el.getBoundingClientRect().width : 0;
    };
    const compute = () => {
      const n = crumbs.length;
      if (n === 0) {
        setPlan({ brandShort: false, from: 0, ellipsisTo: null });
        return;
      }
      const avail = bar.clientWidth - 1; // sub-pixel safety
      if (avail <= 0) return; // not laid out yet; a later observer fire recomputes
      const brandFull = widthOf("brandFull");
      const brandShort = widthOf("brandShort");
      const ell = widthOf("ellipsis");
      const cw: number[] = [];
      let sum = 0;
      for (let i = 0; i < n; i++) {
        cw[i] = widthOf("c" + i);
        sum += cw[i];
      }
      // 1) full brand + every crumb.
      if (brandFull + sum <= avail) {
        setPlan({ brandShort: false, from: 0, ellipsisTo: null });
        return;
      }
      // 2+) short brand, then fill crumbs from the leaf leftward, reserving the
      // ellipsis whenever crumbs would remain hidden on the left.
      let used = brandShort + cw[n - 1];
      let from = n - 1;
      for (let i = n - 2; i >= 0; i--) {
        const reserve = i > 0 ? ell : 0;
        if (used + cw[i] + reserve <= avail) {
          used += cw[i];
          from = i;
        } else break;
      }
      const ellipsisTo = from > 0 ? crumbs[from - 1].to ?? null : null;
      setPlan({ brandShort: true, from, ellipsisTo });
    };
    compute();
    const ro = new ResizeObserver(compute);
    ro.observe(bar); // available width changes (viewport, topnav wrap)
    ro.observe(m); // content width changes (web-font swap, dynamic leaf labels)
    // Fonts load with display=swap; the post-swap metrics widen crumbs after
    // first paint. The measurer observer above catches the resize, but trigger
    // an explicit recompute on fonts.ready too as a belt-and-suspenders.
    const fonts = (document as unknown as { fonts?: { ready?: Promise<unknown> } }).fonts;
    let cancelled = false;
    fonts?.ready?.then(() => {
      if (!cancelled) compute();
    });
    return () => {
      cancelled = true;
      ro.disconnect();
    };
  }, [crumbs]);

  const n = crumbs.length;

  return (
    <nav className="ws-breadcrumbs" ref={barRef} aria-label="Breadcrumb">
      <NavLink to="/" end className="ws-brand__home">
        {plan.brandShort ? "W" : "Workshop"}
      </NavLink>

      {plan.ellipsisTo != null && (
        <span className="ws-bc__item">
          <span className="ws-bc__sep" aria-hidden>
            /
          </span>
          <NavLink to={plan.ellipsisTo} className="ws-bc__ellipsis" aria-label="Show parent levels">
            …
          </NavLink>
        </span>
      )}

      {crumbs.map((c, i) => {
        if (i < plan.from) return null;
        const isLeaf = i === n - 1;
        return (
          <span className="ws-bc__item" key={i}>
            <span className="ws-bc__sep" aria-hidden>
              /
            </span>
            {isLeaf || !c.to ? (
              <span
                className={isLeaf ? "ws-bc__cur ws-bc__leaf" : "ws-bc__cur"}
                aria-current={isLeaf ? "page" : undefined}
              >
                {c.label}
              </span>
            ) : (
              <NavLink to={c.to} className="ws-bc__link">
                {c.label}
              </NavLink>
            )}
          </span>
        );
      })}

      {/* Hidden measurer — natural widths feed the collapse math above. */}
      <div className="ws-bc__measure" aria-hidden ref={measureRef}>
        <span data-m="brandFull" className="ws-brand__home">
          Workshop
        </span>
        <span data-m="brandShort" className="ws-brand__home">
          W
        </span>
        {/* Labels MUST carry the same classes as the rendered crumbs so they
            measure in the real font (JetBrains Mono, 0.92em) — bare text would
            be measured in the proportional UI font and under-report the width,
            making a too-long trail read as "fits" and never collapse. */}
        <span data-m="ellipsis" className="ws-bc__item">
          <span className="ws-bc__sep">/</span>
          <span className="ws-bc__ellipsis">…</span>
        </span>
        {crumbs.map((c, i) => (
          <span data-m={"c" + i} className="ws-bc__item" key={i}>
            <span className="ws-bc__sep">/</span>
            <span className="ws-bc__cur">{c.label}</span>
          </span>
        ))}
      </div>
    </nav>
  );
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

// ---- Section nav (top-level sections) --------------------------------------
// Desktop: the inline link row on the right of the toolbar. Mobile (≤640px):
// the links collapse to a single overflow-menu button so the toolbar stays one
// row — the current section is already named in the breadcrumb, so nothing is
// lost. CSS toggles which of the two renders at the breakpoint.
// Per ADR-008: use first, build second, workers third, config last. Matches
// frequency of operator use. Work is the "workers" view: its Tickets tab is the
// cross-scope task list (the queue every parked to-do lands in) and its Runs tab
// is the Jobs feed (every autonomous sweep + manual run). /jobs stays reachable.
const SECTIONS = [
  { to: "/", label: "Home", icon: HomeIcon },
  { to: "/chats", label: "Chats", icon: MessagesSquare },
  { to: "/projects", label: "Projects", icon: FolderGit2 },
  { to: "/apps", label: "Apps", icon: LayoutGrid },
  { to: "/agents", label: "Agents", icon: Bot },
  { to: "/work", label: "Work", icon: ListChecks },
  { to: "/deploys", label: "Deploys", icon: Rocket },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/usage", label: "Usage", icon: Gauge },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];
const linkClass = ({ isActive }: { isActive: boolean }) => (isActive ? "is-active" : undefined);

export function SectionNav() {
  const { pathname } = useLocation();
  const navigate = useNavigate();

  return (
    <>
      <nav className="ws-nav ws-sectionnav-inline" aria-label="Sections">
        {SECTIONS.map((s) => (
          <NavLink key={s.to} to={s.to} end={s.to === "/"} className={linkClass}>
            {s.label}
          </NavLink>
        ))}
      </nav>

      {/* Same sections, same active highlight — now on the shared Menu
          primitive, which brings the keyboard nav and focus handling this
          hand-rolled popover never had. */}
      <Menu
        className="ws-sectionnav-menu"
        label="Sections"
        trigger={
          <button type="button" className="ws-iconbtn" aria-label="Sections menu">
            <MenuIcon size={20} aria-hidden />
          </button>
        }
      >
        {SECTIONS.map((s) => {
          const Icon = s.icon;
          const isActive = s.to === "/" ? pathname === "/" : pathname.startsWith(s.to);
          return (
            <MenuItem key={s.to} icon={<Icon size={16} />} active={isActive} onSelect={() => navigate(s.to)}>
              {s.label}
            </MenuItem>
          );
        })}
      </Menu>
    </>
  );
}
