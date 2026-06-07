import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { MapPin, Smartphone, ExternalLink } from "lucide-react";
import { useCrumbTrail, type Crumb } from "../../../components/appNav";
import { useBirdInfo } from "./birdinfo";
import { pct, hh, jget, Stat, StatGrid, Chart, HourSpark, CHART_SERIES } from "./ui";

// One species' page: photo + description (client-side reference lookup), stats,
// every day it was heard with that day's hour-of-day mini-graph, and a recent
// list. Filterable by location, device, and date range - all calm selects, with
// location being just one dimension. Breadcrumbs give one-click navigation up.

const REFRESH_MS = 8000;

type LocOpt = { id: number; name: string; count: number };
type DevOpt = { token: string; name: string; count: number };
type Detail = {
  scientific_name: string; common_name: string; count: number;
  first_date: string | null; last_date: string | null; days_seen: number;
  max_conf: number; avg_conf: number; total_all: number;
  occurrence: number | null;
  hourly: { hour: number; count: number }[];
  by_day: { date: string; count: number; hourly: number[] }[];
  locations: LocOpt[]; devices: DevOpt[];
  recent: { at: string; confidence: number; occurrence: number | null; location_id: number | null; location: string | null; device: string | null }[];
};

export default function BirdDetail() {
  const [params, setParams] = useSearchParams();
  const sci = params.get("sci") || "";
  const locId = params.get("location_id") || "";
  const device = params.get("device") || "";
  const days = params.get("days") || "0";
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const info = useBirdInfo(sci || null, null);

  useEffect(() => {
    if (!sci) return;
    const qs = new URLSearchParams({ sci });
    if (locId) qs.set("location_id", locId);
    if (device) qs.set("device", device);
    if (days !== "0") qs.set("days", days);
    const load = () => jget<Detail>(`/bird?${qs.toString()}`).then(setD).catch((e) => setErr(String(e)));
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [sci, locId, device, days]);

  const setFilter = (key: string, val: string) => {
    const p = new URLSearchParams(params);
    if (val && val !== "0") p.set(key, val); else p.delete(key);
    setParams(p);
  };

  const common = d?.common_name || info?.common_name || sci;
  const peak = d?.hourly?.length ? d.hourly.reduce((a, b) => (b.count > a.count ? b : a), d.hourly[0]) : null;
  const place = locId ? d?.locations.find((l) => String(l.id) === locId)?.name : null;

  const trail: Crumb[] = [
    { label: "Apps", to: "/apps" },
    { label: "Birds", to: "/apps/birds" },
  ];
  if (locId) {
    trail.push({ label: "Locations", to: "/apps/birds/locations" });
    if (place) trail.push({ label: place, to: `/apps/birds/location?id=${locId}` });
  }
  trail.push({ label: common });
  useCrumbTrail(trail); // structurally dynamic → feed the toolbar directly

  const showLoc = (d?.locations.length || 0) > 1 || !!locId;
  const showDev = (d?.devices.length || 0) > 1 || !!device;

  return (
    <div>
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
          {/* Filters: location is one dimension among device + date range */}
          {(showLoc || showDev) && (
            <div className="filters">
              {showLoc && (
                <label>Location
                  <select className="num-in" value={locId} onChange={(e) => setFilter("location_id", e.target.value)}>
                    <option value="">All locations ({d.total_all})</option>
                    {d.locations.map((L) => <option key={L.id} value={L.id}>{L.name} ({L.count})</option>)}
                  </select>
                </label>
              )}
              {showDev && (
                <label>Device
                  <select className="num-in" value={device} onChange={(e) => setFilter("device", e.target.value)}>
                    <option value="">All devices</option>
                    {d.devices.map((D) => <option key={D.token} value={D.token}>{D.name} ({D.count})</option>)}
                  </select>
                </label>
              )}
              <label>When
                <select className="num-in" value={days} onChange={(e) => setFilter("days", e.target.value)}>
                  <option value="0">All time</option>
                  <option value="1">Today</option>
                  <option value="7">Last 7 days</option>
                  <option value="30">Last 30 days</option>
                </select>
              </label>
            </div>
          )}

          <StatGrid>
            <Stat label="Detections" value={d.count.toLocaleString()} />
            <Stat label="Days heard" value={d.days_seen} />
            <Stat label="Best" value={pct(d.max_conf)} />
            <Stat label="Avg" value={pct(d.avg_conf)} />
            {d.occurrence != null && (
              <Stat label="Local likelihood" value={pct(d.occurrence)} />
            )}
          </StatGrid>

          {d.count === 0 && <p className="muted">No detections for this filter.</p>}

          {d.count > 0 && (
            <>
              <section className="card" style={card}>
                <h3 style={h3}>Time of day{peak && peak.count > 0 && <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}> · peak {hh(peak.hour)}</span>}</h3>
                <Chart data={d.hourly} x="hour" fill={CHART_SERIES[0]} xfmt={(h: number) => String(h)} interval={2} labelFmt={hh} height={130} />
              </section>

              <section className="card" style={card}>
                <h3 style={h3}>Each day heard ({d.by_day.length})</h3>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  {d.by_day.map((day) => (
                    <div key={day.date} style={{ display: "flex", alignItems: "center", gap: ".8rem", padding: ".55rem 0", borderTop: "1px solid var(--border)" }}>
                      <div style={{ minWidth: 86 }}>
                        <div style={{ fontWeight: 600, fontSize: ".9rem" }}>{day.date.slice(5)}</div>
                        <div className="muted" style={{ fontSize: ".75rem" }}>{day.count}×</div>
                      </div>
                      <div style={{ flex: 1 }}><HourSpark hourly={day.hourly} fill={CHART_SERIES[1]} /></div>
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
                      <div title={title} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".4rem 0", borderTop: "1px solid var(--border)", fontSize: ".84rem" }}>
                        <span className="mono muted" style={{ minWidth: 104, flexShrink: 0 }}>{r.at}</span>
                        <span style={{ flex: 1, minWidth: 0, overflow: "hidden" }}>
                          {!locId && r.location && <span style={{ display: "inline-flex", alignItems: "center", gap: ".25rem" }}><MapPin size={11} className="muted" /> {r.location}</span>}
                          {r.device && <span className="muted" style={{ marginLeft: r.location && !locId ? ".5rem" : 0, display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".78rem" }}><Smartphone size={10} /> {r.device}</span>}
                        </span>
                        <span className="pill neutral" style={{ flexShrink: 0 }}>{pct(r.confidence)}</span>
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
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", fontSize: "1rem" };
