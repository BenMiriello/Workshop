import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useShowNonBird } from "./settings";
import {
  birdsKeys, fetchDevices, fetchHome, fetchLocations, fetchSummary, type HomeRead,
} from "./queries";

// The Birds app's single persistent scope: Location · Device · Time, applied on
// every page (including bird detail). It replaces the per-page pickers the home
// dashboard used to own. Backed by URL params (`source`, `device`, `days`) so a
// scoped view is shareable and survives reloads; the shell renders the pickers.
//
// Defaults: the home location + its devices (Yard · Yard Mic when present), the
// longer 30-day window — so the live feed and "who's here now" are present
// immediately rather than an ambiguous "everything".
//
// FOUR FACTS, FOUR CACHE ENTRIES, AND NO POLL. This provider is mounted for the
// whole Birds section, and it used to run ONE `usePoll` that fired THREE reads
// (`/locations`, `/devices`, `/summary`) into three `useState`s with a swallowed
// error on each -- three requests every eight seconds on every page of the app,
// whether or not anything on screen was reading them, and a backend that had
// been down for ten minutes looked exactly like an app where nothing had
// happened.
//
// They are now four queries on the keys in `queries.ts`, which are THE SAME
// ENTRIES the Locations page, the Devices page and the location detail page
// read. Landing on the same keys is the point: the devices list was being polled
// twice over, at 8s here and 15s there, and the two copies could disagree.
//
// NONE OF THEM IS A LIVE FEED, so none of them gets that 8s cadence back:
//
//   * locations and the yard summary decide what the scope picker OFFERS (which
//     places exist, and whether there is an always-on yard at all). A place
//     appears when a device first reports from somewhere new, which is a minutes
//     scale event, so they refresh on the slow cadence below and on the way back
//     to the tab.
//   * devices and home change only when someone changes them. A write
//     invalidates them; nothing else needs to ask.
//
// The pages that DO want liveness (the locations list's counts, the devices
// list's "recording now" dot) keep their own faster cadence over the same
// entries, so opening one of those pages still gives it, and closing it stops
// paying for it.

export type Loc = { id: number; name: string; detections: number; species: number; devices: string[] };
export type KnownDevice = { token: string; name: string };
export type Source = { key: string; name: string; kind: "yard" | "loc"; id?: number };

// Sentinel value used by the place picker to jump to the full Locations list
// rather than select a source.
export const ALL_LOCS = "__all_locations__";

const DEFAULT_DAYS = 30;
// What the scope picker offers moves on the scale of "a device reported from
// somewhere new", not on the scale of a detection landing. Slower than the 8s
// poll this replaces by a factor of seven, and it covers the one case focus
// alone does not: sitting on the dashboard while the first detections arrive.
const SCOPE_REFRESH_MS = 60_000;
// Offered analytical windows. "Today" maps to a 1-day rolling window so it works
// against the existing days=N endpoints without a backend change.
export const TIME_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: "Today" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

// Stable empties, so a consumer that depends on one of these lists does not see
// a new array identity on every render before the first response lands.
const NO_LOCS: Loc[] = [];
const NO_DEVICES: KnownDevice[] = [];

type ScopeValue = {
  loading: boolean;
  locs: Loc[];
  devices: KnownDevice[];
  home: string | null;
  yardAvailable: boolean;
  sources: Source[];
  selected: Source | null;
  deviceTok: string;
  days: number;
  /**
   * The non-bird display filter, as a BOOLEAN: what a query key and a fetcher
   * take (`queries.ts`).
   *
   * It used to be handed out as `nb`, the raw URL suffix
   * `"&include_nonbird=true"`, which every reader concatenated into a
   * hand-built URL. That is how one read came to be spelled three ways, and a
   * suffix in a cache key would have put URL encoding inside the cache. The
   * suffix is gone; `nonBirdQuery()` in `settings.ts` has no callers left.
   */
  includeNonBird: boolean;
  setSource: (key: string) => void;
  setDevice: (tok: string) => void;
  setDays: (d: number) => void;
  reset: () => void;
};

const Ctx = createContext<ScopeValue | null>(null);

