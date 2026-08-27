import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radio, Loader2, AlertTriangle, X, ChevronLeft, ChevronRight, Maximize2 } from "lucide-react";
import { useRecorder } from "./birds/recorder";
import { ListenButton } from "./birds/BirdsHeader";
import Spectrogram from "./birds/Spectrogram";
import AddressPicker from "./birds/AddressPicker";
import type { Place } from "./birds/geocode";
import { useShowNonBird } from "./birds/settings";
import { useBirdsScope, type Source } from "./birds/scope";
import { Button, Notice, Segmented, Skeleton, StateBlock, type AppChatDockRenderer } from "@workshop/ui";
import { API, pct, Stat, StatGrid, SpeciesLink, BirdThumb } from "./birds/ui";
import {
  birdsKeys, fetchActivityDaily, fetchActivityHourly, fetchDetections, fetchLocationActivity,
  fetchLocationSpecies, fetchLocationSummary, fetchSpecies, fetchSummary,
} from "./birds/queries";
import { RecordingsBanner } from "./birds/recordings";
import ActivityCard from "./birds/ActivityCard";
import NowCard from "./birds/NowCard";
import LiveFeed from "./birds/LiveFeed";
import Aviary from "./birds/Aviary";

// Birds home. One page, reshaped by two dropdown filters: which place (the
// always-on yard, or any device-created location) and which device. The live
// panel (this device's spectrogram + feed) appears while you're listening; the
// always-on yard's live feed is viewable on its own.
const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

export default function Birds({
  renderChatDock,
}: {
  renderChatDock?: AppChatDockRenderer;
}) {
  const rec = useRecorder();
  // The persistent Location · Device · Time scope now lives in the shell; the
  // home page just reads the current selection from it.
  const { selected, deviceTok, yardAvailable, includeNonBird } = useBirdsScope();

  return (
    <div>
      {!PUBLIC && <RecordingsBanner />}

      {/* The live card is the Aviary (the ambient "who's in the yard now" scene).
          Listening on this device is the one recording affordance (its mic); the
          spectrogram for it appears in a panel below only while actually listening. */}
      {!PUBLIC && <AviaryCard />}
      {!PUBLIC && <DeviceListenPanel />}
      {/* Recent: presence (15-min windows), newest first. */}
      {!PUBLIC && yardAvailable && <RecentCard includeNonBird={includeNonBird} />}
      {/* Live feeds from any broadcasting device you can see (your shared devices,
          friends' shared devices) — this is how you watch another mic's feed. */}
      {!PUBLIC && <LiveFeed />}

      {selected
        ? <Dashboard source={selected} includeNonBird={includeNonBird}
            device={selected.kind === "loc" ? deviceTok : ""} />
        : !rec.recording && (
          <div className="card" style={{ padding: "1.5rem", textAlign: "center" }}>
            <p className="muted" style={{ margin: 0 }}>
              No birds yet. Tap <strong>Listen</strong> to start detecting birds from this device.
            </p>
          </div>
        )}
      {!PUBLIC && renderChatDock?.({
        title: "Birds assistant",
        placeholder: "Ask about recent detections…",
      })}
    </div>
  );
}

// --- Live card: the Aviary ----------------------------------------------------
// The home's live card IS the Aviary — the ambient "who's in the yard now" scene
// on the rolling-15-min live data — with an expand ("cast") button and the single
// Listen affordance (this device's mic). No Histogram/Aviary toggle: the always-on
// yard's scrolling spectrogram already lives in the Live feed below.
function AviaryCard() {
  const rec = useRecorder();
  const [fullscreen, setFullscreen] = useState(false);
  return (
    <section className="card birds-livecard is-live">
      <div className="birds-livecard__head">
        <span className="birds-live"><Radio size={14} className="spin-pulse" /> Live Aviary</span>
        <span className="muted" style={{ fontSize: ".85rem" }}>who's in the yard now</span>
        <span className="birds-livecard__ctl">
          <button className="btn sm" title="Open the Aviary fullscreen" aria-label="Open the Aviary fullscreen"
            onClick={() => setFullscreen(true)}><Maximize2 size={15} /></button>
          <ListenButton />
        </span>
      </div>
      <Aviary />
      {rec.err && <p className="error" style={{ marginBottom: 0, marginTop: ".5rem" }}>{rec.err}</p>}
      {rec.analyzerUp === false && <p className="error" style={{ marginBottom: 0 }}>The analyzer is not running.</p>}
      {fullscreen && <AviaryCast location={rec.location} onExit={() => setFullscreen(false)} />}
    </section>
  );
}

