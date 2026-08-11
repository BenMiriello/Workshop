import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { OverlayHost } from "@workshop/ui";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import AgentBuild from "./pages/AgentBuild";
import Chats from "./pages/Chats";
import Projects from "./pages/Projects";
import ProjectNew from "./pages/ProjectNew";
import ProjectDetail from "./pages/ProjectDetail";
import AppPreview from "./pages/AppPreview";
import BaseApps from "./pages/settings/BaseApps";
import Grants from "./pages/settings/Grants";
import Trackers from "./pages/settings/Trackers";
import McpServers from "./pages/settings/McpServers";
import Apps from "./pages/Apps";
import Marketplace from "./pages/Marketplace";
import Home from "./pages/Home";
import Work from "./pages/Work";
import Deploys from "./pages/Deploys";
import TicketDetail from "./pages/TicketDetail";
import DeployDetail from "./pages/DeployDetail";
import StoreDetail from "./pages/StoreDetail";
import Stores from "./pages/Stores";
import MemoryPage from "./pages/Memory";
import RunDetail from "./pages/RunDetail";
import Usage from "./pages/Usage";
import Settings from "./pages/Settings";
import FocusedChat from "./pages/FocusedChat";
import { BreadcrumbBar, BreadcrumbProvider, SectionNav } from "./components/appNav";
import { AppProviders, AppSuspense, appRoutes } from "./apps/registry";

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
  // Page width is NOT decided here, for any route. Every page is .ws-app at
  // --page-max; the route-by-route width list that used to live here is what
  // made the app look like six different apps.
  // The one class below is not a width — .ws-app--chatfull keeps --page-max and
  // changes only SCROLLING: the focused chat becomes a viewport-tall column that
  // never scrolls, so the log is the only scroller and the composer stays above
  // the mobile keyboard. Do not add a width modifier next to it.
  const { pathname } = useLocation();
  const chatFull = pathname.startsWith("/chat/");
  const widthClass = chatFull ? " ws-app--chatfull" : "";
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
