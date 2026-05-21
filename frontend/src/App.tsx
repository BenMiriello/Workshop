import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Agents from "./pages/Agents";
import AgentConfig from "./pages/AgentConfig";
import Projects from "./pages/Projects";
import Apps from "./pages/Apps";
import Activity from "./pages/Activity";

export default function App() {
  return (
    <div className="app">
      <header>
        <h1><span className="brand-full">Workshop</span><span className="brand-short">W</span></h1>
        <nav>
          <NavLink to="/agents">Agents</NavLink>
          <NavLink to="/projects">Projects</NavLink>
          <NavLink to="/apps">Apps</NavLink>
          <NavLink to="/activity">Activity</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/agents" replace />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/agents/:agentId" element={<AgentConfig />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/apps" element={<Apps />} />
          <Route path="/activity" element={<Activity />} />
          {/* Direct-access shortcuts kept for bookmarks/links — they redirect into Activity with the right sub-tab. */}
          <Route path="/services" element={<Navigate to="/activity#services" replace />} />
          <Route path="/events" element={<Navigate to="/activity#events" replace />} />
          <Route path="/runs" element={<Navigate to="/activity#runs" replace />} />
        </Routes>
      </main>
    </div>
  );
}
