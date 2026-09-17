import { lazyPage, RouteSkeleton } from "./lib/lazyPage";
import { runHref } from "./lib/jobs";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { useFreshBuild } from "./lib/useFreshBuild";
import Roles from "./pages/Roles";
import Chats, { ChatsArchived } from "./pages/Chats";
import Apps from "./pages/Apps";
import Home from "./pages/Home";
import Tickets from "./pages/Tickets";
import TicketDetail from "./pages/TicketDetail";
import FocusedChat from "./pages/FocusedChat";
import { BreadcrumbProvider } from "@workshop/ui";
import { QueryClientProvider } from "@tanstack/react-query";
import { queryClient } from "./lib/queryClient";
import { Shell } from "./components/shell/Shell";
import { AppProviders, AppSuspense, appRoutes, appRedirects } from "./apps/registry";
import { useAppDeclarations } from "./apps/declarations";
import { useAppPlacesState } from "./apps/useAppPlaces";

// LAZY BY ROUTE. Each of these is a page the app does not need to render its
// first screen, and several drag heavy libraries behind them (Usage pulls
// recharts). Eager imports put all of it in the main chunk, which every
// visitor downloads before anything appears. Every route below already
// renders inside <AppSuspense>, so this needs no new boundary.
//
// Keep Home, Work, Roles, Apps, Chats and FocusedChat EAGER: those are the
// landing surfaces, and a suspense flash on the way into the chat is worse
// than the bytes.
const AgentDetail = lazyPage(() => import("./pages/AgentDetail"));
const ProjectNew = lazyPage(() => import("./pages/ProjectNew"));

// A project's own page is now its app's Overview. The slug is the same in both
// registries, so an old /projects/<slug> link resolves rather than 404ing.
function ProjectSlugRedirect() {
  const { slug = "" } = useParams();
  return <Navigate to={`/apps/${encodeURIComponent(slug)}/overview`} replace />;
}
const AppPreview = lazyPage(() => import("./pages/AppPreview"));
const Grants = lazyPage(() => import("./pages/settings/Grants"));
// Trackers and McpServers are no longer routed: they are SECTIONS now, of
// Settings > Apps and Settings > Tools and MCP respectively, and each is
// imported by the page it is a section of. Their old routes redirect.
const ModelProviders = lazyPage(() => import("./pages/settings/ModelProviders"));
const ToolsSettings = lazyPage(() => import("./pages/settings/Tools"));
// CustomInstructions is a SECTION of Settings > Chat now, imported by the page
// it is a section of. Its old route redirects there.
const Marketplace = lazyPage(() => import("./pages/Marketplace"));
const StoreDetail = lazyPage(() => import("./pages/StoreDetail"));
const Stores = lazyPage(() => import("./pages/Stores"));
const MemoryPage = lazyPage(() => import("./pages/Memory"));
// THE THREE TOP-LEVEL LISTS ARE GONE (2026-09-01). pages/Runs.tsx,
// pages/Changes.tsx and pages/Deploys.tsx were deleted, along with the
// components/JobsFeed.tsx that only Runs mounted, per section 6 of
// threads-branches-and-pages-2026-08-29.md: each of the three is a view of one
// app now -- /apps/<slug>/work?lens=runs, ?lens=branches, and the app's
// Overview -- so a cross-app page of each was a fifth design generation left
// routed. Their DETAIL pages below stay: a run, a change and a deploy are
// records, and a record outlives the list it was found in.
// The Branches page is retired and its component is deleted. /branches
// redirects to /chats, which is a FLAT list of chats: the grouping-by-worktree
// version shipped and was reverted the same day (ticket 65cb8c6f), because
// unnamed t-<hex> slugs pushed the actual conversations down the page. A branch
// is a small tag on a chat row. The branch-centred view belongs in the app's
// Work tab, not on the global chat list -- threads-branches-and-pages-2026-08-29.md §6.
const ChangeReview = lazyPage(() => import("./pages/ChangeReview"));
const RunDetail = lazyPage(() => import("./pages/RunDetail"));
const Usage = lazyPage(() => import("./pages/Usage"));
const Settings = lazyPage(() => import("./pages/Settings"));

