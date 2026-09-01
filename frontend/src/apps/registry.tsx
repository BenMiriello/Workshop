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

import { Suspense } from "react";
import type { ComponentType, ReactNode } from "react";
import { Route, NavLink, useLocation } from "react-router-dom";
import { RouteBoundary, RouteSkeleton, lazyPage } from "../lib/lazyPage";
import { queryClient } from "../lib/queryClient";
import { timeAgoSeconds, type AppChatDockRenderer } from "@workshop/ui";
import AppChatDock from "../components/AppChatDock";

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
  /** The app's icon, DECLARED not imported: an icon token (see lib/appIcon),
   *  a url/path for a custom image, or absent for a monogram of its name.
   *  It was `Icon?: ComponentType` holding a lucide component, which put "this
   *  particular app wants a bird" in platform source. */
  icon?: string;
  /** Live state for the launcher card. The launcher used to switch on app ids
   *  and fetch each app's endpoint itself; an app knows its own summary. */
  status?: () => Promise<AppStatus>;
  /** A panel this app contributes to the Settings screen, if any. */
  Settings?: ComponentType;
  /** The <Route> elements this app contributes under /apps. */
  routes: () => ReactNode;
};

const Finance = lazyPage(() => import("../pages/apps/Finance"));
const Budget = lazyPage(() => import("../pages/apps/Budget"));
const LocationApp = lazyPage(() => import("../pages/apps/Location"));
const CommuteApp = lazyPage(() => import("../pages/apps/Commute"));
const Notes = lazyPage(() => import("../pages/apps/notes/Notes"));

// The notes app's id, declared once. This file is the one frontend file allowed
// to name an app (frontend/CLAUDE.md), so the name lives here and everything
// that needs the app's base path derives it, rather than each site typing the
// slug again and one of them getting it wrong.
export const NOTES_APP_ID = "notes-app";
const notesAppHome = `/apps/${NOTES_APP_ID}`;
const Birds = lazyPage(() => import("../pages/apps/Birds"));
const Locations = lazyPage(() => import("../pages/apps/birds/Locations"));
const LocationDetail = lazyPage(() => import("../pages/apps/birds/LocationDetail"));
const Devices = lazyPage(() => import("../pages/apps/birds/Devices"));
const BirdDetail = lazyPage(() => import("../pages/apps/birds/BirdDetail"));
const BirdsReview = lazyPage(() => import("../pages/apps/birds/BirdsReview"));
const BirdSettings = lazyPage(() => import("../pages/apps/birds/BirdSettings"));
const Recordings = lazyPage(() => import("../pages/apps/birds/RecordingsPage"));
const Atlas = lazyPage(() => import("../pages/apps/birds/Atlas"));
const ForecastWeek = lazyPage(() => import("../pages/apps/birds/ForecastWeek"));
const HealthSettings = lazyPage(() => import("../pages/apps/health/HealthSettings"));
// Birds runs inside its own responsive shell (sidebar / mobile tabs) with the
// persistent Location·Device·Time scope. Lazy like every other app module:
// eager, it put `birds.css` in the entry stylesheet for every visitor.
const BirdsLayout = lazyPage(() => import("../pages/apps/birds/BirdsLayout"));

// EAGER, and the last one. A Provider is mounted around the whole session, so
// `AppProviders` reduces over it synchronously and cannot await a chunk without
// unmounting the app underneath it. `recorder.tsx` imports no CSS, so the cost
// is bounded, but a provider that must outlive navigation is exactly what the
// manifest should declare and the app loader should mount with the app's own
// bundle. Tracked as stage 5 of
// docs/platform-redesign/app-separation-and-github-2026-08-20.md.
import { RecorderProvider } from "../pages/apps/birds/recorder";


// ---- launcher status ------------------------------------------------------
// Each loader owns its own failure: a card that cannot reach its app says so
// rather than the launcher inventing a status for it.
//
// A PROBE READS THE APP'S OWN FETCHER, ON THE APP'S OWN KEY. It used to spell
// the URL and parse the body here, which is how the location card came to read
// `j.latest` from a route that answers with the record BARE
// (`response_model=LocationRecord | None`): undefined for every possible
// response, so that card said "no pings yet" permanently while the app page one
// tap away showed a live position. Nothing above an untyped `await r.json()`
// could catch it -- both spellings type-check, and the wrong answer is a
// perfectly plausible thing for a launcher card to say. Going through the app's
// exported fetcher makes the two readers unable to disagree, because there is
// only one of them.
//
// AND THROUGH `fetchQuery`, so a probe is a READ OF THE SHARED CACHE ENTRY
// rather than a second request for a fact the app is already holding. Opening
// the launcher just after leaving an app costs no request while that app's
// entry is fresh, and what the probe does fetch is sitting there for the app
// page when it mounts.
//
// The app modules are pulled in DYNAMICALLY, the same way this file loads every
// app page and for the same reason: an instance without an app should not carry
// its code, and a launcher probe must not put an app's fetchers in the entry
// chunk for every visitor.

