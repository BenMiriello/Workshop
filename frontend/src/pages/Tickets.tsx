// The /tickets route. The surface itself is a component
// (components/tickets/TicketsView.tsx), because an app's Tickets tab mounts the
// same one pinned to that app's scope rather than drawing a second, weaker list
// of the same rows.
//
// A ticket IS a task; every identifier stays `task` (ADR 0001).
import TicketsView from "../components/tickets/TicketsView";

export default function Tickets() {
  return <TicketsView />;
}