// The seven per-app tabs (app-is-the-entity-2026-08-20.md sec 9 stage C).
// Generic over `:appSlug` -- these name no app, unlike the routes appRoutes()
// contributes below, so they live here rather than in apps/registry.tsx.
const NotFound = lazyPage(() => import("./pages/NotFound"));
const AppChats = lazyPage(() => import("./pages/apps/tabs/AppChats"));
// One conversation, opened from that tab and STILL INSIDE THE APP. See the
// header of that file: this route exists because a row on an app's Chats tab
// used to navigate to `/chat/...`, which is the All chats place, so opening a
// chat left the app.
const AppChatThread = lazyPage(() => import("./pages/apps/tabs/AppChatThread"));
const AppMemoryTab = lazyPage(() => import("./pages/apps/tabs/AppMemoryTab"));
const AppFilesTab = lazyPage(() => import("./pages/apps/tabs/AppFilesTab"));
// The app-level Work view: branches, and what is on them. It is the app's
// FIRST tab (pages/apps/tabs/appTabState.ts) and therefore what an app with a
// working tree opens on, which is the 2026-08-29 / 08-31 / 09-01 report.
const AppWorkTab = lazyPage(() => import("./pages/apps/tabs/AppWorkTab"));
// Overview absorbed what ProjectDetail had: the autonomy brake, the services
// and the environment. See section 7 step 4.
const AppOverviewTab = lazyPage(() => import("./pages/apps/tabs/AppOverviewTab"));
const AppTicketsTab = lazyPage(() => import("./pages/apps/tabs/AppTicketsTab"));
const AppRunsTab = lazyPage(() => import("./pages/apps/tabs/AppRunsTab"));

// Workshop IS the homepage. Apps live INSIDE Workshop — the Apps tab lists
// them. No peer-app nav strip; the Apps tab is the discovery surface. Which
// apps exist is src/apps/registry.tsx's business, not this file's.

/** An app's base path lands on the surface the app chose.
 *
 *  This used to be a hardcoded `/chats`, which is the whole of ticket 7d8d733a:
 *  every app with no bundled UI opened on a generic chat list, because this one
 *  line decided it for all of them. The app's landing is resolved from its own
 *  declaration (backend/services/app_surface.py) and reaches here through the
 *  launcher, so it is the SAME answer the dock tile and the launcher card give.
 *
 *  It waits for that answer rather than guessing. A redirect is a one-way move:
 *  guessing `/chats` and correcting it a moment later is exactly the landing
 *  nobody chose, and the operator would be on it before the correction arrived.
 *  An app the platform does not know still falls back to its chat list, which
 *  is the one tab every app has. */
function AppHomeRedirect() {
  const { appSlug = "" } = useParams();
  const { apps, ready } = useAppPlacesState();
  if (!ready) return <RouteSkeleton />;
  const slug = encodeURIComponent(appSlug);
  const app = apps.find((a) => a.id === appSlug);
  return <Navigate to={app?.landing ?? `/apps/${slug}/chats`} replace />;
}

/** THE WORK PANE IS THE CHAT. `pages/AgentBuild.tsx` was a second chat surface
 *  wrapped around a workbench, for an entity that no longer exists: a thread is
 *  the one thing (threads-branches-and-pages-2026-08-29.md section 1), and its
 *  address is `agent:<slug>`, which `/chat/:contextKey` already opens. The
 *  workbench itself was files, an editor, a diff and a preview over one working
 *  tree, all of which the app's own Files and Work tabs serve.
 *
 *  Both old spellings land here, because a deep link in a persisted chat note
 *  is still a deep link. */
function AgentChatRedirect() {
  const { slug = "" } = useParams();
  return <Navigate to={`/chat/${encodeURIComponent(`agent:${slug}`)}`} replace />;
}

// "Job" was the legacy noun for a Run. Old /jobs/:id deep-links live in chat
// spawn notes, agent cards and ticket histories that are already persisted, so
// they keep resolving — to the canonical /runs/:id.
function WorkRedirect() {
  const { search, hash } = useLocation();
  return <Navigate to={`/tickets${search}${hash}`} replace />;
}