// This device's listening panel — its spectrogram + now-hearing + session list.
// Shown ONLY while you're actually listening on this device (started from the
// Aviary card's mic); hidden otherwise so it never clutters the home.
function DeviceListenPanel() {
  const rec = useRecorder();
  const [showNonBird] = useShowNonBird();
  const live = rec.recording;
  const paused = rec.wasListening && !rec.recording;
  if (!live && !paused) return null;
  const species = showNonBird ? rec.sessionSpecies : rec.sessionSpecies.filter((s) => s.is_bird);
  const hearing = (showNonBird ? rec.nowHearing : rec.nowHearing.filter((d) => d.is_bird))
    .sort((a, b) => b.confidence - a.confidence);
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".6rem", flexWrap: "wrap" }}>
        {paused ? <Radio size={16} className="muted" />
          : rec.status === "analyzing" ? <Loader2 size={16} className="spin" /> : <Radio size={16} style={{ color: "var(--ok)" }} />}
        <strong>{rec.device.name || "This device"}</strong>
        <span className="muted" style={{ fontSize: ".85rem" }}>{paused ? "· paused" : rec.location ? `· ${rec.location}` : "· locating…"}</span>
        <span style={{ marginLeft: "auto", display: "inline-flex", gap: ".4rem" }}>
          {paused && <button className="btn sm" onClick={rec.stop} title="Dismiss" aria-label="Dismiss"><X size={14} /></button>}
          <ListenButton />
        </span>
      </div>
      <Spectrogram height={150} />
      {live && rec.needsLocation && <SetLocationInline />}
      <div style={{ minHeight: 26, margin: ".6rem 0 .2rem" }}>
        {paused ? (
          <span className="muted" style={{ fontSize: ".82rem" }}>Paused. Resume to keep listening.</span>
        ) : hearing.length > 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: ".8rem" }}>Now:</span>
            {hearing.slice(0, 4).map((d, i) => (
              <span key={i} className="pill ok">{d.common_name} {pct(d.confidence)}</span>
            ))}
          </div>
        ) : <span className="muted" style={{ fontSize: ".82rem" }}>Listening…</span>}
      </div>
      {species.length > 0 && (
        <>
          <div className="muted" style={{ fontSize: ".78rem", margin: ".5rem 0 .3rem" }}>Detected this session</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
            {species.map((s) => (
              <SpeciesLink key={s.scientific_name} sci={s.scientific_name} common={s.common_name}>
                <span className="pill neutral" style={s.is_bird ? { cursor: "pointer" } : { opacity: 0.65, borderStyle: "dashed", cursor: "pointer" }}>
                  {s.common_name} <span className="muted">{pct(s.max_conf)}{s.hits > 1 ? ` ·${s.hits}` : ""}</span>
                </span>
              </SpeciesLink>
            ))}
          </div>
        </>
      )}
    </section>
  );
}

// Fullscreen / "cast" Aviary — full-bleed, chrome minimized to back/exit, a
// location pill, and a live clock. Made to be left running on a tablet or TV.
function AviaryCast({ location, onExit }: { location: string | null; onExit: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onExit(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onExit]);
  return (
    <div className="birds-cast" role="dialog" aria-label="Live Aviary fullscreen">
      <div className="birds-cast__bar">
        <button className="btn sm" onClick={onExit} title="Exit fullscreen" aria-label="Exit fullscreen"><X size={16} /></button>
        <span className="birds-cast__loc">{location || "Yard"}</span>
        <LiveClock />
      </div>
      <div className="birds-cast__scene"><Aviary fullscreen /></div>
    </div>
  );
}

