import { lazyPage } from "./lib/lazyPage";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { OverlayHost } from "@workshop/ui";
import Agents from "./pages/Agents";
import Chats from "./pages/Chats";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Home from "./pages/Home";
import Work from "./pages/Work";
import Deploys from "./pages/Deploys";
import TicketDetail from "./pages/TicketDetail";
import FocusedChat from "./pages/FocusedChat";
import { BreadcrumbBar, BreadcrumbProvider, SectionNav } from "./components/appNav";
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
const BaseApps = lazyPage(() => import("./pages/settings/BaseApps"));
const Grants = lazyPage(() => import("./pages/settings/Grants"));
const Trackers = lazyPage(() => import("./pages/settings/Trackers"));
const McpServers = lazyPage(() => import("./pages/settings/McpServers"));
const Marketplace = lazyPage(() => import("./pages/Marketplace"));
const DeployDetail = lazyPage(() => import("./pages/DeployDetail"));
const StoreDetail = lazyPage(() => import("./pages/StoreDetail"));
const Stores = lazyPage(() => import("./pages/Stores"));
const MemoryPage = lazyPage(() => import("./pages/Memory"));
const RunDetail = lazyPage(() => import("./pages/RunDetail"));
const Usage = lazyPage(() => import("./pages/Usage"));
const Settings = lazyPage(() => import("./pages/Settings"));

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
function JobIdRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={`/runs/${encodeURIComponent(id)}`} replace />;
}

// Voice is now an inline toggle inside the chat, not a screen. Old /voice/:key
// deep-links redirect to the one focused chat (talk from the toggle there).
function VoiceRedirect() {
  const { contextKey = "general" } = useParams();
  return <Navigate to={`/chat/${encodeURIComponent(contextKey)}`} replace />;
}

export default function App() {
  // Two shell modifiers, and they are the ONLY per-route width logic there is.
  // Regular pages need no class: <main> caps their content at --page-max on its
  // own. Do not reintroduce the route-by-route width list that used to live here.
  //   - ws-app--chatfull: the focused chat. A viewport-tall column that never
  //     scrolls (the log is the only scroller, composer above the mobile
  //     keyboard). Width is handled inside, on .chat-panel--full.
  //   - ws-app--appfull: an APP route (/apps/<name>, or an app running in
  //     preview). Drops the content cap so the app owns its own width; the app
  //     opts back into the page measure with `.ws-page` if it wants it.
  const { pathname } = useLocation();
  const chatFull = pathname.startsWith("/chat/");
  const appFull = !chatFull &&
    (/^\/apps\/[^/]/.test(pathname) || /^\/projects\/[^/]+\/run/.test(pathname));
  const widthClass = chatFull ? " ws-app--chatfull" : appFull ? " ws-app--appfull" : "";
  return (
    <AppProviders>
    <BreadcrumbProvider>
    <div className={"ws-root ws-app" + widthClass}>
      <header className="ws-topnav">
        <BreadcrumbBar />
        <SectionNav />
      </header>
      {/* The content region, and the only place an overlay may land. Dialogs
          portal into the host this renders, so they cover the app and not the
          top nav above it — that scoping is the shell's job, and no page or
          dialog has to know about it. See ui/OverlayHost. */}
      <OverlayHost>
      <main>
        <AppSuspense>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/work" element={<Work />} />
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
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/agents/new" element={<AgentDetail />} />
          <Route path="/agents/:slug/chat" element={<AgentBuild />} />
          <Route path="/agents/build/:slug" element={<AgentBuildRedirect />} />
          <Route path="/agents/:slug" element={<AgentDetail />} />
          <Route path="/chats" element={<Chats />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/new" element={<ProjectNew />} />
          <Route path="/projects/:slug" element={<ProjectDetail />} />
          <Route path="/projects/:slug/run" element={<AppPreview />} />
          <Route path="/settings/base-apps" element={<BaseApps />} />
          <Route path="/settings/grants" element={<Grants />} />
          <Route path="/settings/trackers" element={<Trackers />} />
          <Route path="/settings/mcp" element={<McpServers />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/marketplace" element={<Marketplace />} />
          {/* Every app's routes come from src/apps/registry.tsx — the shell
              does not know which apps exist. */}
          {appRoutes()}
          <Route path="/chat/:contextKey" element={<FocusedChat />} />
          {/* Voice is a toggle inside the chat, not a separate screen. Old
              /voice deep-links redirect to the one focused chat (no second mount). */}
          <Route path="/voice/:contextKey" element={<VoiceRedirect />} />
          {/* Legacy path + the old external link both land on the in-workshop app now. */}
          <Route path="/commute" element={<Navigate to="/apps/commute" replace />} />
          <Route path="/settings" element={<Settings />} />
          {/* Retired surfaces. Services/Events moved into Settings > System and
              their page files are deleted. "Jobs" was the legacy noun for a Run
              (entity model section 5) and had its own list page that rendered
              the SAME JobsFeed component as Work's Runs tab — one surface at two
              addresses. The list is Work's Runs tab; the detail is /runs/:id. */}
          <Route path="/activity" element={<Navigate to="/" replace />} />
          <Route path="/services" element={<Navigate to="/settings" replace />} />
          <Route path="/events" element={<Navigate to="/settings" replace />} />
          <Route path="/jobs" element={<Navigate to="/work?tab=runs" replace />} />
          <Route path="/jobs/:id" element={<JobIdRedirect />} />
          <Route path="/notes" element={<Navigate to="/memory" replace />} />
          {/* Old /location web-geolocation page removed. Owntracks iOS now
              posts to /api/location/owntracks; viewer lives at /apps/location. */}
          <Route path="/location" element={<Navigate to="/apps/location" replace />} />
        </Routes>
        </AppSuspense>
      </main>
      </OverlayHost>
    </div>
    </BreadcrumbProvider>
    </AppProviders>
  );
}
