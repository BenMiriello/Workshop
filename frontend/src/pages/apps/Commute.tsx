import { AppGroupNav } from "../../components/appNav";
import { PageHeader } from "../../ui";
import CommutePages from "./commute/CommutePages";

// Commute is a first-class Workshop app (like Finance / Budget / Location /
// Birds) — its source lives here under pages/apps/commute. The page header +
// linked-app switcher match the other apps; CommutePages owns the tabbed body
// (Status / Schedule / Sources / Alerts).
export default function Commute() {
  return (
    <div>
      <AppGroupNav />
      <PageHeader title="Commute" />
      <CommutePages />
    </div>
  );
}
