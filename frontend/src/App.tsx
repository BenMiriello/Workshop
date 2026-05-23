import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Agents from "./pages/Agents";
import AgentConfig from "./pages/AgentConfig";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Finance from "./pages/apps/Finance";
import LocationApp from "./pages/apps/Location";
import Activity from "./pages/Activity";
import Settings from "./pages/Settings";

// Commute is its own app at https://debian.tail38e62e.ts.net:8444/.
// It is NOT a tab inside Workshop — apps live at their own ports. The
// Apps tab has the Commute tile; tapping it opens the standalone app.
const COMMUTE_URL = "https://debian.tail38e62e.ts.net:8444/";

function RedirectExternal({ to }: { to: string }) {
  if (typeof window !== "undefined") window.location.replace(to);
  return null;
}

// Workshop IS the homepage. Commute, Wearables, Finance, etc. are apps
// INSIDE Workshop — the Apps tab lists them. No peer-app nav strip; the
// Apps tab is the discovery surface.

export default function App() {
  return (
    <div className="app">
      <header>
        <h1>
          <NavLink to="/" className="brand-link">
            <span className="brand-full">Workshop</span>
            <span className="brand-short">W</span>
          </NavLink>
        </h1>
        <nav>
          <NavLink to="/agents">Agents</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/apps">Apps</NavLink>
          <NavLink to="/activity">Activity</NavLink>
          <NavLink to="/settings">Settings</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/agents" replace />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:agentId" element={<AgentConfig />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/apps/finance" element={<Finance />} />
          <Route path="/apps/location" element={<LocationApp />} />
          {/* Any /commute link from old bookmarks → bounce to standalone Commute app at :8444. */}
          <Route path="/commute" element={<RedirectExternal to={COMMUTE_URL} />} />
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
    </div>
  );
}
