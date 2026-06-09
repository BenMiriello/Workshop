import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Bird, Mic, Square, List, Star, Radio, Loader2,
  SlidersHorizontal, Smartphone, AlertTriangle, X,
} from "lucide-react";
import { Segmented } from "../../ui";
import { useRecorder } from "./birds/recorder";
import Spectrogram from "./birds/Spectrogram";
import AddressPicker from "./birds/AddressPicker";
import type { Place } from "./birds/geocode";
import { useShowNonBird, nonBirdQuery } from "./birds/settings";
import { API, pct, jget, Stat, StatGrid, SpeciesLink, BirdThumb } from "./birds/ui";
import { RecordingsBanner } from "./birds/recordings";
import SpeciesRow from "./birds/SpeciesRow";
import ActivityCard from "./birds/ActivityCard";

// Yard Birds home. Dynamic across sources: the always-on yard (Pi) plus every
// device-created location. Whichever source you mark home is shown by default.
// Two actions: listen on this device, or browse locations. The live panel (with
// a Merlin-style spectrogram) appears while this device is listening.
const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

type Loc = { id: number; name: string; detections: number; species: number; devices: string[] };
type Source = { key: string; name: string; kind: "yard" | "loc"; id?: number };

export default function Birds() {
  const [params, setParams] = useSearchParams();
  const [locs, setLocs] = useState<Loc[]>([]);
  const [home, setHome] = useState<string | null>(null);
  const [yardAvailable, setYardAvailable] = useState(false);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);
  const rec = useRecorder();

  const load = useCallback(() => {
    jget<{ locations: Loc[]; home: string | null }>(`/locations?_=1${nb}`)
      .then((d) => { setLocs(d.locations); setHome(d.home); }).catch(() => {});
    jget<{ available: boolean; total_detections: number }>(`/summary?_=1`)
      .then((s) => setYardAvailable(s.available && s.total_detections > 0)).catch(() => {});
  }, [nb]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const sources = useMemo<Source[]>(() => {
    const out: Source[] = [];
    if (yardAvailable) out.push({ key: "yard", name: "Yard", kind: "yard" });
    for (const L of locs) out.push({ key: `loc:${L.id}`, name: L.name, kind: "loc", id: L.id });
    return out;
  }, [yardAvailable, locs]);

  // Selected source: explicit ?source=, else home, else first available.
  const selKey = params.get("source") || home || sources[0]?.key || "";
  const selected = sources.find((s) => s.key === selKey) || sources[0] || null;

  const setHomeSource = useCallback((key: string) => {
    const fd = new FormData(); fd.append("source", key);
    fetch(`${API}/home`, { method: "POST", body: fd }).then(() => setHome(key)).catch(() => {});
  }, []);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".5rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
          <Bird size={24} strokeWidth={1.8} /> Birds
        </h2>
        {!PUBLIC && (
          <Link to="/apps/birds/settings" className="btn sm">
            <SlidersHorizontal size={15} /> Settings
          </Link>
        )}
      </div>

      {/* Storage warning/error banner — present only when recording needs attention. */}
      {!PUBLIC && <RecordingsBanner />}

      {/* Actions */}
      {!PUBLIC && (
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap", margin: ".25rem 0 1rem" }}>
          <ListenButton />
          <Link to="/apps/birds/locations" className="btn"><List size={16} /> Locations</Link>
          <Link to="/apps/birds/devices" className="btn"><Smartphone size={16} /> Devices</Link>
        </div>
      )}

      {!PUBLIC && (rec.recording || rec.wasListening) && <LivePanel />}

      {/* Source switch (segmented) + a clear "set as home" for the current view */}
      {sources.length > 1 && (
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem", flexWrap: "wrap", marginBottom: "1rem" }}>
          <div className="choice">
            {sources.map((s) => (
              <button key={s.key} className={s.key === selected?.key ? "active" : ""} onClick={() => setParams({ source: s.key })}>
                {s.kind === "yard" ? <Radio size={11} style={{ verticalAlign: "-1px", marginRight: ".2rem" }} /> : null}
                {s.name}{s.key === home ? " ★" : ""}
              </button>
            ))}
          </div>
          {selected && selected.key !== home && (
            <button className="btn sm" onClick={() => setHomeSource(selected.key)}>
              <Star size={13} /> Set as home
            </button>
          )}
        </div>
      )}

      {selected
        ? <Dashboard source={selected} nb={nb} />
        : !rec.recording && (
          <div className="card" style={{ padding: "1.5rem", textAlign: "center" }}>
            <p className="muted" style={{ margin: 0 }}>
              No sources yet. Tap <strong>Listen</strong> to detect birds from this device, or set up the yard node.
            </p>
          </div>
        )}
    </div>
  );
}