function LiveClock() {
  const [now, setNow] = useState(() => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
  useEffect(() => {
    const t = setInterval(() => setNow(new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })), 30_000);
    return () => clearInterval(t);
  }, []);
  return <span className="birds-cast__clock mono">{now}</span>;
}

// Recent (presence): each species once, newest first, with how many 15-minute
// windows it appeared in over the selected lookback. Presence over chatter — a
// constantly-calling bird does not outrank a quiet, reliably-present one; raw
// per-detection lists live on the bird page. Reads the always-on yard feed
// (/detections); shown when a live yard feed exists.
const RECENT_WINDOWS = [
  { value: "15m", label: "15m", mins: 15 },
  { value: "1h", label: "1h", mins: 60 },
  { value: "today", label: "Today", mins: 0 },
];
const PER_PAGE = 10;
// The whole yard feed the presence rows are folded out of. In the key, because a
// cache entry that answered "the last 1000" must never be handed to a caller
// that asked for 80 (the Aviary does).
const RECENT_LIMIT = 1000;
const RECENT_MS = 8000;
type RecentDet = { date?: string; time: string; common_name: string; scientific_name: string };
// One empty array, so "nothing yet" keeps a stable identity across renders and
// the memo below is not rebuilt for a new `[]` on every tick.
const NO_DETS: RecentDet[] = [];
function detEpoch(date: string | undefined, time: string): number {
  const t = (time || "").slice(0, 8);
  const d = date || new Date().toISOString().slice(0, 10);
  return new Date(`${d}T${t}`).getTime();
}
function clockOf(epoch: number): string {
  return new Date(epoch).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function RecentCard({ includeNonBird }: { includeNonBird: boolean }) {
  const [win, setWin] = useState("1h");
  const [page, setPage] = useState(0);

  // The one genuinely live read on this page: detections land continuously and
  // this card is the "what just happened" view of them, so it keeps the 8s
  // cadence and owns it -- nothing stales it underneath, and it does not retry,
  // because a failed tick is followed by the next tick eight seconds later.
  // Toggling the non-bird filter changes the key; the rows stay up while the
  // new ones load instead of the card emptying and refilling.
  const p = { includeNonBird, limit: RECENT_LIMIT };
  const detQ = useQuery({
    queryKey: birdsKeys.detections(p),
    queryFn: () => fetchDetections<{ detections: RecentDet[] }>(p),
    refetchInterval: RECENT_MS,
    staleTime: 0,
    retry: false,
    placeholderData: keepPreviousData,
  });
  const dets = detQ.data?.detections ?? NO_DETS;
  const err = detQ.isError ? String(detQ.error) : null;

  // The window slides with the DATA, which is why `dets` is a dependency of a
  // value it does not read. "The last 15 minutes" was computed once, when the
  // window was last changed, and then held: ten minutes of sitting on this card
  // quietly turned it into the last twenty-five. Recomputing it as each tick
  // lands is what makes the label true.
  const cutoff = useMemo(() => {
    const w = RECENT_WINDOWS.find((x) => x.value === win)!;
    if (w.mins) return Date.now() - w.mins * 60_000;
    const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime();
  }, [win, dets]);

  const rows = useMemo(() => {
    const map = new Map<string, { common: string; latest: number; buckets: Set<number> }>();
    for (const r of dets) {
      const t = detEpoch(r.date, r.time);
      if (!Number.isFinite(t) || t < cutoff) continue;
      let e = map.get(r.scientific_name);
      if (!e) { e = { common: r.common_name, latest: t, buckets: new Set() }; map.set(r.scientific_name, e); }
      if (t > e.latest) e.latest = t;
      e.buckets.add(Math.floor(t / (15 * 60_000)));
    }
    return [...map.entries()]
      .map(([sci, e]) => ({ sci, common: e.common, latest: e.latest, windows: e.buckets.size }))
      .sort((a, b) => b.latest - a.latest);
  }, [dets, cutoff]);

  const pageCount = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  const safePage = Math.min(page, pageCount - 1);
  const shown = rows.slice(safePage * PER_PAGE, safePage * PER_PAGE + PER_PAGE);

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
        <h3 style={{ margin: 0, fontSize: "1rem", display: "flex", alignItems: "center", gap: ".4rem" }}>
          <Radio size={15} style={{ color: "var(--ok)" }} /> Recent
        </h3>
        <span style={{ marginLeft: "auto" }}>
          {/* Changing the window is what resets the page, so it is done by the
              control rather than by an effect watching the result of it. */}
          <Segmented value={win} onChange={(v) => { setWin(v); setPage(0); }} ariaLabel="Recent window"
            options={RECENT_WINDOWS.map((w) => ({ value: w.value, label: w.label }))} />
        </span>
      </div>
      {/* A dropped read with rows still on screen is a line beside them; with
          nothing on screen it is the region instead. */}
      {err && detQ.data && (
        <div style={{ marginBottom: ".6rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={() => void detQ.refetch()}>Retry</Button>}>
            Recent detections are not refreshing.
          </Notice>
        </div>
      )}
      {err && !detQ.data ? (
        <StateBlock
          variant="error"
          title="Couldn't load recent detections"
          hint={<code>GET /api/birds/detections: {err}</code>}
          action={<Button size="sm" onClick={() => void detQ.refetch()}>Retry</Button>}
        />
      ) : detQ.isPending ? (
        <Skeleton style={{ height: 120 }} />
      ) : rows.length === 0 ? (
        <p className="muted" style={{ fontSize: ".84rem", margin: 0 }}>Nothing heard in this window yet.</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {shown.map((r) => (
            <SpeciesLink key={r.sci} sci={r.sci} common={r.common}>
              <div style={{ display: "flex", alignItems: "center", gap: ".7rem", padding: ".5rem 0", borderTop: "1px solid var(--border)" }}>
                <BirdThumb sci={r.sci} common={r.common} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block" }}>{r.common}</span>
                  <span className="muted" style={{ fontSize: ".78rem" }}>{r.windows} {r.windows === 1 ? "window" : "windows"}</span>
                </span>
                <span className="mono muted" style={{ fontSize: ".8rem", flexShrink: 0 }}>{clockOf(r.latest)}</span>
              </div>
            </SpeciesLink>
          ))}
        </div>
      )}
      {pageCount > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: ".75rem", marginTop: ".6rem" }}>
          <button className="btn sm" disabled={safePage === 0} onClick={() => setPage(safePage - 1)} aria-label="Previous page"><ChevronLeft size={15} /></button>
          <span className="muted" style={{ fontSize: ".8rem" }}>Page {safePage + 1} / {pageCount}</span>
          <button className="btn sm" disabled={safePage >= pageCount - 1} onClick={() => setPage(safePage + 1)} aria-label="Next page"><ChevronRight size={15} /></button>
        </div>
      )}
    </section>
  );
}