async function financeStatus(): Promise<AppStatus> {
  try {
    const [statusR, balR] = await Promise.all([
      fetch("/api/finance/cache"),
      fetch("/api/finance/balances"),
    ]);
    // The balances route carries the answer, so a failure there is a failure of
    // the card. Substituting an empty roster rendered a 502 from the bank as
    // the sentence "0 accounts", and a card must not report as fact something
    // it could not ask about. The cache route is diagnostics and supplies only
    // the "updated" half, so losing THAT drops that half and keeps the count.
    if (!balR.ok) return { text: "unavailable", tone: "err" };
    const bal = await balR.json();
    const status = statusR.ok ? await statusR.json() : { entries: {} };
    const n = (bal.items || []).reduce(
      (s: number, it: { accounts?: unknown[] }) => s + (it.accounts?.length || 0),
      0,
    );
    const entry = status.entries?.["balances"];
    const age = entry ? Math.max(0, entry.age_s) : null;
    return {
      text: `${n} account${n === 1 ? "" : "s"} · ${age != null ? `updated ${timeAgoSeconds(age)}` : "no data yet"}`,
      tone: "muted",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

async function birdsStatus(): Promise<AppStatus> {
  try {
    const birds = await import("../pages/apps/birds/queries");
    const s = await queryClient.fetchQuery({
      queryKey: birds.birdsKeys.summary(birds.YARD_SUMMARY_PARAMS),
      queryFn: birds.fetchYardSummary,
    });
    // `available` is unconditionally true from this route, so it answers
    // nothing and the COUNT is what says whether there is a feed here yet.
    // Reading the flag meant a fresh instance rendered "0 species · 0 today"
    // in the ok tone and the "no detections yet" branch was unreachable.
    if (!s.species_count) return { text: "no detections yet", tone: "muted" };
    return {
      text: `${s.species_count} species · ${s.today?.detections || 0} today`,
      tone: "ok",
    };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

async function locationStatus(): Promise<AppStatus> {
  try {
    const loc = await import("../pages/apps/location/queries");
    // `null` is the real "nothing recorded yet"; a failed read THROWS and lands
    // in the catch below. The two used to be the same blank.
    const rec = await queryClient.fetchQuery({
      queryKey: loc.locationKeys.latest(),
      queryFn: loc.fetchLatest,
    });
    if (!rec?.ts) return { text: "no pings yet", tone: "muted" };
    const age = Math.max(0, Math.round((Date.now() - Date.parse(rec.ts)) / 1000));
    // A ping age is information, not health: significant-change tracking only
    // pings on movement, so hours-old while stationary is normal. Green only
    // when genuinely recent; otherwise neutral.
    return { text: `last ping ${timeAgoSeconds(age)}`, tone: age < 3600 ? "ok" : "muted" };
  } catch {
    return { text: "unavailable", tone: "err" };
  }
}

// ---- shell-provided props --------------------------------------------------
// `AppGroupNav` and `AppChatDock` both need platform knowledge (this
// registry's own group list; Workshop's chat system and its manifest
// endpoint) an app page must not import directly (Stage 4b,
// docs/platform-redesign/HANDOFF-app-platform-and-github-2026-08-20.md). This
// file is the one place allowed to know an app's id, so it renders both and
// hands the result down as props: `groupNav` is a ready element (it takes no
// per-render arguments), `renderChatDock` is a function (a page supplies its
// own title/placeholder copy and an optional onAction callback bound to its
// own state, and decides whether to call it at all (Birds gates it on a
// `PUBLIC` flag). A page that needs neither declares neither prop.

/** Bind a ready-to-call chat dock renderer to one app's id. */
function makeChatDock(appId: string): AppChatDockRenderer {
  return (opts) => (
    <AppChatDock
      appId={appId}
      title={opts?.title}
      placeholder={opts?.placeholder}
      onAction={opts?.onAction}
    />
  );
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
    icon: "banknote",
    status: financeStatus,
    group: "money",
    link: { label: "Finance", to: "/apps/finance" },
    crumbs: { "/apps/finance": { label: "Finance", parent: "/apps" } },
    routes: () => <Route key="finance" path="/apps/finance" element={<Finance groupNav={<AppGroupNav />} renderChatDock={makeChatDock("finance")} />} />,
  },
  {
    id: "budget",
    icon: "piggy-bank",
    status: async () => ({ text: "plan & simulate", tone: "muted" }),
    group: "money",
    link: { label: "Budget", to: "/apps/budget" },
    crumbs: { "/apps/budget": { label: "Budget", parent: "/apps" } },
    routes: () => <Route key="budget" path="/apps/budget" element={<Budget groupNav={<AppGroupNav />} renderChatDock={makeChatDock("budget")} />} />,
  },
  {
    id: "location",
    icon: "map-pin",
    status: locationStatus,
    group: "place",
    link: { label: "Location", to: "/apps/location" },
    crumbs: { "/apps/location": { label: "Location", parent: "/apps" } },
    routes: () => <Route key="location" path="/apps/location" element={<LocationApp groupNav={<AppGroupNav />} renderChatDock={makeChatDock("location")} />} />,
  },
  {
    id: "commute",
    icon: "train-front",
    status: async () => ({ text: "schedule & live status", tone: "muted" }),
    group: "place",
    link: { label: "Commute", to: "/apps/commute" },
    crumbs: { "/apps/commute": { label: "Commute", parent: "/apps" } },
    routes: () => <Route key="commute" path="/apps/commute" element={<CommuteApp groupNav={<AppGroupNav />} renderChatDock={makeChatDock("commute")} />} />,
  },
  {
    // THE PATH MUST BE THE SLUG, so it is DERIVED from the id rather than typed
    // beside it. This entry's crumb key and route used to be spelled one letter
    // short of its id. `appPlaces()` takes an app's `home` from the crumb key,
    // so places.ts built every generic tab from the short path, and the generic
    // route then resolved an app slug no record has: all seven tabs rendered a
    // full-page "no such app" error. The reverse failed too -- on the path that
    // DOES work, no place prefix matched, so the page column showed the Workshop
    // root nav instead of this app's own.
    //
    // Two spellings of one name is the bug. There is now one, and the paths are
    // built from it, so they cannot drift apart again.
    id: NOTES_APP_ID,
    icon: "notebook-pen",
    crumbs: { [notesAppHome]: { label: "Notes", parent: "/apps" } },
    routes: () => <Route key="notes" path={notesAppHome} element={<Notes />} />,
  },
  {
    id: "birds",
    icon: "bird",
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
        <Route path="/apps/birds" element={<Birds renderChatDock={makeChatDock("birds")} />} />
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

// ---- Linked-app switcher ---------------------------------------------------
// Each app belongs to at most one group; the group's siblings render as the
// switcher, passed down to the four grouped app pages as the `groupNav` prop
// (see the route elements above). This used to live in `components/appNav.tsx`,
// but that file imports `appCrumbs` FROM this one to build its route table, so
// this file importing `AppGroupNav` back from there would have made a cycle:
// `apps/registry.test.tsx` caught it at module-init time ("appCrumbs is not a
// function"), because appNav.tsx calls `appCrumbs()` at its own top level and a
// circular import hands it the registry module before that export exists yet.
// This file already owns the group data, so it renders the switcher itself.
type AppLink = { label: string; to: string };
const APP_GROUPS: AppLink[][] = appGroups();

function groupFor(pathname: string): AppLink[] | null {
  return (
    APP_GROUPS.find((g) => g.some((a) => pathname === a.to || pathname.startsWith(a.to + "/"))) ?? null
  );
}

function AppGroupNav() {
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

/** Mounts every app provider around the shell, outermost first. */
export function AppProviders({ children }: { children: ReactNode }) {
  return APPS.reduce<ReactNode>(
    (acc, a) => (a.Provider ? <a.Provider>{acc}</a.Provider> : acc),
    children,
  ) as React.ReactElement;
}

/** Wraps lazily-loaded app pages. The shell renders instantly either way.
 *
 *  The boundary is not optional. `fallback={null}` with nothing catching the
 *  load rejection meant a chunk that 404s (a tab open across a rebuild, which
 *  renames every content-hashed chunk) left the route blank permanently, with
 *  no message and no way back. See lib/lazyPage. */
export function AppSuspense({ children }: { children: ReactNode }) {
  return (
    <RouteBoundary>
      <Suspense fallback={<RouteSkeleton />}>{children}</Suspense>
    </RouteBoundary>
  );
}

/** Every installed app's Settings panel, in catalog order. */
export function appSettingsPanels(): ReactNode[] {
  return APPS.filter((a) => a.Settings).map((a) => {
    const Panel = a.Settings!;
    return <Panel key={a.id} />;
  });
}

/** The icon an app declared, if it declared one. A token/url string, never a
 *  component: resolving it is lib/appIcon's job, and the fallback is a monogram
 *  of the app's name rather than a generic placeholder glyph. */
export function appIcon(id: string): string | undefined {
  return APPS.find((a) => a.id === id)?.icon;
}

/** Every app that has a screen to open, as the shell's dock needs it.
 *
 *  An app with no routes (health, whose entire frontend is a Settings panel)
 *  has nowhere to land, so it gets no dock tile. That is a normal case, not a
 *  special one — same reasoning as `routes: () => null` being allowed.
 *
 *  The landing path is the app's `link`, or its shallowest breadcrumb node.
 *  Nothing new has to be declared per app for the dock to work, which is the
 *  point: the shell must not become a second place that lists apps. */
export function appPlaces(): {
  id: string;
  name: string;
  home: string;
  icon?: string;
}[] {
  const out: { id: string; name: string; home: string; icon?: string }[] = [];
  for (const a of APPS) {
    const paths = Object.keys(a.crumbs).sort((x, y) => x.length - y.length);
    const home = a.link?.to ?? paths[0];
    if (!home) continue;
    out.push({
      id: a.id,
      name: a.link?.label ?? a.crumbs[home]?.label ?? a.id,
      home,
      icon: a.icon,
    });
  }
  return out;
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