// --- Listen button + optional "adopt existing device" picker -------------
type KnownDevice = { token: string; name: string };

function ListenButton() {
  const rec = useRecorder();
  const [adopting, setAdopting] = useState(false);
  const [devices, setDevices] = useState<KnownDevice[] | null>(null);

  if (rec.recording) {
    return <button className="btn" onClick={rec.stop}><Square size={15} /> Stop listening</button>;
  }
  if (rec.wasListening) {
    return <button className="btn" onClick={rec.start}><Mic size={16} /> Resume listening</button>;
  }

  const openAdopt = () => {
    setAdopting(true);
    if (!devices) {
      jget<{ devices: KnownDevice[] }>("/devices")
        .then((r) => setDevices(r.devices.filter((d) => d.token !== rec.device.token)))
        .catch(() => setDevices([]));
    }
  };

  if (adopting) {
    return (
      <span style={{ display: "inline-flex", gap: ".4rem", alignItems: "center", flexWrap: "wrap" }}>
        {devices === null
          ? <Loader2 size={14} className="muted" style={{ animation: "spin 1s linear infinite" }} />
          : devices.length === 0
            ? <span className="muted" style={{ fontSize: ".85rem" }}>No other registered devices.</span>
            : devices.map((d) => (
                <button key={d.token} className="btn" onClick={() => { rec.adoptDevice(d.token, d.name); setAdopting(false); rec.start(); }}>
                  <Smartphone size={14} /> {d.name}
                </button>
              ))
        }
        <button className="btn" onClick={() => setAdopting(false)}><X size={14} /></button>
      </span>
    );
  }

  return (
    <span style={{ display: "inline-flex", gap: ".4rem", alignItems: "center" }}>
      <button className="btn" onClick={rec.start} disabled={rec.analyzerUp === false}>
        <Mic size={16} /> Listen on this device
      </button>
      {!rec.device.name && (
        <button className="btn" title="This is a new browser — adopt an existing device?" onClick={openAdopt}>
          <Smartphone size={14} />
        </button>
      )}
    </span>
  );
}

