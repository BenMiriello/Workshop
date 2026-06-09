import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Mic, Square, Pencil, MapPin, Smartphone, Radio, Loader2, AlertTriangle, X } from "lucide-react";
import { useRecorder } from "./birds/recorder";
import BirdsHeader, { InlineSelect } from "./birds/BirdsHeader";
import Spectrogram from "./birds/Spectrogram";
import AddressPicker from "./birds/AddressPicker";
import type { Place } from "./birds/geocode";
import { useShowNonBird, nonBirdQuery } from "./birds/settings";
import { API, pct, jget, Stat, StatGrid, SpeciesLink, BirdThumb } from "./birds/ui";
import { RecordingsBanner } from "./birds/recordings";
import SpeciesRow from "./birds/SpeciesRow";
import ActivityCard from "./birds/ActivityCard";

// Birds home. One page, reshaped by two dropdown filters: which place (the
// always-on yard, or any device-created location) and which device. The live
// panel (this device's spectrogram + feed) appears while you're listening; the
// always-on yard's live feed is viewable on its own.
const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

type Loc = { id: number; name: string; detections: number; species: number; devices: string[] };
type KnownDevice = { token: string; name: string };
type Source = { key: string; name: string; kind: "yard" | "loc"; id?: number };

export default function Birds() {
  const [params, setParams] = useSearchParams();
  const [locs, setLocs] = useState<Loc[]>([]);
  const [devices, setDevices] = useState<KnownDevice[]>([]);
  const [home, setHome] = useState<string | null>(null);
  const [yardAvailable, setYardAvailable] = useState(false);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);
  const rec = useRecorder();

  const load = useCallback(() => {
    jget<{ locations: Loc[]; home: string | null }>(`/locations?_=1${nb}`)
      .then((d) => { setLocs(d.locations); setHome(d.home); }).catch(() => {});
    jget<{ devices: KnownDevice[] }>(`/devices`).then((d) => setDevices(d.devices)).catch(() => {});
    jget<{ available: boolean; total_detections: number }>(`/summary?_=1`)
      .then((s) => setYardAvailable(s.available && s.total_detections > 0)).catch(() => {});
  }, [nb]);
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);

  const sources = useMemo<Source[]>(() => {
    const out: Source[] = [];
    if (yardAvailable) out.push({ key: "yard", name: "Yard (always-on)", kind: "yard" });
    for (const L of locs) out.push({ key: `loc:${L.id}`, name: L.name, kind: "loc", id: L.id });
    return out;
  }, [yardAvailable, locs]);

  const selKey = params.get("source") || home || sources[0]?.key || "";
  const selected = sources.find((s) => s.key === selKey) || sources[0] || null;
  const deviceTok = params.get("device") || "";

  const setParam = (key: string, val: string) => {
    const p = new URLSearchParams(params);
    if (val) p.set(key, val); else p.delete(key);
    if (key === "source") p.delete("device"); // device filter is per-location
    setParams(p);
  };

  return (
    <div>
      <BirdsHeader title="Birds">
        {sources.length > 0 && (
          <>
            <InlineSelect
              ariaLabel="Place"
              icon={<MapPin size={15} />}
              value={selected?.key || ""}
              options={sources.map((s) => ({ value: s.key, label: s.name }))}
              onChange={(v) => setParam("source", v)}
            />
            {selected?.kind === "loc" && (
              <Link to={`/apps/birds/location?id=${selected.id}`} className="bird-act" title="Edit this place (name, coordinates, devices)">
                <Pencil size={14} />
              </Link>
            )}
            {selected?.kind === "loc" && devices.length > 1 && (
              <InlineSelect
                ariaLabel="Device"
                icon={<Smartphone size={15} />}
                value={deviceTok}
                options={[{ value: "", label: "All devices" }, ...devices.map((dv) => ({ value: dv.token, label: dv.name || "Untitled device" }))]}
                onChange={(v) => setParam("device", v)}
              />
            )}
          </>
        )}
      </BirdsHeader>

      {!PUBLIC && <RecordingsBanner />}

      {!PUBLIC && (rec.recording || rec.wasListening) && <LivePanel />}
      {!PUBLIC && yardAvailable && <YardLiveFeed nb={nb} />}

      {selected
        ? <Dashboard source={selected} nb={nb} device={selected.kind === "loc" ? deviceTok : ""} />
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

// --- Live panel: this device's spectrogram + now hearing + session species ----
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
      {rec.err && <p className="error" style={{ marginBottom: 0 }}>{rec.err}</p>}
      {rec.analyzerUp === false && <p className="error" style={{ marginBottom: 0 }}>Analyzer service is not running on the box.</p>}
    </section>
  );
}