function JobIdRedirect() {
  const { id = "" } = useParams();
  // Through `runHref` like every other caller. This was the last hand-spelled
  // copy of the path, and it is the one place the two spellings meet, so it is
  // the worst place for the canonical target to be written out by hand again.
  return <Navigate to={runHref(id)} replace />;
}

// Voice is now an inline toggle inside the chat, not a screen. Old /voice/:key
// deep-links redirect to the one focused chat (talk from the toggle there).
function VoiceRedirect() {
  const { contextKey = "general" } = useParams();
  return <Navigate to={`/chat/${encodeURIComponent(contextKey)}`} replace />;
}

// The shell is `components/shell/Shell.tsx` now: a dock, a page column and the
// content on a desktop, two toolbars and the content on a phone. The two width
// modifiers (--chatfull, --appfull) moved there with it and are still the ONLY
// per-route width logic there is. Do not reintroduce a route-by-route list.
// A TAB THAT HAS BEEN OPEN ALL DAY IS RUNNING THE BUILD IT LOADED. The edge
// already stops a FRESH load from getting a stale bundle (index.html goes out
// no-cache with an ETag); nothing was closing the other half, so a fix could
// be committed, built, and verifiably live on the public origin while the
// operator, in an open tab, was still looking at the previous one and
// correctly saying it was not fixed. Measured 2026-09-01. No banner: it
// reloads on the next navigation and says nothing. See lib/newBuild.ts.
//
// A COMPONENT RATHER THAN A CALL IN `App`, because the read is on the query
// layer and `App` IS the component that mounts `QueryClientProvider`: a hook
// called in the body of the provider's own owner runs OUTSIDE the context it
// is about to create, and `useQuery` there throws on the first render. It
// renders nothing; it is mounted for its effect, inside the provider.
function FreshBuildWatch() {
  useFreshBuild();
  return null;
}