// --- Live panel: spectrogram + now hearing + session species -------------
function LivePanel() {
  const rec = useRecorder();
  const [showNonBird] = useShowNonBird();
  const paused = !rec.recording; // wasListening but not capturing (after a reload)
  const species = showNonBird ? rec.sessionSpecies : rec.sessionSpecies.filter((s) => s.is_bird);
  const hearing = (showNonBird ? rec.nowHearing : rec.nowHearing.filter((d) => d.is_bird))
    .sort((a, b) => b.confidence - a.confidence);

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem", border: "1px solid color-mix(in srgb, var(--accent) 33%, transparent)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", marginBottom: ".6rem", flexWrap: "wrap" }}>
        {paused
          ? <Radio size={16} className="muted" />
          : rec.status === "analyzing" ? <Loader2 size={16} className="spin" /> : <Radio size={16} style={{ color: "var(--ok)" }} />}
        <strong>{rec.device.name || "This device"}</strong>
        <span className="muted" style={{ fontSize: ".85rem" }}>
          {paused ? "· paused" : rec.location ? `· ${rec.location}` : "· locating…"}
        </span>
        {paused ? (
          <span style={{ marginLeft: "auto", display: "inline-flex", gap: ".4rem" }}>
            <button className="btn" onClick={rec.start}><Mic size={14} /> Resume</button>
            <button className="btn" onClick={rec.stop} title="Dismiss"><X size={14} /></button>
          </span>
        ) : <button className="btn" onClick={rec.stop} style={{ marginLeft: "auto" }}><Square size={14} /> Stop</button>}
      </div>

      <Spectrogram height={150} />

      {rec.needsLocation && <SetLocationInline />}

      <div style={{ minHeight: 26, margin: ".6rem 0 .2rem" }}>
        {paused ? (
          <span className="muted" style={{ fontSize: ".82rem" }}>Paused. Resume to keep listening.</span>
        ) : hearing.length > 0 ? (
          <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: ".8rem" }}>Now:</span>
            {hearing.slice(0, 4).map((d, i) => (
              <span key={i} className="pill ok">
                {d.common_name} {pct(d.confidence)}
              </span>
            ))}
          </div>
        ) : <span className="muted" style={{ fontSize: ".82rem" }}>Listening…</span>}
      </div>

      {species.length > 0 && (
        <>
          <div className="muted" style={{ fontSize: ".78rem", margin: ".5rem 0 .3rem" }}>Detected this session</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>
            {species.map((s) => (
              <SpeciesLink key={s.scientific_name} sci={s.scientific_name}>
                <span className="pill neutral" style={s.is_bird ? { cursor: "pointer" } : { opacity: 0.65, borderStyle: "dashed", cursor: "pointer" }}>
                  {s.common_name} <span className="muted">{pct(s.max_conf)}{s.hits > 1 ? ` ·${s.hits}` : ""}</span>
                </span>
              </SpeciesLink>
            ))}
          </div>
        </>
      )}
      {rec.err && <p className="error" style={{ marginBottom: 0 }}>{rec.err}</p>}
      {rec.analyzerUp === false && <p className="error" style={{ marginBottom: 0 }}>Analyzer service is not running on the box.</p>}
    </section>
  );
}

// Shown while listening when this device has no real location to file
// detections under (adaptive device, GPS denied/unavailable). Picking a place
// creates the location and pins this device to it so detections start saving.
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
          {busy ? "Saving location…" : "No location for this device yet — pick where you are so detections save."}
        </span>
      </div>
      <AddressPicker onPick={pick} />
    </div>
  );
}

// --- Dashboard: stats + activity + species for any source ----------------
type Norm = {
  species: number; detections: number; today: number; active_days: number;
  last: { common_name: string; confidence: number; at: string } | null;
  daily: { date: string; count: number }[];
  hourly: { hour: number; count: number }[];
  speciesList: { scientific_name: string; common_name: string; count: number; max_conf: number; days_seen?: number; last_date?: string }[];
  lat?: number | null; lon?: number | null;
};

type TrustStatus = "confirmed" | "suggested" | "rejected";

