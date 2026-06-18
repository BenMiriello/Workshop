import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { jget } from "./ui";
import { useShowNonBird, nonBirdQuery } from "./settings";

// The Birds app's single persistent scope: Location · Device · Time, applied on
// every page (including bird detail). It replaces the per-page pickers the home
// dashboard used to own. Backed by URL params (`source`, `device`, `days`) so a
// scoped view is shareable and survives reloads; the shell renders the pickers.
//
// Defaults: the home location + its devices (Yard · Yard Mic when present), the
// longer 30-day window — so the live feed and "who's here now" are present
// immediately rather than an ambiguous "everything".

export type Loc = { id: number; name: string; detections: number; species: number; devices: string[] };
export type KnownDevice = { token: string; name: string };
export type Source = { key: string; name: string; kind: "yard" | "loc"; id?: number };

// Sentinel value used by the place picker to jump to the full Locations list
// rather than select a source.
export const ALL_LOCS = "__all_locations__";

const DEFAULT_DAYS = 30;
// Offered analytical windows. "Today" maps to a 1-day rolling window so it works
// against the existing days=N endpoints without a backend change.
export const TIME_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: "Today" },
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
];

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
  nb: string; // non-bird display-filter query suffix, threaded through every scoped fetch
  setSource: (key: string) => void;
  setDevice: (tok: string) => void;
  setDays: (d: number) => void;
  reset: () => void;
  reload: () => void;
};

const Ctx = createContext<ScopeValue | null>(null);

export function BirdsScopeProvider({ children }: { children: React.ReactNode }) {
  const [params, setParams] = useSearchParams();
  const [locs, setLocs] = useState<Loc[]>([]);
  const [devices, setDevices] = useState<KnownDevice[]>([]);
  const [home, setHome] = useState<string | null>(null);
  const [yardAvailable, setYardAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);

  const reload = useCallback(() => {
    jget<{ locations: Loc[]; home: string | null }>(`/locations?_=1${nb}`)
      .then((d) => { setLocs(d.locations); setHome(d.home); }).catch(() => {}).finally(() => setLoading(false));
    jget<{ devices: KnownDevice[] }>(`/devices`).then((d) => setDevices(d.devices)).catch(() => {});
    jget<{ available: boolean; total_detections: number }>(`/summary?_=1`)
      .then((s) => setYardAvailable(s.available && s.total_detections > 0)).catch(() => {});
  }, [nb]);
  useEffect(() => { reload(); const t = setInterval(reload, 8000); return () => clearInterval(t); }, [reload]);

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

  const value: ScopeValue = {
    loading, locs, devices, home, yardAvailable, sources, selected, deviceTok, days, nb,
    setSource, setDevice, setDays, reset, reload,
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