export function BirdsScopeProvider({ children }: { children: React.ReactNode }) {
  const [params, setParams] = useSearchParams();
  const [showNonBird] = useShowNonBird();
  // The one shape a key and a fetcher both take, built once from the preference.
  const filter = { includeNonBird: showNonBird };

  // The places. Same entry as the Locations page (which drives it faster while
  // it is open) and the Devices page's pinned-location menu.
  const locsQ = useQuery({
    queryKey: birdsKeys.locations(filter),
    queryFn: () => fetchLocations<{ locations: Loc[] }>(filter),
    refetchInterval: SCOPE_REFRESH_MS,
    retry: false,
  });

  // The devices behind the device picker. Same entry as the Devices page.
  const devicesQ = useQuery({
    queryKey: birdsKeys.devices(),
    queryFn: () => fetchDevices<{ devices: KnownDevice[] }>(),
    retry: false,
  });

  // Is there an always-on yard feed at all. Asked with the non-bird filter OFF
  // because the question is "does this feed exist", not "how many birds are in
  // it" -- which is also why it is a different cache entry from the dashboard's
  // summary whenever that filter is on.
  const yardQ = useQuery({
    queryKey: birdsKeys.summary({ includeNonBird: false }),
    queryFn: () => fetchSummary<{ available: boolean; total_detections: number }>({ includeNonBird: false }),
    refetchInterval: SCOPE_REFRESH_MS,
    retry: false,
  });

  // WHICH PLACE IS HOME COMES FROM `/home`, not from the `home` field inside the
  // `/locations` payload it used to be read off. Home is what the scope falls
  // back to when no `?source=` is set, so a list refetch landing on top of an
  // optimistic star toggle did not just flicker a star, it could move the whole
  // app's selected place. One fact, one entry, and `useSetHome` keeps that entry
  // right (`queries.ts`).
  const homeQ = useQuery({ queryKey: birdsKeys.home(), queryFn: () => fetchHome<HomeRead>() });

  const locs = locsQ.data?.locations ?? NO_LOCS;
  const devices = devicesQ.data?.devices ?? NO_DEVICES;
  const home = homeQ.data?.home ?? null;
  const yard = yardQ.data;
  const yardAvailable = !!yard?.available && yard.total_detections > 0;

  const sources = useMemo<Source[]>(() => {
    const out: Source[] = [];
    if (yardAvailable) out.push({ key: "yard", name: "Yard (always-on)", kind: "yard" });
    for (const L of locs) out.push({ key: `loc:${L.id}`, name: L.name, kind: "loc", id: L.id });
    return out;
  }, [yardAvailable, locs]);

  const selKey = params.get("source") || home || sources[0]?.key || "";
  const selected = sources.find((s) => s.key === selKey) || sources[0] || null;
  const deviceTok = params.get("device") || "";
  const days = Number(params.get("days")) || DEFAULT_DAYS;

  const setParam = useCallback((updates: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(updates)) {
      if (v) p.set(k, v); else p.delete(k);
    }
    setParams(p);
  }, [params, setParams]);

  const setSource = useCallback((key: string) => setParam({ source: key, device: null }), [setParam]);
  const setDevice = useCallback((tok: string) => setParam({ device: tok || null }), [setParam]);
  const setDays = useCallback((d: number) => setParam({ days: d === DEFAULT_DAYS ? null : String(d) }), [setParam]);
  const reset = useCallback(() => setParam({ source: null, device: null, days: null }), [setParam]);

  // "Still deciding what to offer": the picker has no places yet and the read
  // that would supply them has not answered. It is the LIST that gates this, not
  // all four reads, because that is the one the pickers cannot be drawn without.
  const loading = locsQ.isPending;

  const value: ScopeValue = {
    loading, locs, devices, home, yardAvailable, sources, selected, deviceTok, days,
    includeNonBird: showNonBird, setSource, setDevice, setDays, reset,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBirdsScope(): ScopeValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useBirdsScope must be used within a BirdsScopeProvider");
  return v;
}

// True while the viewport is in the mobile chrome (top-bar + bottom tabs) rather
// than the web sidebar. 920px is the redesign's web↔mobile breakpoint.
export function useIsNarrow(maxWidth = 920): boolean {
  const [narrow, setNarrow] = useState(() =>
    typeof window !== "undefined" && window.matchMedia(`(max-width:${maxWidth}px)`).matches);
  useEffect(() => {
    const mq = window.matchMedia(`(max-width:${maxWidth}px)`);
    const on = () => setNarrow(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [maxWidth]);
  return narrow;
}
