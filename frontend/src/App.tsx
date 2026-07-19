import { Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
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
import Apps from "./pages/Apps";
import Marketplace from "./pages/Marketplace";
import Finance from "./pages/apps/Finance";
import Budget from "./pages/apps/Budget";
import LocationApp from "./pages/apps/Location";
import Birds from "./pages/apps/Birds";
import Locations from "./pages/apps/birds/Locations";
import LocationDetail from "./pages/apps/birds/LocationDetail";
import Devices from "./pages/apps/birds/Devices";
import BirdDetail from "./pages/apps/birds/BirdDetail";
import BirdsReview from "./pages/apps/birds/BirdsReview";
import BirdSettings from "./pages/apps/birds/BirdSettings";
import Recordings from "./pages/apps/birds/RecordingsPage";
import Atlas from "./pages/apps/birds/Atlas";
import ForecastWeek from "./pages/apps/birds/ForecastWeek";
import { BirdsScopeProvider } from "./pages/apps/birds/scope";
import BirdsShell from "./pages/apps/birds/BirdsShell";
import { RecorderProvider } from "./pages/apps/birds/recorder";
import Home from "./pages/Home";
import Work from "./pages/Work";
import Deploys from "./pages/Deploys";
import NotesPage from "./pages/Notes";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Usage from "./pages/Usage";
import Settings from "./pages/Settings";
import CommuteApp from "./pages/apps/Commute";
import Notes from "./pages/apps/notes/Notes";
import FocusedChat from "./pages/FocusedChat";
import { BreadcrumbBar, BreadcrumbProvider, SectionNav } from "./components/appNav";

// Workshop IS the homepage. Commute, Wearables, Finance, etc. are apps
// INSIDE Workshop — the Apps tab lists them. No peer-app nav strip; the
// Apps tab is the discovery surface.

// Layout route for the Birds app: provides the persistent scope and the
// responsive chrome around every birds page.
function BirdsLayout() {
  return (
    <BirdsScopeProvider>
      <BirdsShell><Outlet /></BirdsShell>
    </BirdsScopeProvider>
  );
}

// Deep-link / mobile-shell compatibility: the builder work pane moved from
// /agents/build/:slug to /agents/:slug/chat. Keep old links working.
function AgentBuildRedirect() {
  const { slug = "" } = useParams();
  return <Navigate to={`/agents/${encodeURIComponent(slug)}/chat`} replace />;
}

// Voice is now an inline toggle inside the chat, not a screen. Old /voice/:key
// deep-links redirect to the one focused chat (talk from the toggle there).
function VoiceRedirect() {
  const { contextKey = "general" } = useParams();
  return <Navigate to={`/chat/${encodeURIComponent(contextKey)}`} replace />;
}

export default function App() {
  // Width by page type. Content/app pages keep the reading-width cap (980).
  // The builder is a 3-pane IDE and needs the full viewport (wide). The
  // launcher/dashboard grids (apps, projects, agents, marketplace) look sparse
  // at 980 on wide screens, so they get a roomier cap. The Birds app needs the
  // room too (sidebar + content), and Activity/Settings were inconsistently
  // narrow — so they share the roomy cap. (finance/budget/location/commute stay
  // at 980 by choice; an app may impose its own column.)
  const { pathname } = useLocation();
  const wide = /^\/agents\/[^/]+\/chat$/.test(pathname);
  // The focused chat is a full-screen surface: the shell becomes a viewport-tall
  // flex column that never scrolls, so the chat log is the only scroller and the
  // composer pins to the bottom (above the mobile keyboard). See .ws-app--chatfull.
  const chatFull = pathname.startsWith("/chat/");
  const roomy =
    !wide && !chatFull &&
    (pathname === "/" ||
      pathname === "/apps" ||
      pathname === "/projects" ||
      pathname.startsWith("/projects/") ||
      pathname === "/agents" ||
      pathname.startsWith("/jobs") ||
      pathname === "/chats" ||
      pathname === "/marketplace" ||
      pathname.startsWith("/apps/birds") ||
      pathname === "/apps/notes" ||
      pathname === "/settings");
  const widthClass = chatFull
    ? " ws-app--chatfull"
    : wide ? " ws-app--wide" : roomy ? " ws-app--roomy" : "";
  return (
    <RecorderProvider>
    <BreadcrumbProvider>
    <div className={"ws-root ws-app" + widthClass}>
      <header className="ws-topnav">
        <BreadcrumbBar />
        <SectionNav />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/work" element={<Work />} />
          <Route path="/deploys" element={<Deploys />} />
          <Route path="/notes" element={<NotesPage />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
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
          <Route path="/apps" element={<Apps />} />
          <Route path="/marketplace" element={<Marketplace />} />
          <Route path="/apps/finance" element={<Finance />} />
          <Route path="/apps/budget" element={<Budget />} />
          <Route path="/apps/location" element={<LocationApp />} />
          <Route path="/apps/notes" element={<Notes />} />
          {/* Birds runs inside the responsive Birds shell (sidebar / mobile tabs)
              with the persistent Location·Device·Time scope. The Workshop topnav
              above stays the outer nav level; this is the inner one. */}
          <Route element={<BirdsLayout />}>
            <Route path="/apps/birds" element={<Birds />} />
            <Route path="/apps/birds/birds" element={<BirdsReview />} />
            <Route path="/apps/birds/locations" element={<Locations />} />
            <Route path="/apps/birds/location" element={<LocationDetail />} />
            <Route path="/apps/birds/devices" element={<Devices />} />
            <Route path="/apps/birds/bird" element={<BirdDetail />} />
            {/* Old per-station route now resolves to the location page by name is gone;
                StationDetail is superseded by LocationDetail (by id). */}
            <Route path="/apps/birds/settings" element={<BirdSettings />} />
            <Route path="/apps/birds/recordings" element={<Recordings />} />
            <Route path="/apps/birds/atlas" element={<Atlas />} />
            <Route path="/apps/birds/forecast" element={<ForecastWeek />} />
          </Route>
          <Route path="/apps/commute" element={<CommuteApp />} />
          <Route path="/chat/:contextKey" element={<FocusedChat />} />
          {/* Voice is a toggle inside the chat, not a separate screen. Old
              /voice deep-links redirect to the one focused chat (no second mount). */}
          <Route path="/voice/:contextKey" element={<VoiceRedirect />} />
          {/* Legacy path + the old external link both land on the in-workshop app now. */}
          <Route path="/commute" element={<Navigate to="/apps/commute" replace />} />
          <Route path="/settings" element={<Settings />} />
          {/* Activity retired: Jobs live at /jobs (under Agents), Services/Events
              moved into Settings > System. Old URLs redirect to their new homes. */}
          <Route path="/activity" element={<Navigate to="/" replace />} />
          <Route path="/services" element={<Navigate to="/settings" replace />} />
          <Route path="/events" element={<Navigate to="/settings" replace />} />
          <Route path="/runs" element={<Navigate to="/jobs" replace />} />
          {/* Old /location web-geolocation page removed. Owntracks iOS now
              posts to /api/location/owntracks; viewer lives at /apps/location. */}
          <Route path="/location" element={<Navigate to="/apps/location" replace />} />
        </Routes>
      </main>
    </div>
    </BreadcrumbProvider>
    </RecorderProvider>
  );
}
