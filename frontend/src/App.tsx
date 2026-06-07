import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Agents from "./pages/Agents";
import AgentConfig from "./pages/AgentConfig";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Finance from "./pages/apps/Finance";
import Budget from "./pages/apps/Budget";
import LocationApp from "./pages/apps/Location";
import Birds from "./pages/apps/Birds";
import Locations from "./pages/apps/birds/Locations";
import LocationDetail from "./pages/apps/birds/LocationDetail";
import Devices from "./pages/apps/birds/Devices";
import BirdDetail from "./pages/apps/birds/BirdDetail";
import BirdSettings from "./pages/apps/birds/BirdSettings";
import { RecorderProvider } from "./pages/apps/birds/recorder";
import RecorderBar from "./pages/apps/birds/RecorderBar";
import Activity from "./pages/Activity";
import Settings from "./pages/Settings";
import CommuteApp from "./pages/apps/Commute";
import { BreadcrumbBar, BreadcrumbProvider } from "./components/appNav";

// Workshop IS the homepage. Commute, Wearables, Finance, etc. are apps
// INSIDE Workshop — the Apps tab lists them. No peer-app nav strip; the
// Apps tab is the discovery surface.

export default function App() {
  // Active-link class for the ws-nav (react-router v6 gives us isActive).
  const navClass = ({ isActive }: { isActive: boolean }) => (isActive ? "is-active" : undefined);

  return (
    <RecorderProvider>
    <BreadcrumbProvider>
    <div className="ws-root ws-app">
      <header className="ws-topnav">
        <BreadcrumbBar />
        <nav className="ws-nav">
          <NavLink to="/agents" className={navClass}>Agents</NavLink>
          <NavLink to="/projects" className={navClass}>Projects</NavLink>
          <NavLink to="/apps" className={navClass}>Apps</NavLink>
          <NavLink to="/activity" className={navClass}>Activity</NavLink>
          <NavLink to="/settings" className={navClass}>Settings</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/apps" replace />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:agentId" element={<AgentConfig />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/apps/finance" element={<Finance />} />
          <Route path="/apps/budget" element={<Budget />} />
          <Route path="/apps/location" element={<LocationApp />} />
          <Route path="/apps/birds" element={<Birds />} />
          <Route path="/apps/birds/locations" element={<Locations />} />
          <Route path="/apps/birds/location" element={<LocationDetail />} />
          <Route path="/apps/birds/devices" element={<Devices />} />
          <Route path="/apps/birds/bird" element={<BirdDetail />} />
          {/* Old per-station route now resolves to the location page by name is gone;
              StationDetail is superseded by LocationDetail (by id). */}
          <Route path="/apps/birds/settings" element={<BirdSettings />} />
          <Route path="/apps/commute" element={<CommuteApp />} />
          {/* Legacy path + the old external link both land on the in-workshop app now. */}
          <Route path="/commute" element={<Navigate to="/apps/commute" replace />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/activity" element={<Activity />} />
          {/* Direct-access shortcuts kept for bookmarks/links — they redirect into Activity with the right sub-tab. */}
          <Route path="/services" element={<Navigate to="/activity#services" replace />} />
          <Route path="/events" element={<Navigate to="/activity#events" replace />} />
          <Route path="/runs" element={<Navigate to="/activity#runs" replace />} />
          {/* Old /location web-geolocation page removed. Owntracks iOS now
              posts to /api/location/owntracks; viewer lives at /apps/location. */}
          <Route path="/location" element={<Navigate to="/apps/location" replace />} />
        </Routes>
      </main>
      <RecorderBar />
    </div>
    </BreadcrumbProvider>
    </RecorderProvider>
  );
}
