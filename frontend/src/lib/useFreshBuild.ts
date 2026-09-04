import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { currentEntry, entryFromHtml, isStale } from "./newBuild";
import { queryKeys } from "./queryKeys";

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
 *
 * ON THE QUERY LAYER, not a `setInterval`. This shipped as a hand-rolled timer
 * around a `fetch`, which is the legacy mechanism ADR 0025 exists to retire and
 * `check_frontend_state.py` counts: it was the one site that pushed
 * `raw_poll_intervals` back up after the ratchet was pinned. The cadence, the
 * retry policy and the wake-from-hidden catch-up are the client's defaults now
 * rather than three decisions restated here.
 */
export function useFreshBuild() {
  const { pathname } = useLocation();
  // THE ENTRY THIS TAB BOOTED ON, read once. It is a fact about the document
  // that loaded, so it must not move when the poll answers -- and reading it
  // lazily into a ref keeps it out of the query's own lifecycle, where an
  // `enabled` flip would re-evaluate it.
  const mine = useRef<string | null | undefined>(undefined);
  if (mine.current === undefined) mine.current = currentEntry(document);
  const booted = mine.current;

  const { data: latest } = useQuery({
    queryKey: queryKeys.build.entry(),
    queryFn: async (): Promise<string | null> => {
      const r = await fetch("/index.html", { cache: "no-store" });
      if (!r.ok) throw new Error(`${r.status}`);
      return entryFromHtml(await r.text());
    },
    // Nothing to compare against (the dev server serves no hashed entry), so
    // this does not run there at all rather than polling for an answer it can
    // never use.
    enabled: !!booted,
    refetchInterval: POLL_MS,
    // The read owns its own cadence, so a staleness window over it would only
    // swallow the catch-up read on the way back from hidden -- and that read is
    // the one that matters most here. A tab restored from the app switcher
    // after two days is the likeliest tab in the app to be running an old
    // build, so the client's focus/visibility default is kept rather than
    // turned off at the call site.
    staleTime: 0,
    // Offline, or the edge restarting, is not a new build and says nothing.
    // The next tick asks again; retrying would put extra requests in front of
    // a cadence that already re-reads on its own.
    retry: false,
  });

  // ONCE STALE, ALWAYS STALE. A tab that has seen a newer build reloads on its
  // next navigation however long that takes, so a later answer must never be
  // able to un-stale it -- which a value derived from `data` alone would, the
  // moment a failed read or a cache eviction left `latest` undefined.
  const staleRef = useRef(false);
  useEffect(() => {
    if (isStale(booted, latest ?? null)) staleRef.current = true;
  }, [booted, latest]);

  const first = useRef(true);
  useEffect(() => {
    // Not on the first render: that IS the load, and reloading there would be a
    // loop for a tab that opened while a build was mid-write.
    if (first.current) { first.current = false; return; }
    if (staleRef.current) window.location.reload();
  }, [pathname]);
}
