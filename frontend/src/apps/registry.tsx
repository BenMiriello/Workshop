// The app catalog for the frontend shell.
//
// This is the ONE file on the frontend allowed to name a specific app, and it
// is the counterpart to backend/services/app_registry.py. Everything else in
// the shell — App.tsx's route table, appNav's breadcrumbs and app switcher,
// the launcher's status probes — reads this instead of listing apps itself, so
// adding or removing an app is a change here and nowhere else.
//
// An app entry carries the four things the shell used to hardcode:
//   routes    the <Route> elements it contributes, including any layout route
//   Provider  a context provider the shell must mount for it (birds' recorder
//             needs to outlive navigation, so it wraps the whole shell)
//   crumbs    its breadcrumb nodes, merged into the shared route hierarchy
//   group     apps that link to each other in the PageHeader switcher
//
// Pages are imported lazily: an instance without an app should not pay for its
// bundle, and until dynamic bundle injection exists (docs/apps-are-pluggable.md
// calls this the deferred seam) lazy imports are what keeps an uninstalled
// app's code off the critical path.

import { Suspense, lazy } from "react";
import type { ComponentType, ReactNode } from "react";
import { Outlet, Route } from "react-router-dom";
import { Banknote, Bird, MapPin, NotebookPen, PiggyBank, TrainFront } from "lucide-react";
import type { LucideProps } from "lucide-react";

export type CrumbNode = { label: string; parent?: string };

/** One line of live state for the launcher card. */
export type AppStatus = { text: string; tone: "muted" | "ok" | "err" };

export type AppModule = {
  id: string;
  /** Breadcrumb nodes for this app's routes, keyed by exact path. */
  crumbs: Record<string, CrumbNode>;
  /** Apps sharing a group id render as each other's switcher siblings. */
  group?: string;
  /** Label + landing route for the switcher. Omit to stay out of it. */
  link?: { label: string; to: string };
  /** A provider the shell mounts for the whole session while installed. */
  Provider?: ComponentType<{ children: ReactNode }>;
  /** The glyph for this app's launcher card. */
  Icon?: ComponentType<LucideProps>;
  /** Live state for the launcher card. The launcher used to switch on app ids
   *  and fetch each app's endpoint itself; an app knows its own summary. */
  status?: () => Promise<AppStatus>;
  /** A panel this app contributes to the Settings screen, if any. */
  Settings?: ComponentType;
  /** The <Route> elements this app contributes under /apps. */
  routes: () => ReactNode;
};

const Finance = lazy(() => import("../pages/apps/Finance"));
const Budget = lazy(() => import("../pages/apps/Budget"));
const LocationApp = lazy(() => import("../pages/apps/Location"));
const CommuteApp = lazy(() => import("../pages/apps/Commute"));
const Notes = lazy(() => import("../pages/apps/notes/Notes"));
const Birds = lazy(() => import("../pages/apps/Birds"));
const Locations = lazy(() => import("../pages/apps/birds/Locations"));
const LocationDetail = lazy(() => import("../pages/apps/birds/LocationDetail"));
const Devices = lazy(() => import("../pages/apps/birds/Devices"));
const BirdDetail = lazy(() => import("../pages/apps/birds/BirdDetail"));
const BirdsReview = lazy(() => import("../pages/apps/birds/BirdsReview"));
const BirdSettings = lazy(() => import("../pages/apps/birds/BirdSettings"));
const Recordings = lazy(() => import("../pages/apps/birds/RecordingsPage"));
const Atlas = lazy(() => import("../pages/apps/birds/Atlas"));
const ForecastWeek = lazy(() => import("../pages/apps/birds/ForecastWeek"));
const HealthSettings = lazy(() => import("../pages/apps/health/HealthSettings"));

import { BirdsScopeProvider } from "../pages/apps/birds/scope";
import BirdsShell from "../pages/apps/birds/BirdsShell";
import { RecorderProvider } from "../pages/apps/birds/recorder";

