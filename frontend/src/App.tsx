import { lazyPage } from "./lib/lazyPage";
import { runHref } from "./lib/jobs";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import Agents from "./pages/Agents";
import Chats, { ChatsArchived } from "./pages/Chats";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Home from "./pages/Home";
import Tickets from "./pages/Tickets";
import Deploys from "./pages/Deploys";
import TicketDetail from "./pages/TicketDetail";
import FocusedChat from "./pages/FocusedChat";
import { BreadcrumbProvider } from "@workshop/ui";
import { Shell } from "./components/shell/Shell";
import { AppProviders, AppSuspense, appRoutes } from "./apps/registry";

// LAZY BY ROUTE. Each of these is a page the app does not need to render its
// first screen, and several drag heavy libraries behind them (Usage pulls
// recharts). Eager imports put all of it in the main chunk, which every
// visitor downloads before anything appears. Every route below already
// renders inside <AppSuspense>, so this needs no new boundary.
//
// Keep Home, Work, Agents, Apps, Chats and FocusedChat EAGER: those are the
// landing surfaces, and a suspense flash on the way into the chat is worse
// than the bytes.
const AgentDetail = lazyPage(() => import("./pages/AgentDetail"));
const AgentBuild = lazyPage(() => import("./pages/AgentBuild"));
const ProjectNew = lazyPage(() => import("./pages/ProjectNew"));
const ProjectDetail = lazyPage(() => import("./pages/ProjectDetail"));
const AppPreview = lazyPage(() => import("./pages/AppPreview"));
const Grants = lazyPage(() => import("./pages/settings/Grants"));
const Trackers = lazyPage(() => import("./pages/settings/Trackers"));
const McpServers = lazyPage(() => import("./pages/settings/McpServers"));
const CustomInstructions = lazyPage(() => import("./pages/settings/CustomInstructions"));
const Tools = lazyPage(() => import("./pages/settings/Tools"));
const Marketplace = lazyPage(() => import("./pages/Marketplace"));
const DeployDetail = lazyPage(() => import("./pages/DeployDetail"));
const StoreDetail = lazyPage(() => import("./pages/StoreDetail"));
const Stores = lazyPage(() => import("./pages/Stores"));
const MemoryPage = lazyPage(() => import("./pages/Memory"));
const Runs = lazyPage(() => import("./pages/Runs"));
// The review surface (changes-and-review-2026-08-22.md step C4). Lazy: a
// change page drags a whole patch renderer, and neither screen is on the way
// into the app.
const Changes = lazyPage(() => import("./pages/Changes"));
const ChangeReview = lazyPage(() => import("./pages/ChangeReview"));
const RunDetail = lazyPage(() => import("./pages/RunDetail"));
const Usage = lazyPage(() => import("./pages/Usage"));
const Settings = lazyPage(() => import("./pages/Settings"));

// The seven per-app tabs (app-is-the-entity-2026-08-20.md sec 9 stage C).
// Generic over `:appSlug` -- these name no app, unlike the routes appRoutes()
// contributes below, so they live here rather than in apps/registry.tsx.
const AppChats = lazyPage(() => import("./pages/apps/tabs/AppChats"));
const AppAgentsTab = lazyPage(() => import("./pages/apps/tabs/AppAgentsTab"));
const AppMemoryTab = lazyPage(() => import("./pages/apps/tabs/AppMemoryTab"));
const AppFilesTab = lazyPage(() => import("./pages/apps/tabs/AppFilesTab"));
const AppTicketsTab = lazyPage(() => import("./pages/apps/tabs/AppTicketsTab"));
const AppRunsTab = lazyPage(() => import("./pages/apps/tabs/AppRunsTab"));
const AppDeploysTab = lazyPage(() => import("./pages/apps/tabs/AppDeploysTab"));

// Workshop IS the homepage. Apps live INSIDE Workshop — the Apps tab lists
// them. No peer-app nav strip; the Apps tab is the discovery surface. Which
// apps exist is src/apps/registry.tsx's business, not this file's.

