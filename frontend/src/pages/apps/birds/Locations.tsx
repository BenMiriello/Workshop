import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { MapPin, Star, Radio, Smartphone, RotateCcw } from "lucide-react";
import { useShowNonBird, nonBirdQuery } from "./settings";
import { API, jget } from "./ui";

// All the places birds have been heard. Mark one home (the app defaults to it);
// the yard/Pi shows up here once it is reporting. Also the way into per-device
// settings.

const REFRESH_MS = 8000;

type Loc = {
  id: number; name: string; lat: number | null; lon: number | null;
  detections: number; species: number; last_ts: string | null; devices: string[];
};

export default function Locations() {
  const [locs, setLocs] = useState<Loc[]>([]);
  const [removed, setRemoved] = useState<Loc[]>([]);
  const [home, setHome] = useState<string | null>(null);
  const [yardAvailable, setYardAvailable] = useState(false);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);

  const load = useCallback(() => {
    jget<{ locations: Loc[]; home: string | null }>(`/locations?_=1${nb}`)
      .then((d) => { setLocs(d.locations); setHome(d.home); }).catch(() => {});
    jget<{ locations: Loc[] }>(`/locations?archived=1${nb}`)
      .then((d) => setRemoved(d.locations)).catch(() => {});
    jget<{ available: boolean; total_detections: number }>(`/summary?_=1`)
      .then((s) => setYardAvailable(s.available && s.total_detections > 0)).catch(() => {});
  }, [nb]);

  useEffect(() => { load(); const t = setInterval(load, REFRESH_MS); return () => clearInterval(t); }, [load]);

  const setAsHome = useCallback((source: string) => {
    const fd = new FormData(); fd.append("source", source);
    fetch(`${API}/home`, { method: "POST", body: fd }).then(() => setHome(source)).catch(() => {});
  }, []);

  const restore = useCallback((id: number) => {
    const fd = new FormData(); fd.append("location_id", String(id));
    fetch(`${API}/location/restore`, { method: "POST", body: fd }).then(() => setTimeout(load, 150)).catch(() => {});
  }, [load]);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: ".75rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", margin: 0 }}>
          <MapPin size={22} strokeWidth={1.8} /> Locations
        </h2>
        <Link to="/apps/birds/devices" className="btn sm"><Smartphone size={14} /> Devices</Link>
      </div>

      <div style={{ display: "grid", gap: ".6rem" }}>
        {yardAvailable && (
          <SourceCard
            to="/apps/birds?source=yard" name="Yard (always-on)" sub="BirdNET-Go capture node"
            isHome={home === "yard"} onHome={() => setAsHome("yard")} icon={<Radio size={16} />} />
        )}
        {locs.map((L) => (
          <SourceCard
            key={L.id} to={`/apps/birds/location?id=${L.id}`}
            name={L.name}
            sub={`${L.species} species · ${L.detections} detections${L.devices.length ? ` · ${L.devices.join(", ")}` : ""}`}
            isHome={home === `loc:${L.id}`} onHome={() => setAsHome(`loc:${L.id}`)}
            needsAddress={L.lat == null}
            icon={<MapPin size={16} />} />
        ))}
        {locs.length === 0 && !yardAvailable && (
          <p className="muted">No locations yet. Start listening on a device to create one.</p>
        )}
      </div>

      {removed.length > 0 && (
        <section style={{ marginTop: "1.5rem" }}>
          <div className="muted" style={{ fontSize: ".78rem", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: ".4rem" }}>
            Removed locations
          </div>
          <div style={{ display: "grid", gap: ".5rem" }}>
            {removed.map((L) => (
              <div key={L.id} className="card" style={{ padding: ".6rem .9rem", display: "flex", alignItems: "center", gap: ".6rem", opacity: 0.85 }}>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>{L.name}</span>
                  <span className="muted" style={{ fontSize: ".76rem", display: "block" }}>
                    {L.species} species · {L.detections.toLocaleString()} detections kept
                  </span>
                </span>
                <button className="btn sm" onClick={() => restore(L.id)}><RotateCcw size={14} /> Restore</button>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

function SourceCard({ to, name, sub, isHome, onHome, icon, needsAddress }: {
  to: string; name: string; sub: string; isHome: boolean; onHome: () => void;
  icon: React.ReactNode; needsAddress?: boolean;
}) {
  return (
    <div className="card" style={{ padding: ".7rem .9rem", display: "flex", alignItems: "center", gap: ".7rem" }}>
      <span className="muted" style={{ flexShrink: 0 }}>{icon}</span>
      <Link to={to} style={{ flex: 1, minWidth: 0, textDecoration: "none", color: "inherit" }}>
        <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: ".4rem" }}>
          {name}
          {needsAddress && <span className="pill" style={{ fontSize: ".68rem", color: "var(--warn)", borderColor: "color-mix(in srgb, var(--warn) 33%, transparent)" }}>set address</span>}
        </div>
        <div className="muted" style={{ fontSize: ".8rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub}</div>
      </Link>
      <button onClick={onHome} title={isHome ? "Home" : "Set as home"}
        style={{ background: "none", border: "none", cursor: "pointer", color: isHome ? "var(--warn-solid)" : "var(--muted)", flexShrink: 0 }}>
        <Star size={18} fill={isHome ? "var(--warn-solid)" : "none"} />
      </button>
    </div>
  );
}
