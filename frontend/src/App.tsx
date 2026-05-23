import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Agents from "./pages/Agents";
import AgentConfig from "./pages/AgentConfig";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Finance from "./pages/apps/Finance";
import LocationApp from "./pages/apps/Location";
import Activity from "./pages/Activity";

// Workshop top nav. Companion apps (Commute, Open Wearables) live on their
// own Tailscale Serve ports — each is a peer, not a sub-path. The launcher
// at https://debian.tail38e62e.ts.net/ lists all three as equals.
const COMMUTE_URL = "https://debian.tail38e62e.ts.net:8444/";
const OW_URL = "https://debian.tail38e62e.ts.net:8445/";
const LAUNCHER_URL = "https://debian.tail38e62e.ts.net/";

export default function App() {
  return (
    <div className="app">
      <header>
        <h1>
          <a href={LAUNCHER_URL} className="brand-link" title="Launcher">
            <span className="brand-full">Workshop</span>
            <span className="brand-short">W</span>
          </a>
        </h1>
        <nav>
          <NavLink to="/agents">Agents</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/apps">Apps</NavLink>
          <NavLink to="/activity">Activity</NavLink>
        </nav>
        <nav className="peer-apps">
          <a href={COMMUTE_URL}>Commute ↗</a>
          <a href={OW_URL}>Wearables ↗</a>
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
