// Run history. Its own address, not a tab on the ticket list.
//
// A run is what a ticket DID, so the runs that matter are on the ticket page.
// This page is the rest: sweeps, agent turns and cron work that no ticket owns,
// which would have nowhere to appear if the feed were only ever a ticket's
// child. It is reachable from a ticket ("All runs") and from Settings, and it is
// not in the primary nav, because "what ran" is a question you ask second.
import { PageHeader } from "@workshop/ui";
import JobsFeed from "../components/JobsFeed";

export default function Runs() {
  return (
    <div>
      <PageHeader title="Runs" sub="Every execution, including work no ticket owns." />
      <JobsFeed />
    </div>
  );
}
