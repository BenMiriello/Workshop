import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronLeft, MapPin, Sunrise, TrendingUp, Radio, Star, Pencil, Check, X, Crosshair,
} from "lucide-react";
import { useRecorder } from "./recorder";
import { useShowNonBird, nonBirdQuery } from "./settings";
import { API, pct, hh, jget, Stat, StatGrid, Chart, BirdThumb, SpeciesLink } from "./ui";

// Everything heard at one place: the devices reporting here, summary, activity
// over time and by hour, and the species list (each opens the bird page scoped
// to this location). You can rename the place, pin its coordinates (so adaptive
// devices merge into it), mark it home, or start listening here.

const REFRESH_MS = 8000;

type Summary = {
  id: number; name: string; lat: number | null; lon: number | null; address: string | null;
  devices: string[]; detections: number; species: number; first_date: string | null;
  last_date: string | null; today: number; active_days: number;
  last_detection: { common_name: string; scientific_name: string; confidence: number; at: string } | null;
};
type Species = {
  scientific_name: string; common_name: string; count: number; max_conf: number;
  avg_conf: number; days_seen: number; first_date: string; last_date: string;
};
type Activity = { daily: { date: string; count: number }[]; hourly: { hour: number; count: number }[] };

export default function LocationDetail() {
  const [params] = useSearchParams();
  const id = Number(params.get("id") || 0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [species, setSpecies] = useState<Species[]>([]);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [home, setHome] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);
  const { recording, location: liveLoc } = useRecorder();

  const refresh = useCallback(() => {
    if (!id) return;
    Promise.all([
      jget<Summary>(`/location/summary?location_id=${id}${nb}`),
      jget<{ species: Species[] }>(`/location/species?location_id=${id}${nb}`),
      jget<Activity>(`/location/activity?location_id=${id}${nb}`),
      jget<{ home: string | null }>(`/home`),
    ]).then(([s, sp, a, h]) => { setSummary(s); setSpecies(sp.species); setActivity(a); setHome(h.home); })
      .catch((e) => setErr(String(e)));
  }, [id, nb]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const peak = useMemo(() => {
    if (!activity?.hourly?.length) return null;
    const m = activity.hourly.reduce((a, b) => (b.count > a.count ? b : a), activity.hourly[0]);
    return m.count > 0 ? m : null;
  }, [activity]);

  const setAsHome = useCallback(() => {
    const fd = new FormData(); fd.append("source", `loc:${id}`);
    fetch(`${API}/home`, { method: "POST", body: fd }).then(() => setHome(`loc:${id}`)).catch(() => {});
  }, [id]);

  if (!id) return <Wrap><p className="error">No location selected.</p></Wrap>;
  if (err && !summary) return <Wrap><p className="error">Could not load: {err}</p></Wrap>;
  if (!summary) return <Wrap><p className="muted">Loading…</p></Wrap>;

  const isHome = home === `loc:${id}`;
  const liveHere = recording && liveLoc === summary.name;

  return (
    <Wrap name={summary.name} isHome={isHome} onHome={setAsHome} onEdit={() => setEditing((v) => !v)}>
      {editing && <EditLocation summary={summary} onDone={() => { setEditing(false); refresh(); }} />}

      {summary.devices.length > 0 && (
        <p className="muted" style={{ margin: "0 0 .8rem", fontSize: ".85rem" }}>
          <Radio size={13} style={{ verticalAlign: "-2px" }} /> Devices here: {summary.devices.join(", ")}
          {liveHere && <span style={{ color: "#16a34a", marginLeft: ".4rem" }}>· listening now</span>}
        </p>
      )}

      <StatGrid>
        <Stat label="Species" value={summary.species} />
        <Stat label="Detections" value={summary.detections.toLocaleString()} />
        <Stat label="Today" value={summary.today} />
        <Stat label="Active days" value={summary.active_days} />
      </StatGrid>

      {summary.last_detection && (
        <p className="muted" style={{ margin: "0 0 1rem" }}>
          Last: <strong>{summary.last_detection.common_name}</strong> at {summary.last_detection.at} ({pct(summary.last_detection.confidence)})
        </p>
      )}

      {activity && activity.daily.length > 0 && (
        <section className="card" style={card}>
          <h3 style={h3}><TrendingUp size={17} /> Detections per day</h3>
          <Chart data={activity.daily} x="date" fill="#34d399" xfmt={(dd: string) => dd.slice(5)} />
        </section>
      )}

      {activity && (
        <section className="card" style={card}>
          <h3 style={h3}><Sunrise size={17} /> Activity by hour
            {peak && <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}> · peak {hh(peak.hour)}</span>}
          </h3>
          <Chart data={activity.hourly} x="hour" fill="#4ea1ff" xfmt={(h: number) => String(h)} interval={1} labelFmt={hh} />
        </section>
      )}

      <section className="card" style={card}>
        <h3 style={h3}>Species ({species.length})</h3>
        {species.length === 0 && <p className="muted">Nothing detected here yet.</p>}
        <div style={{ display: "flex", flexDirection: "column" }}>
          {species.map((s) => (
            <SpeciesLink key={s.scientific_name} sci={s.scientific_name} locationId={id}>
              <div style={{ display: "flex", alignItems: "center", gap: ".7rem", padding: ".5rem 0", borderTop: "1px solid var(--border,#1e2636)" }}>
                <BirdThumb sci={s.scientific_name} common={s.common_name} />
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ display: "block" }}>{s.common_name}</span>
                  <span className="muted" style={{ fontStyle: "italic", fontSize: ".78rem" }}>{s.scientific_name}</span>
                </span>
                <span className="muted" style={{ fontSize: ".8rem", textAlign: "right" }}>
                  {s.count}× · {s.days_seen}d<br />best {pct(s.max_conf)}
                </span>
              </div>
            </SpeciesLink>
          ))}
        </div>
      </section>
    </Wrap>
  );
}

