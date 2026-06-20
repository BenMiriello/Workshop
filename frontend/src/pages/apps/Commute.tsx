import { AppGroupNav } from "../../components/appNav";
import { PageHeader } from "../../ui";
import CommutePages from "./commute/CommutePages";
import AppChatDock from "../../components/AppChatDock";
import { useCommuteNative } from "../../native/commute";

// Commute is a first-class Workshop app (like Finance / Budget / Location /
// Birds) — its source lives here under pages/apps/commute. The page header +
// linked-app switcher match the other apps; CommutePages owns the tabbed body
// (Status / Schedule / Sources / Alerts).
export default function Commute() {
  // In the mobile shell: schedule local "leave now" notifications + drive the
  // lock-screen Live Activity countdown from the commute brief. No-op in a browser.
  useCommuteNative();

  return (
    <div>
      <AppGroupNav />
      <PageHeader title="Commute" />
      <CommutePages />
      <AppChatDock appId="commute" title="Commute assistant" placeholder="Ask about departures or transit status…" />
    </div>
  );
}
