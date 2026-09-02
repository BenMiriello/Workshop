import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { currentEntry, entryFromHtml, isStale } from "./newBuild";

// How often to ask. The request is one conditional GET of index.html, which the
// edge answers 304 from an ETag when nothing changed (workshop_public_app), so
// the steady-state cost is a few hundred bytes every five minutes.
const POLL_MS = 5 * 60 * 1000;

/**
 * Reload a tab that is running an older build, on its next navigation.
 *
 * The decisions and why they are not the obvious ones are in lib/newBuild.ts.
 * The short version: no banner, and never mid-page. A route change is already a
 * discontinuity, so reloading there costs nothing extra and cannot interrupt
 * typing or a running turn.
 */
export function useFreshBuild() {
  const { pathname } = useLocation();
  const mine = useRef<string | null>(null);
  const staleRef = useRef(false);
  const first = useRef(true);

  useEffect(() => {
    mine.current = currentEntry(document);
    // Nothing to compare against (the dev server serves no hashed entry), so
    // this does not run there at all rather than polling for an answer it can
    // never use.
    if (!mine.current) return;

    let alive = true;
    const check = async () => {
      try {
        const r = await fetch("/index.html", { cache: "no-store" });
        if (!r.ok) return;
        const latest = entryFromHtml(await r.text());
        if (alive && isStale(mine.current, latest)) staleRef.current = true;
      } catch {
        // Offline, or the edge is restarting. Not a new build; say nothing.
      }
    };

    void check();
    const t = window.setInterval(() => void check(), POLL_MS);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  useEffect(() => {
    // Not on the first render: that IS the load, and reloading there would be a
    // loop for a tab that opened while a build was mid-write.
    if (first.current) { first.current = false; return; }
    if (staleRef.current) window.location.reload();
  }, [pathname]);
}