function EditLocation({ summary, onDone }: { summary: Summary; onDone: () => void }) {
  const [name, setName] = useState(summary.name);
  const [lat, setLat] = useState(summary.lat != null ? String(summary.lat) : "");
  const [lon, setLon] = useState(summary.lon != null ? String(summary.lon) : "");
  const [address, setAddress] = useState(summary.address || "");
  const [busy, setBusy] = useState(false);

  const useCurrent = () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(
      (p) => { setLat(p.coords.latitude.toFixed(5)); setLon(p.coords.longitude.toFixed(5)); },
      () => {}, { enableHighAccuracy: true, timeout: 8000 });
  };

  const save = () => {
    setBusy(true);
    const fd = new FormData();
    fd.append("location_id", String(summary.id));
    fd.append("name", name);
    if (lat) fd.append("lat", lat);
    if (lon) fd.append("lon", lon);
    fd.append("address", address);
    fetch(`${API}/location`, { method: "POST", body: fd }).finally(() => { setBusy(false); onDone(); });
  };

  return (
    <section className="card" style={{ ...card, border: "1px solid #1f6feb55" }}>
      <h3 style={h3}>Edit location</h3>
      <div style={{ display: "grid", gap: ".5rem", maxWidth: 420 }}>
        <label className="muted" style={lbl}>Name
          <input className="num-in" value={name} onChange={(e) => setName(e.target.value)} style={inp} />
        </label>
        <label className="muted" style={lbl}>Street address (optional)
          <input className="num-in" value={address} placeholder="123 Main St" onChange={(e) => setAddress(e.target.value)} style={inp} />
        </label>
        <div style={{ display: "flex", gap: ".5rem" }}>
          <label className="muted" style={{ ...lbl, flex: 1 }}>Latitude
            <input className="num-in" value={lat} onChange={(e) => setLat(e.target.value)} style={inp} />
          </label>
          <label className="muted" style={{ ...lbl, flex: 1 }}>Longitude
            <input className="num-in" value={lon} onChange={(e) => setLon(e.target.value)} style={inp} />
          </label>
        </div>
        <p className="muted" style={{ fontSize: ".75rem", margin: 0 }}>
          Pinning coordinates lets adaptive devices within ~40 m merge into this location automatically.
        </p>
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="pill" onClick={useCurrent} style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            <Crosshair size={14} /> Use my current location
          </button>
          <button className="pill" onClick={save} disabled={busy}
            style={{ cursor: "pointer", background: "#1f6feb", color: "#fff", borderColor: "#1f6feb", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            <Check size={14} /> Save
          </button>
          <button className="pill" onClick={onDone} style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            <X size={14} /> Cancel
          </button>
        </div>
      </div>
    </section>
  );
}

function Wrap({ children, name, isHome, onHome, onEdit }: {
  children: React.ReactNode; name?: string; isHome?: boolean; onHome?: () => void; onEdit?: () => void;
}) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".5rem", marginBottom: ".75rem" }}>
        <Link to="/apps/birds/locations" className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Locations
        </Link>
        {name && (
          <div style={{ display: "flex", gap: ".6rem" }}>
            <button onClick={onHome} title={isHome ? "Home" : "Set as home"} className="muted"
              style={{ background: "none", border: "none", cursor: "pointer", color: isHome ? "#f5c518" : undefined, display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".82rem" }}>
              <Star size={15} fill={isHome ? "#f5c518" : "none"} /> {isHome ? "Home" : "Set home"}
            </button>
            <button onClick={onEdit} className="muted" style={{ background: "none", border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".82rem" }}>
              <Pencil size={14} /> Edit
            </button>
          </div>
        )}
      </div>
      {name && (
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
          <MapPin size={22} strokeWidth={1.8} /> {name}
        </h2>
      )}
      {children}
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" };
const lbl: React.CSSProperties = { display: "flex", flexDirection: "column", gap: ".2rem", fontSize: ".78rem" };
const inp: React.CSSProperties = { marginTop: ".1rem" };
