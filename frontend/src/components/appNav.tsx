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
import { NavLink, useLocation } from "react-router-dom";

// Single source of truth for in-app navigation chrome:
//   • BreadcrumbBar — the toolbar breadcrumb trail "Workshop / Apps / … / Leaf".
//     Route-driven and globally responsive: it collapses on narrow widths by
//     first shortening "Workshop"→"W", then folding the highest-level crumbs
//     into a "…" that links to the deepest collapsed level.
//   • AppGroupNav — the switcher linking sibling apps (Finance ⇄ Budget,
//     Location ⇄ Commute), rendered inline in the PageHeader actions slot.
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
  "/apps/finance": { label: "Finance", parent: "/apps" },
  "/apps/budget": { label: "Budget", parent: "/apps" },
  "/apps/location": { label: "Location", parent: "/apps" },
  "/apps/birds": { label: "Birds", parent: "/apps" },
  "/apps/birds/locations": { label: "Locations", parent: "/apps/birds" },
  "/apps/birds/devices": { label: "Devices", parent: "/apps/birds" },
  "/apps/birds/settings": { label: "Settings", parent: "/apps/birds" },
  "/apps/birds/location": { label: "Location", parent: "/apps/birds/locations" },
  "/apps/birds/bird": { label: "Bird", parent: "/apps/birds" },
  "/apps/commute": { label: "Commute", parent: "/apps" },
  "/agents": { label: "Agents" },
  "/projects": { label: "Projects" },
  "/activity": { label: "Activity" },
  "/settings": { label: "Settings" },
};

// Longest-prefix lookup keys, most specific first.
const NODE_KEYS = Object.keys(NODES).sort((a, b) => b.length - a.length);

export function resolveCrumbs(pathname: string): Crumb[] {
  // Dynamic detail under a known section, e.g. /agents/<id>.
  if (pathname.startsWith("/agents/")) {
    return [
      { label: "Agents", to: "/agents" },
      { label: decodeURIComponent(pathname.slice("/agents/".length)) },
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
      <NavLink to="/apps" className="ws-brand__home">
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
const APP_GROUPS: AppLink[][] = [
  [
    { label: "Finance", to: "/apps/finance" },
    { label: "Budget", to: "/apps/budget" },
  ],
  [
    { label: "Location", to: "/apps/location" },
    { label: "Commute", to: "/apps/commute" },
  ],
];

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