// The always-on yard's live feed: the latest detections it is logging right now.
// No spectrogram (we don't have the yard's audio) — just the rolling list, so you
// can watch what the house mic is hearing without listening on this device.
type YardDet = { id: number; time: string; common_name: string; scientific_name: string; confidence: number };
function t12(t: string): string {
  const [h, m] = (t || "").split(":").map(Number);
  if (Number.isNaN(h)) return t;
  const ap = h < 12 ? "AM" : "PM";
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, "0")} ${ap}`;
}
function YardLiveFeed({ nb }: { nb: string }) {
  const [open, setOpen] = useState(false);
  const [dets, setDets] = useState<YardDet[]>([]);
  useEffect(() => {
    if (!open) return;
    const load = () => jget<{ detections: YardDet[] }>(`/detections?limit=8${nb}`)
      .then((d) => setDets(d.detections)).catch(() => {});
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [open, nb]);

  return (
    <section className="card" style={{ padding: ".7rem 1rem", marginBottom: "1rem" }}>
      <button type="button" onClick={() => setOpen((v) => !v)}
        style={{ display: "flex", alignItems: "center", gap: ".5rem", width: "100%", background: "none", border: "none", padding: 0, cursor: "pointer", color: "inherit" }}>
        <Radio size={15} style={{ color: "var(--ok)" }} />
        <strong style={{ fontSize: ".92rem" }}>Yard live feed</strong>
        <span className="muted" style={{ fontSize: ".8rem", marginLeft: "auto" }}>{open ? "Hide" : "Show what the always-on mic hears"}</span>
      </button>
      {open && (
        <div style={{ marginTop: ".5rem" }}>
          {dets.length === 0
            ? <p className="muted" style={{ fontSize: ".82rem", margin: 0 }}>Nothing in the last few minutes.</p>
            : dets.map((r) => (
              <div key={r.id} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".35rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
                <span className="mono muted" style={{ minWidth: 78, flexShrink: 0 }}>{t12(r.time)}</span>
                <SpeciesLink sci={r.scientific_name} common={r.common_name} style={{ flex: 1, minWidth: 0 }}>{r.common_name}</SpeciesLink>
                <span className="muted" style={{ fontSize: ".78rem" }}>{pct(r.confidence)}</span>
              </div>
            ))}
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
  const [status, setStatus] = useState<ListStatus>("active");
  const [newCount, setNewCount] = useState(0);
  const dev = device ? `&device=${encodeURIComponent(device)}` : "";

  const load = useCallback(() => {
    if (source.kind === "loc" && source.id) {
      const id = source.id;
      Promise.all([
        jget<any>(`/location/summary?location_id=${id}${nb}${dev}`),
        jget<any>(`/location/activity?location_id=${id}${nb}${dev}`),
        jget<{ species: any[] }>(`/location/species?location_id=${id}${nb}${dev}&status=${status}`),
      ]).then(([s, a, sp]) => setD({
        species: s.species, detections: s.detections, today: s.today, active_days: s.active_days,
        daily: a.daily, hourly: a.hourly, speciesList: sp.species, lat: s.lat, lon: s.lon,
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
        daily: dd.daily, hourly: h.hourly, speciesList: sp.species, lat: null, lon: null,
      })).catch((e) => setErr(String(e)));
    }
    jget<{ new_count: number }>(`/new-count`).then((r) => setNewCount(r.new_count)).catch(() => {});
  }, [source.kind, source.id, nb, dev, status]);

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

      {(d.daily.length > 0 || d.hourly.some((h) => h.count > 0)) && (
        <ActivityCard daily={d.daily} hourly={d.hourly} />
      )}

      <section className="card" style={card}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".5rem" }}>
          <h3 style={{ ...h3, margin: 0 }}>
            Species ({d.speciesList.length})
            {isLoc && newCount > 0 && <span className="bird-badge" style={{ marginLeft: ".4rem" }}>{newCount} new</span>}
          </h3>
          {isLoc && (
            <select className="num-in" style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value as ListStatus)}>
              <option value="active">All active</option>
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
    </>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" };
