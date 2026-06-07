// In-workshop Commute mount. Renders the SAME component as the standalone
// Commute app (apps/commute, served at :8444) via the @commute alias — no fork,
// so the schedule/anchor/scoring logic lives in exactly one place. The imported
// default export is the tab shell (Status / Schedule / Sources / Alerts) without
// the standalone "← Workshop" chrome, which is what we want inside the shell.
import CommutePages from "@commute/CommutePages";

export default function Commute() {
  return <CommutePages />;
}
