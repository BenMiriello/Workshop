import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronLeft, MapPin, ExternalLink } from "lucide-react";
import { useBirdInfo } from "./birdinfo";
import { pct, hh, jget, Stat, StatGrid, Chart, HourSpark } from "./ui";

// One species' page: photo + description (client-side reference lookup), the
// stats, every day it was heard with that day's hour-of-day mini-graph, where it
// was heard, and a recent list. Scoped to one location when arrived at from a
// location page, otherwise across all locations.

const REFRESH_MS = 8000;

type Detail = {
  scientific_name: string; common_name: string; count: number;
  first_date: string | null; last_date: string | null; days_seen: number;
  max_conf: number; avg_conf: number;
  hourly: { hour: number; count: number }[];
  by_day: { date: string; count: number; hourly: number[] }[];
  locations: { id: number; name: string; count: number }[];
  recent: { at: string; confidence: number; location: string | null; device: string | null }[];
};

export default function BirdDetail() {
  const [params] = useSearchParams();
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

  const common = d?.common_name || info?.common_name || sci;
  const peak = d?.hourly?.length ? d.hourly.reduce((a, b) => (b.count > a.count ? b : a), d.hourly[0]) : null;

  return (
    <div>
      <div style={{ marginBottom: ".75rem" }}>
        <Link to={locId ? `/apps/birds/location?id=${locId}` : "/apps/birds"} className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Back
        </Link>
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
            {locId && (
              <div className="muted" style={{ fontSize: ".82rem", marginBottom: ".5rem" }}>
                <MapPin size={12} style={{ verticalAlign: "-1px" }} /> at this location ·{" "}
                <Link to={`/apps/birds/bird?sci=${encodeURIComponent(sci)}`} className="muted" style={{ textDecoration: "underline" }}>
                  view across all locations
                </Link>
              </div>
            )}
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
          <StatGrid>
            <Stat label="Detections" value={d.count.toLocaleString()} />
            <Stat label="Days heard" value={d.days_seen} />
            <Stat label="Best" value={pct(d.max_conf)} />
            <Stat label="Avg" value={pct(d.avg_conf)} />
          </StatGrid>

          {d.locations.length > 1 && !locId && (
            <section className="card" style={card}>
              <h3 style={h3}>Where it was heard</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: ".3rem" }}>
                {d.locations.map((L) => (
                  <Link key={L.id} to={`/apps/birds/bird?sci=${encodeURIComponent(sci)}&location_id=${L.id}`}
                    className="card" style={{ padding: ".5rem .7rem", display: "flex", justifyContent: "space-between", textDecoration: "none", color: "inherit" }}>
                    <span><MapPin size={13} style={{ verticalAlign: "-2px" }} /> {L.name}</span>
                    <span className="muted">{L.count}×</span>
                  </Link>
                ))}
              </div>
            </section>
          )}

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
            <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 320, overflowY: "auto" }}>
              {d.recent.map((r, i) => (
                <li key={i} style={{ display: "flex", alignItems: "center", gap: ".6rem", padding: ".3rem 0", borderTop: "1px solid var(--border,#1e2636)", fontSize: ".84rem" }}>
                  <span className="mono muted" style={{ minWidth: 110 }}>{r.at}</span>
                  {!locId && r.location && <span className="muted" style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.location}</span>}
                  <span className="pill" style={{ marginLeft: "auto" }}>{pct(r.confidence)}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", fontSize: "1rem" };