function Dashboard({ source, nb }: { source: Source; nb: string }) {
  const [d, setD] = useState<Norm | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [status, setStatus] = useState<TrustStatus>("confirmed");
  const [newCount, setNewCount] = useState(0);
  const st = `&status=${status}`;

  const load = useCallback(() => {
    if (source.kind === "loc" && source.id) {
      const id = source.id;
      Promise.all([
        // Stats + activity always reflect real (confirmed) data; only the
        // species list below is re-filtered by the chosen status.
        jget<any>(`/location/summary?location_id=${id}${nb}`),
        jget<any>(`/location/activity?location_id=${id}${nb}`),
        jget<{ species: any[] }>(`/location/species?location_id=${id}${nb}${st}`),
      ]).then(([s, a, sp]) => setD({
        species: s.species, detections: s.detections, today: s.today, active_days: s.active_days,
        last: s.last_detection, daily: a.daily, hourly: a.hourly, speciesList: sp.species,
        lat: s.lat, lon: s.lon,
      })).catch((e) => setErr(String(e)));
    } else {
      Promise.all([
        jget<any>(`/summary?_=1${nb}`),
        jget<{ hourly: any[] }>(`/activity/hourly?days=30${nb}`),
        jget<{ daily: any[] }>(`/activity/daily?days=45${nb}`),
        jget<{ species: any[] }>(`/species?_=1${nb}`),
      ]).then(([s, h, dd, sp]) => setD({
        species: s.species_count, detections: s.total_detections, today: s.today?.detections || 0,
        active_days: s.active_days || 0,
        last: s.last_detection ? { common_name: s.last_detection.common_name, confidence: s.last_detection.confidence, at: s.last_detection.time } : null,
        daily: dd.daily, hourly: h.hourly, speciesList: sp.species, lat: null, lon: null,
      })).catch((e) => setErr(String(e)));
    }
    jget<{ new_count: number }>(`/new-count`).then((r) => setNewCount(r.new_count)).catch(() => {});
  }, [source.kind, source.id, nb, st]);

  useEffect(() => { setD(null); setErr(null); load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  if (err && !d) return <p className="error">Could not load: {err}</p>;
  if (!d) return <p className="muted">Loading…</p>;
  const isLoc = source.kind === "loc";
  const locId = isLoc ? source.id : undefined;

  return (
    <>
      <StatGrid>
        <Stat label="Species" value={d.species} />
        <Stat label="Detections" value={d.detections.toLocaleString()} />
        <Stat label="Today" value={d.today} />
        <Stat label="Active days" value={d.active_days} />
      </StatGrid>
      {d.last && (
        <p className="muted" style={{ margin: "0 0 1rem" }}>
          Last: <strong>{d.last.common_name}</strong> at {d.last.at} ({pct(d.last.confidence)})
        </p>
      )}

      {(d.daily.length > 0 || d.hourly.some((h) => h.count > 0)) && (
        <ActivityCard daily={d.daily} hourly={d.hourly} />
      )}

      <section className="card" style={card}>
        <h3 style={h3}>Species ({d.speciesList.length})</h3>
        {isLoc ? (
          <StatusFilter status={status} setStatus={setStatus} newCount={newCount} />
        ) : null}
        {isLoc && status === "suggested" && (
          <p className="muted" style={{ fontSize: ".8rem", margin: "0 0 .6rem" }}>
            Birds heard here but not yet vouched for. Tap a row to inspect its
            detections; confirm the real ones (future detections auto-confirm).
            No need to decide now — looking is enough.
          </p>
        )}
        {d.speciesList.length === 0 && (
          <p className="muted">
            {!isLoc ? "Nothing yet."
              : status === "confirmed" ? "Nothing confirmed yet — check Suggested to review what's been heard."
              : status === "suggested" ? "No suggestions right now." : "Nothing rejected."}
          </p>
        )}
        <div style={{ display: "flex", flexDirection: "column" }}>
          {isLoc
            ? d.speciesList.map((s) => (
                <SpeciesRow key={s.scientific_name} s={s} locId={locId} status={status}
                  lat={d.lat} lon={d.lon} onChanged={load} />
              ))
            : d.speciesList.map((s) => (
                <SpeciesLink key={s.scientific_name} sci={s.scientific_name}>
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
    </>
  );
}

// Shared trust-status switch (Confirmed / Suggested / Rejected) with the new-
// arrival badge on Suggested. Used on the dashboard and the location page.
export function StatusFilter({ status, setStatus, newCount }: {
  status: TrustStatus; setStatus: (s: TrustStatus) => void; newCount: number;
}) {
  return (
    <div style={{ marginBottom: ".7rem" }}>
      <Segmented<TrustStatus>
        ariaLabel="Detection status"
        value={status}
        onChange={setStatus}
        options={[
          { value: "confirmed", label: "Confirmed" },
          { value: "suggested", label: (
            <span style={{ display: "inline-flex", alignItems: "center", gap: ".35rem" }}>
              Suggested
              {newCount > 0 && <span className="bird-badge">{newCount}</span>}
            </span>
          ) },
          { value: "rejected", label: "Rejected" },
        ]}
      />
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" };
