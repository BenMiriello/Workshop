import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import ErrorBoundary from "./ErrorBoundary";
import "./styles.css";

// Defense-in-depth SW unregister. The dev server now serves a kill-switch sw.js
// (see vite.config.ts), but iOS Safari can be sticky about replacing an
// already-active SW. So at load time we also actively unregister any SW that
// has registered against this origin, and delete all caches that the previous
// PWA build left behind. This is a no-op once the page has been loaded clean
// at least once.
//
// We do this BEFORE rendering the React tree so that any in-flight requests
// the SW would have intercepted don't get poisoned with stale precache hits.
async function unregisterStaleServiceWorkers() {
  if (!("serviceWorker" in navigator)) return;
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map((r) => r.unregister().catch(() => {})));
  } catch {
    // ignore
  }
  if ("caches" in self) {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k).catch(() => false)));
    } catch {
      // ignore
    }
  }
}

void unregisterStaleServiceWorkers();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>,
);
