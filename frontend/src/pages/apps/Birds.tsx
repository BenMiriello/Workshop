import { useCallback, useEffect, useMemo, useState } from "react";
import { Radio, Loader2, AlertTriangle, X, ChevronLeft, ChevronRight } from "lucide-react";
import { useRecorder } from "./birds/recorder";
import { ListenButton } from "./birds/BirdsHeader";
import Spectrogram from "./birds/Spectrogram";
import AddressPicker from "./birds/AddressPicker";
import type { Place } from "./birds/geocode";
import { useShowNonBird } from "./birds/settings";
import { useBirdsScope, type Source } from "./birds/scope";
import { Segmented } from "../../ui";
import { API, pct, jget, Stat, StatGrid, SpeciesLink, BirdThumb } from "./birds/ui";
import { RecordingsBanner } from "./birds/recordings";
import SpeciesRow from "./birds/SpeciesRow";
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
// The species-list filter is a view preference: it persists across navigation and
// reloads (and every place), so it doesn't snap back to "All" each time you land
// on a dashboard. Stored as one shared value, not per-location.
const SPECIES_FILTER_KEY = "birdSpeciesFilter";
function loadSpeciesFilter(): ListStatus {
  const v = localStorage.getItem(SPECIES_FILTER_KEY);
  return v === "confirmed" || v === "suggested" || v === "rejected" || v === "active" ? v : "active";
}