export default function App() {
  // WHICH APPS THIS INSTALLATION HAS, asked of the platform rather than
  // compiled in. The read starts on mount and the routed area waits for it:
  // mounting the route table first would 404 a deep link into an app for one
  // frame, and would mount the tree below any session-scoped provider an app
  // declares, which is exactly the unmount `AppProviders` cannot survive.
  // Everything else on the screen -- the shell, the nav, the chat -- renders
  // immediately, and an unreachable endpoint yields no apps rather than an
  // error thrown into render (apps/declarations.tsx).
  const apps = useAppDeclarations();
  return (
    // The query cache sits ABOVE every route, so moving between screens does
    // not throw away what was already read, and INSIDE `AuthProvider` (which
    // wraps this component in main.tsx), so every query runs with a token.
    // main.tsx mounts `BrowserRouter` above `AuthProvider`, so "outside the
    // router" can only mean above the route tree, which is what this is.
    <QueryClientProvider client={queryClient}>
    <FreshBuildWatch />
    <AppProviders>
    <BreadcrumbProvider>
    <Shell>
        <AppSuspense>
        {!apps.ready ? <RouteSkeleton /> : (
        <Routes>
          <Route path="/" element={<Home />} />
          {/* ROLES REPLACED THE AGENTS ROSTER (threads-branches-and-pages-
              2026-08-29.md sections 1, 2 and 6: "`/agents` as a roster.
              Becomes Home > Roles, plus an armed filter on Chats"). A thread is
              the one entity; "agent" is a description of a thread whose `armed`
              is not `off`, so there is no roster of a second kind of object to
              list. /agents keeps resolving, to the surface that answers what it
              used to be opened for.

              HALF OF THAT SENTENCE IS NO LONGER TRUE OF THE TREE. The filter on
              Chats is gone (2026-09-17): no thread in the catalog uses the
              field, so it could only empty the list, and the operator rejected
              the label. Roles is the whole of what replaced the roster, and it
              is where the setting is read and written. See pages/chatList. */}
          <Route path="/roles" element={<Roles />} />
          <Route path="/agents" element={<Navigate to="/roles" replace />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/tickets/:scope/:id" element={<TicketDetail />} />
          <Route path="/stores" element={<Stores />} />
          <Route path="/stores/:app/:name" element={<StoreDetail />} />
          {/* The scoped knowledge store — agent memory, not the Notes app at
              /apps/notes. Both were called "Notes", which is why neither was
              findable. This one is Memory (it is what agents read and write
              and what promotion in the memory design operates on). */}
          <Route path="/memory" element={<MemoryPage />} />
          {/* THE THREE RETIRED LISTS, kept as redirects because a bookmark is
              still a bookmark. Each goes to HOME and not to some app: a
              cross-app URL carries no app, so the only honest destination is
              the one surface that is genuinely global. Their records keep
              their own routes below, and each record page now points back at
              the app it belongs to. */}
          <Route path="/changes" element={<Navigate to="/" replace />} />
          <Route path="/deploys" element={<Navigate to="/" replace />} />
          <Route path="/runs" element={<Navigate to="/" replace />} />
          <Route path="/branches" element={<Navigate to="/chats" replace />} />
          {/* A change is the reviewable artifact a branch produces: the diff
              behind a finished ticket, before it reaches the trunk. Accepting
              the ticket is what merges it, so there is no merge route here. */}
          <Route path="/changes/:id" element={<ChangeReview />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/agents/new" element={<AgentDetail />} />
          <Route path="/agents/:slug/chat" element={<AgentChatRedirect />} />
          <Route path="/agents/build/:slug" element={<AgentChatRedirect />} />
          <Route path="/agents/:slug" element={<AgentDetail />} />
          <Route path="/chats" element={<Chats />} />
          {/* The archive is its own list, not a filter over /chats. Eager like
              Chats itself: it is one small component in the same module. */}
          <Route path="/chats/archived" element={<ChatsArchived />} />
          {/* PROJECT IS NOT AN ENTITY. Settled 2026-08-20 (app-is-the-entity),
              executed 2026-09-01 on the operator's word: "I don't know if there
              is a projects page anymore... that is sort of being phased out and
              has no place anymore." The list is the app list; a project's own
              page is that app's Overview, which absorbed the four panels that
              lived nowhere else. Both routes stay as redirects, because a
              bookmark is still a bookmark. */}
          <Route path="/projects" element={<Navigate to="/apps" replace />} />
          <Route path="/projects/new" element={<ProjectNew />} />
          <Route path="/projects/:slug" element={<ProjectSlugRedirect />} />
          <Route path="/projects/:slug/run" element={<AppPreview />} />
          <Route path="/settings/grants" element={<Grants />} />
          <Route path="/settings/trackers" element={<Navigate to="/settings/connectors" replace />} />
          {/* Tools and MCP are one page now (places.ts). The routes stay as
              redirects: a bookmark to either is still a bookmark. */}
          <Route path="/settings/mcp" element={<Navigate to="/settings/tools" replace />} />
          <Route path="/settings/models" element={<ModelProviders />} />
          <Route path="/settings/tools" element={<ToolsSettings />} />
          <Route path="/settings/instructions" element={<Navigate to="/settings/chat" replace />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/marketplace" element={<Marketplace />} />
          {/* Every app's routes come from src/apps/registry.tsx — the shell
              does not know which apps exist. */}
          {appRoutes()}
          {/* The seven per-app tabs, generic over :appSlug: the shell does not
              need to know which apps exist to route to them, only that every
              app gets the same eight rows (places.ts). */}
          {/* An app with no bundled UI still has a base path, because that is
              the URL the shell uses as its place prefix and as the stem for
              every tab (places.ts). Its own screen is the first tab. A bundled
              app's static route above wins on specificity, so this never
              shadows one. */}
          <Route path="/apps/:appSlug" element={<AppHomeRedirect />} />
          <Route path="/apps/:appSlug/work" element={<AppWorkTab />} />
          <Route path="/apps/:appSlug/overview" element={<AppOverviewTab />} />
          <Route path="/apps/:appSlug/chats" element={<AppChats />} />
          {/* The app's own chat detail. Deeper than the tab, so react-router
              ranks it ahead of the row above without either needing to know
              the other exists, and the path is built in ONE place
              (`appChatPath`, shell/places.ts) so the row, this route and the
              shell's chat-layout rule cannot drift. */}
          <Route path="/apps/:appSlug/chats/:threadId" element={<AppChatThread />} />
          <Route path="/apps/:appSlug/memory" element={<AppMemoryTab />} />
          <Route path="/apps/:appSlug/files" element={<AppFilesTab />} />
          <Route path="/apps/:appSlug/tickets" element={<AppTicketsTab />} />
          <Route path="/apps/:appSlug/runs" element={<AppRunsTab />} />
          <Route path="/chat/:contextKey" element={<FocusedChat />} />
          {/* Voice is a toggle inside the chat, not a separate screen. Old
              /voice deep-links redirect to the one focused chat (no second mount). */}
          <Route path="/voice/:contextKey" element={<VoiceRedirect />} />
          {/* Old top-level paths that apps absorbed. Each app DECLARES its own
              (`AppModule.redirects`); this file names none. Two were hardcoded
              here until 2026-09-04, which put two specific app names in the
              platform's route table. */}
          {Object.entries(appRedirects()).map(([from, to]) => (
            <Route key={from} path={from} element={<Navigate to={to} replace />} />
          ))}
          {/* Settings is a PLACE with one route per tab (see pages/Settings.tsx
              and the `settings` entry in components/shell/places.ts). All six
              share one lazy chunk, so the split costs no extra request; what it
              buys is that only the section you are looking at mounts, instead
              of all thirteen starting their polls at once. */}
          <Route path="/settings" element={<Settings tab="account" />} />
          <Route path="/settings/appearance" element={<Settings tab="appearance" />} />
          <Route path="/settings/chat" element={<Settings tab="chat" />} />
          <Route path="/settings/memory" element={<Settings tab="memory" />} />
          <Route path="/settings/connectors" element={<Settings tab="connectors" />} />
          {/* NOT a redirect. /settings/apps still exists and still renders the
              installed-app catalog; only the two connection panels moved off it. */}
          <Route path="/settings/apps" element={<Settings tab="apps" />} />
          <Route path="/settings/agents" element={<Settings tab="agents" />} />
          {/* Retired standalone routes. The "This host" tab became "Agents" on
              2026-08-20 when its GPU/broker/Ollama residency rows and the
              loopback service-probe grid were removed (a single machine's
              daemons, absent from any production deploy); the redirect keeps old
              /settings/host and /settings/base-apps links landing somewhere.
              Events was deleted on 2026-08-20 (its endpoints were edge-blocked
              and had never rendered on this origin); its redirect stays too. */}
          <Route path="/activity" element={<Navigate to="/" replace />} />
          <Route path="/settings/host" element={<Navigate to="/settings/agents" replace />} />
          <Route path="/settings/base-apps" element={<Navigate to="/settings/agents" replace />} />
          <Route path="/services" element={<Navigate to="/settings" replace />} />
          <Route path="/events" element={<Navigate to="/settings" replace />} />
          {/* "Jobs" is the legacy noun for a Run. The feed it named is gone,
              so this lands where /runs does. */}
          <Route path="/jobs" element={<Navigate to="/" replace />} />
          <Route path="/jobs/:id" element={<JobIdRedirect />} />
          {/* /work was the ticket list until 2026-08-20. ADR 0023 finished
              the rename ADR 0016 stopped halfway through: the page said
              "Tickets" over an eyebrow that said "Work", and a reader of the
              UI could not tell which noun was the product's. A plain
              <Navigate> would drop ?scope= and ?status=, and those filters
              are what the in-app links carry. */}
          <Route path="/work" element={<WorkRedirect />} />
          <Route path="/notes" element={<Navigate to="/memory" replace />} />
          {/* A URL this app does not have says so. Without this, an unmatched
              path rendered the shell with an empty main, which is the same
              picture a failed load and a broken build produce. */}
          <Route path="*" element={<NotFound />} />
        </Routes>
        )}
        </AppSuspense>
    </Shell>
    </BreadcrumbProvider>
    </AppProviders>
    </QueryClientProvider>
  );
}
