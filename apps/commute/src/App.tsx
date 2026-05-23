import Commute from "./CommutePages";
import { ChevronLeft } from "lucide-react";

// Standalone Commute app. The actual UI is in CommutePages.tsx
// (transplanted from workshop frontend after masini's redesign — has
// real Status / Schedule / Sources / Alerts tabs instead of raw JSON).
//
// This wrapper just adds the standalone-app chrome: a back link to
// Workshop. Commute is an app WITHIN Workshop; it lives at its own
// HTTPS port (:8444) so the operator can install it as a phone PWA
// without dragging the whole Workshop frontend along.

const WORKSHOP_URL = "https://debian.tail38e62e.ts.net:8443/";

export default function App() {
  return (
    <div className="app">
      <header className="system-nav">
        <a className="brand" href={WORKSHOP_URL}>
          <ChevronLeft size={16} strokeWidth={1.8} aria-hidden /> Workshop
        </a>
        <h1 className="app-name">Commute</h1>
      </header>
      <Commute />
    </div>
  );
}