export default function Birds() {
  const rec = useRecorder();
  // The persistent Location · Device · Time scope now lives in the shell; the
  // home page just reads the current selection from it.
  const { selected, deviceTok, yardAvailable, nb } = useBirdsScope();

  return (
    <div>
      {!PUBLIC && <RecordingsBanner />}

      {/* The live card is the home's primary surface and the ONE recording
          affordance: Listen/Stop + a single pulsing "Live" sit in its header
          (the old top-bar Listen + bottom RecorderBar pill are gone). It's
          persistent so Listen is always reachable, showing an idle invite when
          not listening and this device's real scrolling spectrogram while live. */}
      {!PUBLIC && <LiveCard />}
      {/* Recent: presence (15-min windows), newest first — directly under the
          live card per the redesign order. */}
      {!PUBLIC && yardAvailable && <RecentCard nb={nb} />}
      {/* Live feeds from any broadcasting device you can see (your shared devices,
          friends' shared devices) — this is how you watch another mic's feed. */}
      {!PUBLIC && <LiveFeed />}

      {/* The Live Aviary: an ambient "who's in the yard right now" diorama, sitting
          directly under the live feed, driven by real detections from the mic.
          Private app only — the public deploy has no live detection feed to drive it. */}
      {!PUBLIC && <div style={{ marginBottom: "1rem" }}><Aviary /></div>}

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

// --- Live card: the home's primary surface + sole recording affordance --------
// Persistent on Home. Header carries the single Listen/Stop control (ListenButton)
// and a single pulsing "Live" indicator. While listening it shows this device's
// real scrolling spectrogram + now-hearing + session species; idle, it invites a
// tap. (Phase 7 adds the Histogram <-> Aviary view toggle + expand here.)
function LiveCard() {
  const rec = useRecorder();
  const [showNonBird] = useShowNonBird();
  const live = rec.recording;
  const paused = rec.wasListening && !rec.recording; // was listening, not capturing (after a reload)
  const active = live || paused;
  const species = showNonBird ? rec.sessionSpecies : rec.sessionSpecies.filter((s) => s.is_bird);
  const hearing = (showNonBird ? rec.nowHearing : rec.nowHearing.filter((d) => d.is_bird))
    .sort((a, b) => b.confidence - a.confidence);

  return (
    <section className={`card birds-livecard${live ? " is-live" : ""}`}>
      <div className="birds-livecard__head">
        {live
          ? <span className="birds-live"><Radio size={14} className="spin-pulse" /> Live</span>
          : rec.status === "analyzing" ? <Loader2 size={16} className="spin" /> : <Radio size={16} className="muted" />}
        <strong>{rec.device.name || "This device"}</strong>
        <span className="muted" style={{ fontSize: ".85rem" }}>
          {live ? (rec.location ? `· ${rec.location}` : "· locating…") : paused ? "· paused" : "· not listening"}
        </span>
        <span className="birds-livecard__ctl">
          {paused && <button className="btn sm" onClick={rec.stop} title="Dismiss" aria-label="Dismiss"><X size={14} /></button>}
          <ListenButton />
        </span>
      </div>

      {active && <Spectrogram height={150} />}
      {live && rec.needsLocation && <SetLocationInline />}

      {!active && (
        <p className="muted" style={{ margin: ".4rem 0 0", fontSize: ".85rem" }}>
          Not listening. Tap the mic to detect birds from this device.
        </p>
      )}

      {active && (
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
      )}

      {active && species.length > 0 && (
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
      {rec.err && <p className="error" style={{ marginBottom: 0, marginTop: ".5rem" }}>{rec.err}</p>}
      {rec.analyzerUp === false && <p className="error" style={{ marginBottom: 0 }}>Analyzer service is not running on the box.</p>}
    </section>
  );
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
type ListStatus = "active" | "confirmed" | "suggested" | "rejected";

function Dashboard({ source, nb, device }: { source: Source; nb: string; device: string }) {
  const [d, setD] = useState<Norm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatusState] = useState<ListStatus>(loadSpeciesFilter);
  const setStatus = useCallback((v: ListStatus) => {
    localStorage.setItem(SPECIES_FILTER_KEY, v);
    setStatusState(v);
  }, []);
  const [newCount, setNewCount] = useState(0);
  const dev = device ? `&device=${encodeURIComponent(device)}` : "";

  const load = useCallback(() => {
    if (source.kind === "loc" && source.id) {
      const id = source.id;
      Promise.all([
        jget<any>(`/location/summary?location_id=${id}${nb}${dev}`),
        jget<any>(`/location/activity?location_id=${id}${nb}${dev}`),
        jget<{ species: any[] }>(`/location/species?location_id=${id}${nb}${dev}&status=${status}`),
      ]).then(([s, a, sp]) => {
        setD({
          species: s.species, detections: s.detections, today: s.today, active_days: s.active_days,
          daily: a.daily, hourly: a.hourly, speciesList: sp.species, lat: s.lat, lon: s.lon,
        });
        // "New" badge = species first heard here in the last 24h, straight from
        // the list, so the count and the per-row badges can never disagree.
        setNewCount(sp.species.filter((x) => x.is_new).length);
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
  }, [source.kind, source.id, nb, dev, status]);

  useEffect(() => { setD(null); setErr(null); load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  if (err && !d) return <p className="error">Could not load: {err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const isLoc = source.kind === "loc";
  const locId = isLoc ? source.id : undefined;

  return (
    <>
      {/* Home order (redesign): Activity -> Forecast peek -> Top birds -> stats.
          Recent (presence) slots above Activity in Phase 3. */}
      {(d.daily.length > 0 || d.hourly.some((h) => h.count > 0)) && (
        <ActivityCard daily={d.daily} hourly={d.hourly} locationId={locId} />
      )}

      {/* Forecast peek: the compact "what to expect" card for a location. */}
      {locId != null && <NowCard locationId={locId} />}

      <section className="card" style={card}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
          <h3 style={{ ...h3, margin: 0 }}>
            Species ({d.speciesList.length})
            {isLoc && newCount > 0 && <span className="bird-badge" style={{ marginLeft: ".4rem" }}>{newCount} new</span>}
          </h3>
          {isLoc && (
            <select className="num-in" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value as ListStatus)}>
              <option value="active">All</option>
              <option value="confirmed">Confirmed</option>
              <option value="suggested">Suggested</option>
              <option value="rejected">Rejected</option>
            </select>
          )}
        </div>
        {d.speciesList.length === 0 && (
          <p className="muted">
            {!isLoc ? "Nothing yet."
              : status === "suggested" ? "No suggestions right now."
              : status === "rejected" ? "Nothing rejected."
              : "Nothing here yet."}
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column" }}>
          {isLoc
            ? d.speciesList.map((s) => (
                <SpeciesRow key={s.scientific_name} s={s} locId={locId}
                  status={(s.status as any) || "suggested"}
                  lat={d.lat} lon={d.lon} onChanged={load} />
              ))
            : d.speciesList.map((s) => (
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