// Deep-link / mobile-shell compatibility: the builder work pane moved from
// /agents/build/:slug to /agents/:slug/chat. Keep old links working.
function AgentBuildRedirect() {
  const { slug = "" } = useParams();
  return <Navigate to={`/agents/${encodeURIComponent(slug)}/chat`} replace />;
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
export default function App() {
  return (
    <AppProviders>
    <BreadcrumbProvider>
    <Shell>
        <AppSuspense>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/tickets" element={<Tickets />} />
          <Route path="/deploys" element={<Deploys />} />
          <Route path="/tickets/:scope/:id" element={<TicketDetail />} />
          <Route path="/deploys/:id" element={<DeployDetail />} />
          <Route path="/stores" element={<Stores />} />
          <Route path="/stores/:app/:name" element={<StoreDetail />} />
          {/* The scoped knowledge store — agent memory, not the Notes app at
              /apps/notes. Both were called "Notes", which is why neither was
              findable. This one is Memory (it is what agents read and write
              and what promotion in the memory design operates on). */}
          <Route path="/memory" element={<MemoryPage />} />
          {/* A change is the reviewable artifact a branch produces: the diff
              behind a finished ticket, before it reaches the trunk. Accepting
              the ticket is what merges it, so there is no merge route here. */}
          <Route path="/changes" element={<Changes />} />
          <Route path="/changes/:id" element={<ChangeReview />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/agents/new" element={<AgentDetail />} />
          <Route path="/agents/:slug/chat" element={<AgentBuild />} />
          <Route path="/agents/build/:slug" element={<AgentBuildRedirect />} />
          <Route path="/agents/:slug" element={<AgentDetail />} />
          <Route path="/chats" element={<Chats />} />
          {/* The archive is its own list, not a filter over /chats. Eager like
              Chats itself: it is one small component in the same module. */}
          <Route path="/chats/archived" element={<ChatsArchived />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/new" element={<ProjectNew />} />
          <Route path="/projects/:slug" element={<ProjectDetail />} />
          <Route path="/projects/:slug/run" element={<AppPreview />} />
          <Route path="/settings/grants" element={<Grants />} />
          <Route path="/settings/trackers" element={<Trackers />} />
          <Route path="/settings/mcp" element={<McpServers />} />
          <Route path="/settings/instructions" element={<CustomInstructions />} />
          <Route path="/settings/tools" element={<Tools />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/marketplace" element={<Marketplace />} />
          {/* Every app's routes come from src/apps/registry.tsx — the shell
              does not know which apps exist. */}
          {appRoutes()}
          {/* The seven per-app tabs, generic over :appSlug: the shell does not
              need to know which apps exist to route to them, only that every
              app gets the same eight rows (places.ts). */}
          <Route path="/apps/:appSlug/chats" element={<AppChats />} />
          <Route path="/apps/:appSlug/agents" element={<AppAgentsTab />} />
          <Route path="/apps/:appSlug/memory" element={<AppMemoryTab />} />
          <Route path="/apps/:appSlug/files" element={<AppFilesTab />} />
          <Route path="/apps/:appSlug/tickets" element={<AppTicketsTab />} />
          <Route path="/apps/:appSlug/runs" element={<AppRunsTab />} />
          <Route path="/apps/:appSlug/deploys" element={<AppDeploysTab />} />
          <Route path="/chat/:contextKey" element={<FocusedChat />} />
          {/* Voice is a toggle inside the chat, not a separate screen. Old
              /voice deep-links redirect to the one focused chat (no second mount). */}
          <Route path="/voice/:contextKey" element={<VoiceRedirect />} />
          {/* Legacy path + the old external link both land on the in-workshop app now. */}
          <Route path="/commute" element={<Navigate to="/apps/commute" replace />} />
          {/* Settings is a PLACE with one route per tab (see pages/Settings.tsx
              and the `settings` entry in components/shell/places.ts). All six
              share one lazy chunk, so the split costs no extra request; what it
              buys is that only the section you are looking at mounts, instead
              of all thirteen starting their polls at once. */}
          <Route path="/settings" element={<Settings tab="account" />} />
          <Route path="/settings/appearance" element={<Settings tab="appearance" />} />
          <Route path="/settings/chat" element={<Settings tab="chat" />} />
          <Route path="/settings/memory" element={<Settings tab="memory" />} />
          <Route path="/settings/apps" element={<Settings tab="apps" />} />
          <Route path="/settings/agents" element={<Settings tab="agents" />} />
          {/* Retired standalone routes. The "This host" tab became "Agents" on
              2026-08-20 when its GPU/broker/Ollama residency rows and the
              loopback service-probe grid were removed (a single machine's
              daemons, absent from any production deploy); the redirect keeps old
              /settings/host and /settings/base-apps links landing somewhere.
              Events was deleted on 2026-08-20 (its endpoints were edge-blocked
              and had never rendered on this origin); its redirect stays too.
              "Jobs" is the legacy noun for
              a Run (entity model section 5). The Runs feed was a tab on the
              ticket list until 2026-08-12; it is /runs now, because a run
              belongs to the ticket that produced it and the standalone feed
              is the leftovers, not a peer of the ticket list. */}
          <Route path="/activity" element={<Navigate to="/" replace />} />
          <Route path="/settings/host" element={<Navigate to="/settings/agents" replace />} />
          <Route path="/settings/base-apps" element={<Navigate to="/settings/agents" replace />} />
          <Route path="/services" element={<Navigate to="/settings" replace />} />
          <Route path="/events" element={<Navigate to="/settings" replace />} />
          <Route path="/jobs" element={<Navigate to="/runs" replace />} />
          <Route path="/jobs/:id" element={<JobIdRedirect />} />
          {/* /work was the ticket list until 2026-08-20. ADR 0023 finished
              the rename ADR 0016 stopped halfway through: the page said
              "Tickets" over an eyebrow that said "Work", and a reader of the
              UI could not tell which noun was the product's. A plain
              <Navigate> would drop ?scope= and ?status=, and those filters
              are what the in-app links carry. */}
          <Route path="/work" element={<WorkRedirect />} />
          <Route path="/notes" element={<Navigate to="/memory" replace />} />
          {/* Old /location web-geolocation page removed. Owntracks iOS now
              posts to /api/location/owntracks; viewer lives at /apps/location. */}
          <Route path="/location" element={<Navigate to="/apps/location" replace />} />
        </Routes>
        </AppSuspense>
    </Shell>
    </BreadcrumbProvider>
    </AppProviders>
  );
}