// Birds runs inside its own responsive shell (sidebar / mobile tabs) with the
// persistent Location·Device·Time scope. The Workshop topnav stays the outer
// nav level; this is the inner one.
function BirdsLayout() {
  return (
    <BirdsScopeProvider>
      <BirdsShell><Outlet /></BirdsShell>
    </BirdsScopeProvider>
  );
}


// ---- launcher status ------------------------------------------------------
// Each loader owns its own failure: a card that cannot reach its app says so
// rather than the launcher inventing a status for it.

function fmtAge(s: number): string {
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

async function financeStatus(): Promise<AppStatus> {
  try {
    const [statusR, balR] = await Promise.all([
      fetch("/api/finance/cache"),
      fetch("/api/finance/balances"),
    ]);
    const status = statusR.ok ? await statusR.json() : { entries: {} };
    const bal = balR.ok ? await balR.json() : { items: [] };
    const n = (bal.items || []).reduce(
      (s: number, it: { accounts?: unknown[] }) => s + (it.accounts?.length || 0),
      0,
    );
    const entry = status.entries?.["balances"];
    const age = entry ? Math.max(0, entry.age_s) : null;
    return {
      text: `${n} account${n === 1 ? "" : "s"} · ${age != null ? `updated ${fmtAge(age)}` : "no data yet"}`,
      tone: "muted",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

async function birdsStatus(): Promise<AppStatus> {
  try {
    const r = await fetch("/api/birds/summary");
    const s = r.ok ? await r.json() : null;
    if (!s || !s.available) return { text: "no detections yet", tone: "muted" };
    return {
      text: `${s.species_count || 0} species · ${s.today?.detections || 0} today`,
      tone: "ok",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

async function locationStatus(): Promise<AppStatus> {
  try {
    const r = await fetch("/api/location/latest");
    const j = r.ok ? await r.json() : { latest: null };
    const ts = (j.latest as { ts?: string } | null)?.ts;
    if (!ts) return { text: "no pings yet", tone: "muted" };
    const age = Math.max(0, Math.round((Date.now() - Date.parse(ts)) / 1000));
    // A ping age is information, not health — significant-change tracking only
    // pings on movement, so hours-old while stationary is normal. Green only
    // when genuinely recent; otherwise neutral.
    return { text: `last ping ${fmtAge(age)}`, tone: age < 3600 ? "ok" : "muted" };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

export const APPS: AppModule[] = [
  {
    // Health has no page of its own: a settings panel is its entire frontend.
    // An app contributing no routes is a normal case, not a special one.
    id: "health",
    Settings: HealthSettings,
    crumbs: {},
    routes: () => null,
  },
  {
    id: "finance",
    Icon: Banknote,
    status: financeStatus,
    group: "money",
    link: { label: "Finance", to: "/apps/finance" },
    crumbs: { "/apps/finance": { label: "Finance", parent: "/apps" } },
    routes: () => <Route key="finance" path="/apps/finance" element={<Finance />} />,
  },
  {
    id: "budget",
    Icon: PiggyBank,
    status: async () => ({ text: "plan & simulate", tone: "muted" }),
    group: "money",
    link: { label: "Budget", to: "/apps/budget" },
    crumbs: { "/apps/budget": { label: "Budget", parent: "/apps" } },
    routes: () => <Route key="budget" path="/apps/budget" element={<Budget />} />,
  },
  {
    id: "location",
    Icon: MapPin,
    status: locationStatus,
    group: "place",
    link: { label: "Location", to: "/apps/location" },
    crumbs: { "/apps/location": { label: "Location", parent: "/apps" } },
    routes: () => <Route key="location" path="/apps/location" element={<LocationApp />} />,
  },
  {
    id: "commute",
    Icon: TrainFront,
    status: async () => ({ text: "schedule & live status", tone: "muted" }),
    group: "place",
    link: { label: "Commute", to: "/apps/commute" },
    crumbs: { "/apps/commute": { label: "Commute", parent: "/apps" } },
    routes: () => <Route key="commute" path="/apps/commute" element={<CommuteApp />} />,
  },
  {
    id: "notes-app",
    Icon: NotebookPen,
    crumbs: { "/apps/notes": { label: "Notes", parent: "/apps" } },
    routes: () => <Route key="notes" path="/apps/notes" element={<Notes />} />,
  },
  {
    id: "birds",
    Icon: Bird,
    status: birdsStatus,
    Provider: RecorderProvider,
    crumbs: {
      "/apps/birds": { label: "Birds", parent: "/apps" },
      "/apps/birds/locations": { label: "Locations", parent: "/apps/birds" },
      "/apps/birds/devices": { label: "Devices", parent: "/apps/birds" },
      "/apps/birds/settings": { label: "Settings", parent: "/apps/birds" },
      "/apps/birds/recordings": { label: "Recordings", parent: "/apps/birds" },
      "/apps/birds/atlas": { label: "Atlas", parent: "/apps/birds" },
      "/apps/birds/location": { label: "Location", parent: "/apps/birds/locations" },
      "/apps/birds/bird": { label: "Bird", parent: "/apps/birds" },
    },
    routes: () => (
      <Route key="birds" element={<BirdsLayout />}>
        <Route path="/apps/birds" element={<Birds />} />
        <Route path="/apps/birds/birds" element={<BirdsReview />} />
        <Route path="/apps/birds/locations" element={<Locations />} />
        <Route path="/apps/birds/location" element={<LocationDetail />} />
        <Route path="/apps/birds/devices" element={<Devices />} />
        <Route path="/apps/birds/bird" element={<BirdDetail />} />
        <Route path="/apps/birds/settings" element={<BirdSettings />} />
        <Route path="/apps/birds/recordings" element={<Recordings />} />
        <Route path="/apps/birds/atlas" element={<Atlas />} />
        <Route path="/apps/birds/forecast" element={<ForecastWeek />} />
      </Route>
    ),
  },
];

/** Every app's <Route> elements, for the shell's one <Routes>. */
export function appRoutes(): ReactNode[] {
  return APPS.map((a) => a.routes());
}

/** Breadcrumb nodes from every app, merged. */
export function appCrumbs(): Record<string, CrumbNode> {
  return Object.assign({}, ...APPS.map((a) => a.crumbs));
}

/** The switcher groups: sibling apps that link to each other. */
export function appGroups(): { label: string; to: string }[][] {
  const byGroup = new Map<string, { label: string; to: string }[]>();
  for (const a of APPS) {
    if (!a.group || !a.link) continue;
    const list = byGroup.get(a.group) ?? [];
    list.push(a.link);
    byGroup.set(a.group, list);
  }
  return [...byGroup.values()].filter((g) => g.length > 1);
}

/** Mounts every app provider around the shell, outermost first. */
export function AppProviders({ children }: { children: ReactNode }) {
  return APPS.reduce<ReactNode>(
    (acc, a) => (a.Provider ? <a.Provider>{acc}</a.Provider> : acc),
    children,
  ) as React.ReactElement;
}

/** Wraps lazily-loaded app pages. The shell renders instantly either way. */
export function AppSuspense({ children }: { children: ReactNode }) {
  return <Suspense fallback={null}>{children}</Suspense>;
}

/** Every installed app's Settings panel, in catalog order. */
export function appSettingsPanels(): ReactNode[] {
  return APPS.filter((a) => a.Settings).map((a) => {
    const Panel = a.Settings!;
    return <Panel key={a.id} />;
  });
}

/** The glyph an app declared, if it declared one. */
export function appIcon(id: string): ComponentType<LucideProps> | undefined {
  return APPS.find((a) => a.id === id)?.Icon;
}

/** Load every installed app's launcher status, keyed by app id. */
export async function appStatuses(): Promise<Record<string, AppStatus>> {
  const out: Record<string, AppStatus> = {};
  await Promise.all(
    APPS.filter((a) => a.status).map(async (a) => {
      out[a.id] = await a.status!();
    }),
  );
  return out;
}
