import JobsFeed from "../components/JobsFeed";

// The Jobs page is a thin wrapper over the shared JobsFeed component (the same
// feed the Work page's Runs tab renders). /jobs and /jobs/:id stay as routes so
// deep links from Home, Agents, and chat spawn-notes keep working.
export default function Jobs() {
  return <JobsFeed />;
}
