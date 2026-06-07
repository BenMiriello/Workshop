import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ChevronLeft, MapPin, Smartphone, ExternalLink } from "lucide-react";
import { useBirdInfo } from "./birdinfo";
import { pct, hh, jget, Stat, StatGrid, Chart, HourSpark } from "./ui";

// One species' page: photo + description (client-side reference lookup), the
// stats, every day it was heard with that day's hour-of-day mini-graph, and a
// recent list. A location filter at the top scopes everything to one place or
// shows all - bidirectional, switchable in place.

const REFRESH_MS = 8000;

type Detail = {
  scientific_name: string; common_name: string; count: number;
  first_date: string | null; last_date: string | null; days_seen: number;
  max_conf: number; avg_conf: number; total_all_locations: number;
  hourly: { hour: number; count: number }[];
  by_day: { date: string; count: number; hourly: number[] }[];
  locations: { id: number; name: string; count: number }[];
  recent: { at: string; confidence: number; location_id: number | null; location: string | null; device: string | null }[];
};

export default function BirdDetail() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const sci = params.get("sci") || "";
  const locId = params.get("location_id");
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const info = useBirdInfo(sci || null, null);

  useEffect(() => {
    if (!sci) return;
    const load = () => jget<Detail>(`/bird?sci=${encodeURIComponent(sci)}${locId ? `&location_id=${locId}` : ""}`)
      .then(setD).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [sci, locId]);

  const setLocFilter = (id?: number) => {
    const p = new URLSearchParams(params);
    if (id) p.set("location_id", String(id)); else p.delete("location_id");
    setParams(p);
  };

  const common = d?.common_name || info?.common_name || sci;
  const peak = d?.hourly?.length ? d.hourly.reduce((a, b) => (b.count > a.count ? b : a), d.hourly[0]) : null;

  return (
    <div>
      <div style={{ marginBottom: ".75rem" }}>
        <button onClick={() => nav(-1)} className="muted"
          style={{ background: "none", border: "none", cursor: "pointer", display: "inline-flex", alignItems: "center", padding: 0, font: "inherit" }}>
          <ChevronLeft size={18} /> Back
        </button>
      </div>

      {/* Header card: photo + identity + description */}
      <section className="card" style={{ padding: 0, marginBottom: "1rem", overflow: "hidden" }}>
        <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
          {info?.photo && (
            <img src={info.photo} alt={common} style={{ width: 200, maxWidth: "100%", height: 200, objectFit: "cover" }} />
          )}
          <div style={{ flex: 1, minWidth: 220, padding: "1rem" }}>
            <h2 style={{ margin: "0 0 .15rem" }}>{common}</h2>
            <div className="muted" style={{ fontStyle: "italic", marginBottom: ".5rem" }}>{sci}</div>
            {info?.summary && <p style={{ margin: ".2rem 0 .5rem", fontSize: ".9rem", lineHeight: 1.45 }}>{info.summary}</p>}
            {info && !info.found && <p className="muted" style={{ fontSize: ".82rem" }}>No reference entry for this label.</p>}
            <div style={{ display: "flex", gap: ".8rem", flexWrap: "wrap", fontSize: ".72rem" }} className="muted">
              {info?.attribution && <span>Photo: {info.attribution}</span>}
              {info?.wikiUrl && <a href={info.wikiUrl} target="_blank" rel="noreferrer" className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".2rem" }}>Wikipedia <ExternalLink size={11} /></a>}
            </div>
          </div>
        </div>
      </section>

      {err && !d && <p className="error">Could not load: {err}</p>}
      {!d && !err && <p className="muted">Loading…</p>}

      {d && (
        <>
          {/* Location filter - scopes everything below; bidirectional */}
          {d.locations.length > 1 ? (
            <div style={{ display: "flex", gap: ".4rem", flexWrap: "wrap", marginBottom: "1rem", alignItems: "center" }}>
              <span className="muted" style={{ fontSize: ".78rem", marginRight: ".1rem" }}><MapPin size={12} style={{ verticalAlign: "-1px" }} /> Filter:</span>
              <FilterChip active={!locId} onClick={() => setLocFilter()}>All <span className="muted">{d.total_all_locations}</span></FilterChip>
              {d.locations.map((L) => (
                <FilterChip key={L.id} active={locId === String(L.id)} onClick={() => setLocFilter(L.id)}>
                  {L.name} <span className="muted">{L.count}</span>
                </FilterChip>
              ))}
            </div>
          ) : d.locations.length === 1 && (
            <p className="muted" style={{ margin: "0 0 1rem", fontSize: ".85rem" }}>
              <MapPin size={13} style={{ verticalAlign: "-2px" }} /> Heard at{" "}
              <Link to={`/apps/birds/location?id=${d.locations[0].id}`} className="muted" style={{ textDecoration: "underline" }}>{d.locations[0].name}</Link>
            </p>
          )}

          <StatGrid>
            <Stat label="Detections" value={d.count.toLocaleString()} />
            <Stat label="Days heard" value={d.days_seen} />
            <Stat label="Best" value={pct(d.max_conf)} />
            <Stat label="Avg" value={pct(d.avg_conf)} />
          </StatGrid>

          <section className="card" style={card}>
            <h3 style={h3}>Time of day{peak && peak.count > 0 && <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}> · peak {hh(peak.hour)}</span>}</h3>
            <Chart data={d.hourly} x="hour" fill="#a78bfa" xfmt={(h: number) => String(h)} interval={2} labelFmt={hh} height={130} />
          </section>

          <section className="card" style={card}>
            <h3 style={h3}>Each day heard ({d.by_day.length})</h3>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {d.by_day.map((day) => (
                <div key={day.date} style={{ display: "flex", alignItems: "center", gap: ".8rem", padding: ".55rem 0", borderTop: "1px solid var(--border,#1e2636)" }}>
                  <div style={{ minWidth: 86 }}>
                    <div style={{ fontWeight: 600, fontSize: ".9rem" }}>{day.date.slice(5)}</div>
                    <div className="muted" style={{ fontSize: ".75rem" }}>{day.count}×</div>
                  </div>
                  <div style={{ flex: 1 }}><HourSpark hourly={day.hourly} fill="#34d399" /></div>
                </div>
              ))}
            </div>
          </section>

          <section className="card" style={card}>
            <h3 style={h3}>Recent</h3>
            <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 360, overflowY: "auto" }}>
              {d.recent.map((r, i) => {
                const title = [r.location, r.device].filter(Boolean).join(" · ");
                const row = (
                  <div title={title} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".4rem 0", borderTop: "1px solid var(--border,#1e2636)", fontSize: ".84rem" }}>
                    <span className="mono muted" style={{ minWidth: 104, flexShrink: 0 }}>{r.at}</span>
                    <span style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                      {!locId && r.location && <span style={{ display: "inline-flex", alignItems: "center", gap: ".25rem" }}><MapPin size={11} className="muted" /> {r.location}</span>}
                      {r.device && <span className="muted" style={{ marginLeft: r.location && !locId ? ".5rem" : 0, display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".78rem" }}><Smartphone size={10} /> {r.device}</span>}
                    </span>
                    <span className="pill" style={{ flexShrink: 0 }}>{pct(r.confidence)}</span>
                  </div>
                );
                return r.location_id
                  ? <li key={i}><Link to={`/apps/birds/location?id=${r.location_id}`} style={{ textDecoration: "none", color: "inherit", display: "block" }}>{row}</Link></li>
                  : <li key={i}>{row}</li>;
              })}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button className="pill" onClick={onClick}
      style={{ cursor: "pointer", opacity: active ? 1 : 0.6,
        background: active ? "#1f6feb" : undefined, color: active ? "#fff" : undefined,
        borderColor: active ? "#1f6feb" : undefined }}>
      {children}
    </button>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", fontSize: "1rem" };