// Shown while listening when this device has no real location to file detections
// under. Picking a place creates the location and pins this device to it.
function SetLocationInline() {
  const rec = useRecorder();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const pick = (p: Place) => {
    setBusy(true);
    const fd = new FormData();
    fd.append("name", p.short);
    fd.append("lat", String(p.lat)); fd.append("lon", String(p.lon));
    fd.append("address", p.label || p.short);
    fetch(`${API}/location`, { method: "POST", body: fd })
      .then((r) => r.json())
      .then((d) => {
        const id = d.location?.id;
        if (id) { rec.setMode("fixed"); rec.setFixedLocation(String(id)); }
        // This creates the place the scope picker offers and pins this device to
        // it, so everything downstream of "which places exist" is now wrong.
        // The provider used to catch up because it re-read all three of its
        // facts every eight seconds; it does not any more, and it should not
        // have to. The write says what it changed.
        void qc.invalidateQueries({ queryKey: birdsKeys.all() });
      })
      .finally(() => setBusy(false));
  };
  return (
    <div className="card" style={{ padding: ".7rem .9rem", margin: ".6rem 0", border: "1px solid color-mix(in srgb, var(--warn) 33%, transparent)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".5rem" }}>
        <AlertTriangle size={16} style={{ color: "var(--warn)" }} />
        <span style={{ fontSize: ".88rem" }}>
          {busy ? "Saving location…" : "No location for this device yet. Pick where you are so detections save."}
        </span>
      </div>
      <AddressPicker onPick={pick} />
    </div>
  );
}

// --- Dashboard: stats + activity + species for any source ----------------
//
// SEVEN QUERIES, ONE BRANCH, ONE PURE MERGE, AND THE TWO BUGS THAT CAME OUT OF
// THE OLD SHAPE.
//
// This was a `Promise.all` inside a `useCallback` behind an 8s `usePoll`, with
// the branch (a place is selected / nothing is) chosen INSIDE the callback and
// its result written to one `useState`.
//
//  1. RESPONSE REORDERING. The location id lived in the closure, not in
//     anything the request could be identified by, so changing places while a
//     slow round trip was in flight landed the OLD place's payload in the new
//     place's state and left it there until the next tick. Every parameter is
//     in the key now (`queries.ts`), so the two answers are two cache entries
//     and the stale one has nowhere to land.
//
//  2. A GUARANTEED BLANK ON EVERY SCOPE CHANGE. `useEffect(() => { setD(null);
//     load(); }, [load])` blanked the whole dashboard -- charts, species, stats
//     -- for a full round trip every time the place, the device or the non-bird
//     filter changed, because `usePoll` reads its callback through a ref and
//     never re-runs it on its own. That effect is DELETED, not ported: a query
//     refetches when its key changes, and `keepPreviousData` leaves the last
//     answer up while the next one is in flight. This is the single biggest
//     visible change in this migration.
//
// The branch is now `enabled`, which is a property of each query rather than a
// decision made at fetch time, and the merge is a pure function of what the
// active branch returned. Each card is fed by exactly ONE read (stats from the
// summary, the chart from the activity, the list from the species), so the
// brief window where one has answered and another has not shows two cards a
// beat apart rather than one card contradicting itself.
type SpeciesItem = {
  scientific_name: string; common_name: string; count: number; max_conf: number;
  status?: string; is_new?: boolean; auto?: boolean;
};
type DayPoint = {
  date: string; count: number; windows?: number;
  hourly?: number[]; hourly_species?: number[]; hourly_shannon?: number[];
};
type HourPoint = { hour: number; count: number; species?: number; shannon?: number };

type Norm = {
  species: number; detections: number; today: number; active_days: number;
  daily: DayPoint[];
  hourly: HourPoint[];
  speciesList: SpeciesItem[];
  lat?: number | null; lon?: number | null;
};

// The two branches' payloads, as this page reads them.
type LocSummary = {
  species: number; detections: number; today: number; active_days: number;
  lat?: number | null; lon?: number | null;
};
type LocActivity = { daily: DayPoint[]; hourly: HourPoint[] };
type SpeciesList = { species: SpeciesItem[] };
type YardSummary = {
  species_count: number; total_detections: number;
  today?: { detections?: number } | null; active_days?: number;
};

/** One place's reads, merged. Pure: same inputs, same `Norm`, no state. */
function mergeLocation(s: LocSummary, a: LocActivity, sp: SpeciesList): Norm {
  return {
    species: s.species, detections: s.detections, today: s.today, active_days: s.active_days,
    daily: a.daily, hourly: a.hourly, speciesList: sp.species, lat: s.lat, lon: s.lon,
  };
}

/** Everywhere-at-once, merged from its four reads. Same contract. */
function mergeYard(s: YardSummary, h: { hourly: HourPoint[] }, d: { daily: DayPoint[] }, sp: SpeciesList): Norm {
  return {
    species: s.species_count, detections: s.total_detections,
    today: s.today?.detections || 0, active_days: s.active_days || 0,
    daily: d.daily, hourly: h.hourly, speciesList: sp.species, lat: null, lon: null,
  };
}

// The windows the unscoped activity charts ask for. In the keys, because they
// are in the URLs.
const YARD_HOURLY_DAYS = 30;
const YARD_DAILY_DAYS = 45;
const DASH_MS = 8000;

function Dashboard({ source, includeNonBird, device }: {
  source: Source; includeNonBird: boolean; device: string;
}) {
  const isLoc = source.kind === "loc" && !!source.id;
  // A location branch that is not selected still has to name a key, so it names
  // an impossible one and stays disabled. `enabled` is the only thing that
  // decides which branch runs.
  const lp = { locationId: source.id ?? 0, includeNonBird, device };
  const yp = { includeNonBird };

  // Every one of these keeps the previous answer while the next is in flight,
  // which is what removes the blank frame. None of them is stale-gated: each
  // owns its cadence, the same 8s the single poll had.
  const live = { refetchInterval: DASH_MS, staleTime: 0, retry: false, placeholderData: keepPreviousData };

  const locSummaryQ = useQuery({
    queryKey: birdsKeys.locationSummary(lp),
    queryFn: () => fetchLocationSummary<LocSummary>(lp),
    enabled: isLoc, ...live,
  });
  const locActivityQ = useQuery({
    queryKey: birdsKeys.locationActivity(lp),
    queryFn: () => fetchLocationActivity<LocActivity>(lp),
    enabled: isLoc, ...live,
  });
  // Home is confirmed-only ("settled truth"); the full review (Suggested /
  // Rejected) lives on the Birds page, which reads the same entry per status.
  const confirmed = { ...lp, status: "confirmed" };
  const locSpeciesQ = useQuery({
    queryKey: birdsKeys.locationSpecies(confirmed),
    queryFn: () => fetchLocationSpecies<SpeciesList>(confirmed),
    enabled: isLoc, ...live,
  });

  const yardSummaryQ = useQuery({
    queryKey: birdsKeys.summary(yp),
    queryFn: () => fetchSummary<YardSummary>(yp),
    enabled: !isLoc, ...live,
  });
  const yardHourlyQ = useQuery({
    queryKey: birdsKeys.activityHourly({ ...yp, days: YARD_HOURLY_DAYS }),
    queryFn: () => fetchActivityHourly<{ hourly: HourPoint[] }>({ ...yp, days: YARD_HOURLY_DAYS }),
    enabled: !isLoc, ...live,
  });
  const yardDailyQ = useQuery({
    queryKey: birdsKeys.activityDaily({ ...yp, days: YARD_DAILY_DAYS }),
    queryFn: () => fetchActivityDaily<{ daily: DayPoint[] }>({ ...yp, days: YARD_DAILY_DAYS }),
    enabled: !isLoc, ...live,
  });
  const yardSpeciesQ = useQuery({
    queryKey: birdsKeys.species(yp),
    queryFn: () => fetchSpecies<SpeciesList>(yp),
    enabled: !isLoc, ...live,
  });

  const locData = locSummaryQ.data && locActivityQ.data && locSpeciesQ.data
    ? { s: locSummaryQ.data, a: locActivityQ.data, sp: locSpeciesQ.data } : null;
  const yardData = yardSummaryQ.data && yardHourlyQ.data && yardDailyQ.data && yardSpeciesQ.data
    ? { s: yardSummaryQ.data, h: yardHourlyQ.data, d: yardDailyQ.data, sp: yardSpeciesQ.data } : null;

  const d = useMemo<Norm | null>(() => {
    if (isLoc) return locData ? mergeLocation(locData.s, locData.a, locData.sp) : null;
    return yardData ? mergeYard(yardData.s, yardData.h, yardData.d, yardData.sp) : null;
  }, [isLoc, locData, yardData]);

  const active = isLoc
    ? [locSummaryQ, locActivityQ, locSpeciesQ]
    : [yardSummaryQ, yardHourlyQ, yardDailyQ, yardSpeciesQ];
  const failed = active.find((q) => q.isError);
  const err = failed ? String(failed.error) : null;
  const retry = () => { for (const q of active) void q.refetch(); };

  const topBirds = useMemo(
    () => (d ? [...d.speciesList].sort((a, b) => b.count - a.count).slice(0, 8) : []), [d]);

  if (!d) {
    return err ? (
      <StateBlock
        variant="error"
        title="Couldn't load this view"
        hint={<code>{err}</code>}
        action={<Button size="sm" onClick={retry}>Retry</Button>}
      />
    ) : (
      <div style={{ display: "grid", gap: "1rem" }}>
        <Skeleton style={{ height: 220 }} />
        <Skeleton style={{ height: 160 }} />
      </div>
    );
  }
  const locId = source.kind === "loc" ? source.id : undefined;

  return (
    <>
      {/* Numbers still on screen and a read that dropped: a line, not a region. */}
      {err && (
        <div style={{ marginBottom: ".7rem" }}>
          <Notice tone="warn" action={<Button size="sm" variant="ghost" onClick={retry}>Retry</Button>}>
            These numbers are not refreshing.
          </Notice>
        </div>
      )}

      {/* Home order (redesign): Activity -> Forecast peek -> Top birds -> stats.
          Recent (presence) slots above Activity in Phase 3. */}
      {(d.daily.length > 0 || d.hourly.some((h) => h.count > 0)) && (
        <ActivityCard daily={d.daily} hourly={d.hourly} locationId={locId} />
      )}

      {/* Forecast peek: the compact "what to expect" card for a location. */}
      {locId != null && <NowCard locationId={locId} />}

      {/* Top birds: a confirmed-only peek; the full review (Confirmed/Suggested/
          Rejected) lives on the Birds page. */}
      {topBirds.length > 0 && (
        <section className="card" style={card}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
            <h3 style={{ ...h3, margin: 0 }}>Top birds</h3>
            <Link to="/apps/birds/birds" className="muted" style={{ fontSize: ".82rem", display: "inline-flex", alignItems: "center", gap: ".15rem" }}>
              See all <ChevronRight size={14} />
            </Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {topBirds.map((s) => (
              <SpeciesLink key={s.scientific_name} sci={s.scientific_name} common={s.common_name}>
                <div style={{ display: "flex", alignItems: "center", gap: ".7rem", padding: ".5rem 0", borderTop: "1px solid var(--border)" }}>
                  <BirdThumb sci={s.scientific_name} common={s.common_name} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span style={{ display: "block" }}>{s.common_name}</span>
                    <span className="muted" style={{ fontStyle: "italic", fontSize: ".78rem" }}>{s.scientific_name}</span>
                  </span>
                  <span className="muted" style={{ fontSize: ".8rem", textAlign: "right" }}>{s.count}×<br />best {pct(s.max_conf)}</span>
                </div>
              </SpeciesLink>
            ))}
          </div>
        </section>
      )}

      {/* At-a-glance stats, at the foot of the page. */}
      <StatGrid>
        <Stat label="Species" value={d.species} />
        <Stat label="Detections" value={d.detections.toLocaleString()} />
        <Stat label="Today" value={d.today} />
        <Stat label="Active days" value={d.active_days} />
      </StatGrid>
    </>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" };
