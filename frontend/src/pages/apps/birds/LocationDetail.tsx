import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronLeft, MapPin, Sunrise, TrendingUp, Radio, Star, Pencil, Check, X, AlertTriangle,
} from "lucide-react";
import { useRecorder } from "./recorder";
import { useShowNonBird, nonBirdQuery } from "./settings";
import { API, pct, hh, jget, Stat, StatGrid, Chart, BirdThumb, SpeciesLink } from "./ui";
import AddressPicker from "./AddressPicker";
import type { Place } from "./geocode";

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

  // A location with no pinned place needs one - open the editor automatically.
  const autoOpened = useRef(false);
  useEffect(() => {
    if (summary && summary.lat == null && !autoOpened.current) { autoOpened.current = true; setEditing(true); }
  }, [summary]);

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

      {!editing && summary.lat == null && (
        <div className="card" style={{ padding: ".7rem .9rem", marginBottom: "1rem", border: "1px solid #f5a62355",
          display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <AlertTriangle size={16} style={{ color: "#f5a623" }} />
          <span style={{ flex: 1, fontSize: ".88rem" }}>This location has no real place yet.</span>
          <button className="pill" onClick={() => setEditing(true)} style={{ cursor: "pointer" }}>Set address</button>
        </div>
      )}

      {summary.devices.length > 0 && (
        <p className="muted" style={{ margin: "0 0 .8rem", fontSize: ".85rem", display: "flex", alignItems: "center", gap: ".3rem", flexWrap: "wrap" }}>
          <Radio size={13} style={{ verticalAlign: "-2px" }} /> Devices here:
          {summary.devices.map((dv, i) => (
            <Link key={dv} to="/apps/birds/devices" className="muted" style={{ textDecoration: "underline" }}>
              {dv}{i < summary.devices.length - 1 ? "," : ""}
            </Link>
          ))}
          {liveHere && <span style={{ color: "#16a34a" }}>· listening now</span>}
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
  const hasPlace = summary.lat != null && summary.lon != null;
  const [name, setName] = useState(summary.name && summary.name !== "Untitled location" ? summary.name : "");
  const [place, setPlace] = useState<Place | null>(
    hasPlace ? { short: summary.name, label: summary.address || "", lat: summary.lat!, lon: summary.lon! } : null);
  const [busy, setBusy] = useState(false);

  const save = () => {
    if (!place) return;
    setBusy(true);
    const fd = new FormData();
    fd.append("location_id", String(summary.id));
    fd.append("name", name.trim() || place.short);
    fd.append("lat", String(place.lat));
    fd.append("lon", String(place.lon));
    fd.append("address", place.label || place.short);
    fetch(`${API}/location`, { method: "POST", body: fd }).finally(() => { setBusy(false); onDone(); });
  };

  return (
    <section className="card" style={{ ...card, border: "1px solid #1f6feb55" }}>
      <h3 style={h3}>{hasPlace ? "Edit location" : "Set a real location"}</h3>
      <div style={{ display: "grid", gap: ".6rem", maxWidth: 460 }}>
        <div>
          <div className="muted" style={lbl}>Address or GPS</div>
          <AddressPicker value={place} onPick={setPlace} />
        </div>
        <label className="muted" style={lbl}>Display name (optional)
          <input className="num-in" value={name} placeholder={place?.short || "e.g. Home, Back deck"}
            onChange={(e) => setName(e.target.value)} style={inp} />
        </label>
        <p className="muted" style={{ fontSize: ".75rem", margin: 0 }}>
          A location must be a verified address or GPS point. Adaptive devices within ~40 m merge into it automatically.
        </p>
        <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
          <button className="pill" onClick={save} disabled={busy || !place}
            style={{ cursor: place ? "pointer" : "not-allowed", opacity: place ? 1 : 0.5,
              background: "#1f6feb", color: "#fff", borderColor: "#1f6feb", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            <Check size={14} /> Save
          </button>
          {hasPlace && (
            <button className="pill" onClick={onDone} style={{ cursor: "pointer", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
              <X size={14} /> Cancel
            </button>
          )}
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
