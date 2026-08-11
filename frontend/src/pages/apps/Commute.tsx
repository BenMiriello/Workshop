import { AppGroupNav } from "../../components/appNav";
import { PageHeader } from "@workshop/ui";
import CommutePages from "./commute/CommutePages";
import AppChatDock from "../../components/AppChatDock";
import { AppSettings } from "../../components/AppSettings";
import CommuteSettings from "../../components/appSettings/CommuteSettings";
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
      <AppSettings>
        <CommuteSettings />
      </AppSettings>
      <AppChatDock appId="commute" title="Commute assistant" placeholder="Ask about departures or transit status…" />
    </div>
  );
}
