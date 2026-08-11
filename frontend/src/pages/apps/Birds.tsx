import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Radio, Loader2, AlertTriangle, X, ChevronLeft, ChevronRight, Maximize2 } from "lucide-react";
import { useRecorder } from "./birds/recorder";
import { ListenButton } from "./birds/BirdsHeader";
import Spectrogram from "./birds/Spectrogram";
import AddressPicker from "./birds/AddressPicker";
import type { Place } from "./birds/geocode";
import { useShowNonBird } from "./birds/settings";
import { useBirdsScope, type Source } from "./birds/scope";
import { Segmented } from "@workshop/ui";
import { API, pct, jget, Stat, StatGrid, SpeciesLink, BirdThumb } from "./birds/ui";
import { RecordingsBanner } from "./birds/recordings";
import ActivityCard from "./birds/ActivityCard";
import NowCard from "./birds/NowCard";
import LiveFeed from "./birds/LiveFeed";
import Aviary from "./birds/Aviary";
import AppChatDock from "../../components/AppChatDock";

// Birds home. One page, reshaped by two dropdown filters: which place (the
// always-on yard, or any device-created location) and which device. The live
// panel (this device's spectrogram + feed) appears while you're listening; the
// always-on yard's live feed is viewable on its own.
const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

export default function Birds() {
  const rec = useRecorder();
  // The persistent Location · Device · Time scope now lives in the shell; the
  // home page just reads the current selection from it.
  const { selected, deviceTok, yardAvailable, nb } = useBirdsScope();

  return (
    <div>
      {!PUBLIC && <RecordingsBanner />}

      {/* The live card is the Aviary (the ambient "who's in the yard now" scene).
          Listening on this device is the one recording affordance (its mic); the
          spectrogram for it appears in a panel below only while actually listening. */}
      {!PUBLIC && <AviaryCard />}
      {!PUBLIC && <DeviceListenPanel />}
      {/* Recent: presence (15-min windows), newest first. */}
      {!PUBLIC && yardAvailable && <RecentCard nb={nb} />}
      {/* Live feeds from any broadcasting device you can see (your shared devices,
          friends' shared devices) — this is how you watch another mic's feed. */}
      {!PUBLIC && <LiveFeed />}

      {selected
        ? <Dashboard source={selected} nb={nb} device={selected.kind === "loc" ? deviceTok : ""} />
        : !rec.recording && (
          <div className="card" style={{ padding: "1.5rem", textAlign: "center" }}>
            <p className="muted" style={{ margin: 0 }}>
              No birds yet. Tap <strong>Listen</strong> to start detecting birds from this device.
            </p>
          </div>
        )}
      {!PUBLIC && <AppChatDock appId="birds" title="Birds assistant" placeholder="Ask about recent detections…" />}
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
type RecentDet = { date?: string; time: string; common_name: string; scientific_name: string };
function detEpoch(date: string | undefined, time: string): number {
  const t = (time || "").slice(0, 8);
  const d = date || new Date().toISOString().slice(0, 10);
  return new Date(`${d}T${t}`).getTime();
}
function clockOf(epoch: number): string {
  return new Date(epoch).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function RecentCard({ nb }: { nb: string }) {
  const [win, setWin] = useState("1h");
  const [page, setPage] = useState(0);
  const [dets, setDets] = useState<RecentDet[]>([]);
  useEffect(() => {
    const load = () => jget<{ detections: RecentDet[] }>(`/detections?limit=1000${nb}`)
      .then((d) => setDets(d.detections || [])).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [nb]);
  useEffect(() => setPage(0), [win]);

  const cutoff = useMemo(() => {
    const w = RECENT_WINDOWS.find((x) => x.value === win)!;
    if (w.mins) return Date.now() - w.mins * 60_000;
    const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime();
  }, [win]);

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
          <Segmented value={win} onChange={setWin} ariaLabel="Recent window"
            options={RECENT_WINDOWS.map((w) => ({ value: w.value, label: w.label }))} />
        </span>
      </div>
      {rows.length === 0 ? (
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
type Norm = {
  species: number; detections: number; today: number; active_days: number;
  daily: { date: string; count: number }[];
  hourly: { hour: number; count: number }[];
  speciesList: { scientific_name: string; common_name: string; count: number; max_conf: number; status?: string; is_new?: boolean; auto?: boolean }[];
  lat?: number | null; lon?: number | null;
};

function Dashboard({ source, nb, device }: { source: Source; nb: string; device: string }) {
  const [d, setD] = useState<Norm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const dev = device ? `&device=${encodeURIComponent(device)}` : "";

  const load = useCallback(() => {
    if (source.kind === "loc" && source.id) {
      const id = source.id;
      Promise.all([
        jget<any>(`/location/summary?location_id=${id}${nb}${dev}`),
        jget<any>(`/location/activity?location_id=${id}${nb}${dev}`),
        // Home is confirmed-only ("settled truth"); the full review (Suggested/
        // Rejected) lives on the Birds page.
        jget<{ species: any[] }>(`/location/species?location_id=${id}${nb}${dev}&status=confirmed`),
      ]).then(([s, a, sp]) => {
        setD({
          species: s.species, detections: s.detections, today: s.today, active_days: s.active_days,
          daily: a.daily, hourly: a.hourly, speciesList: sp.species, lat: s.lat, lon: s.lon,
        });
      }).catch((e) => setErr(String(e)));
    } else {
      Promise.all([
        jget<any>(`/summary?_=1${nb}`),
        jget<{ hourly: any[] }>(`/activity/hourly?days=30${nb}`),
        jget<{ daily: any[] }>(`/activity/daily?days=45${nb}`),
        jget<{ species: any[] }>(`/species?_=1${nb}`),
      ]).then(([s, h, dd, sp]) => setD({
        species: s.species_count, detections: s.total_detections, today: s.today?.detections || 0,
        active_days: s.active_days || 0,
        daily: dd.daily, hourly: h.hourly, speciesList: sp.species, lat: null, lon: null,
      })).catch((e) => setErr(String(e)));
    }
  }, [source.kind, source.id, nb, dev]);

  useEffect(() => { setD(null); setErr(null); load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  if (err && !d) return <p className="error">Could not load: {err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const locId = source.kind === "loc" ? source.id : undefined;
  const topBirds = [...d.speciesList].sort((a, b) => b.count - a.count).slice(0, 8);

  return (
    <>
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
