import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronLeft, MapPin, Sunrise, TrendingUp, ChevronDown, ChevronRight, SlidersHorizontal } from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { useShowNonBird, nonBirdQuery } from "./settings";

// Per-location detail for a mic-test station: every species detected there with
// its stats, overall activity over time + by hour, and per-species drill-down
// (what time of day and which days each kind shows up). Auto-refreshes so it
// keeps updating while a device is listening.

const API = "/api/birds";
const REFRESH_MS = 8000;

type Summary = {
  station: string; detections: number; species: number; first_date: string | null;
  last_date: string | null; today: number; active_days: number;
  last_detection: { common_name: string; scientific_name: string; confidence: number; at: string } | null;
};
type Species = {
  scientific_name: string; common_name: string; count: number; max_conf: number;
  avg_conf: number; days_seen: number; first_date: string; last_date: string;
};
type Activity = { daily: { date: string; count: number; species: number }[]; hourly: { hour: number; count: number }[] };
type SpeciesDetail = {
  scientific_name: string; common_name: string; count: number;
  hourly: { hour: number; count: number }[]; daily: { date: string; count: number }[];
  recent: { at: string; confidence: number }[];
};

const pct = (c: number) => `${Math.round(c * 100)}%`;
const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

export default function StationDetail() {
  const [params] = useSearchParams();
  const station = params.get("s") || "";
  const q = encodeURIComponent(station);

  const [summary, setSummary] = useState<Summary | null>(null);
  const [species, setSpecies] = useState<Species[]>([]);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<SpeciesDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);

  const refresh = useCallback(() => {
    if (!station) return;
    Promise.all([
      jget<Summary>(`/station/summary?station=${q}${nb}`),
      jget<{ species: Species[] }>(`/station/species?station=${q}${nb}`),
      jget<Activity>(`/station/activity?station=${q}${nb}`),
    ]).then(([s, sp, a]) => { setSummary(s); setSpecies(sp.species); setActivity(a); })
      .catch((e) => setErr(String(e)));
  }, [station, q, nb]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const toggleSpecies = useCallback((sci: string) => {
    if (open === sci) { setOpen(null); setDetail(null); return; }
    setOpen(sci); setDetail(null);
    jget<SpeciesDetail>(`/station/species-detail?station=${q}&sci=${encodeURIComponent(sci)}`)
      .then(setDetail).catch(() => setDetail(null));
  }, [open, q]);

  // Refresh the open species detail on the same cadence.
  useEffect(() => {
    if (!open) return;
    const t = setInterval(() => {
      jget<SpeciesDetail>(`/station/species-detail?station=${q}&sci=${encodeURIComponent(open)}`)
        .then(setDetail).catch(() => {});
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [open, q]);

  const peak = useMemo(() => {
    if (!activity?.hourly?.length) return null;
    const m = activity.hourly.reduce((a, b) => (b.count > a.count ? b : a), activity.hourly[0]);
    return m.count > 0 ? m : null;
  }, [activity]);

  if (!station) return <Wrap><p className="error">No station selected.</p></Wrap>;
  if (err && !summary) return <Wrap><p className="error">Could not load: {err}</p></Wrap>;
  if (!summary) return <Wrap><p className="muted">Loading…</p></Wrap>;

  return (
    <Wrap station={station}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: ".7rem", marginBottom: "1rem" }}>
        <Stat label="Species" value={summary.species} />
        <Stat label="Detections" value={summary.detections.toLocaleString()} />
        <Stat label="Today" value={summary.today} />
        <Stat label="Active days" value={summary.active_days} />
      </div>
      {summary.last_detection && (
        <p className="muted" style={{ margin: "0 0 1rem" }}>
          Last: <strong>{summary.last_detection.common_name}</strong> at {summary.last_detection.at} ({pct(summary.last_detection.confidence)})
          <span style={{ marginLeft: ".5rem", color: "#16a34a" }}>• auto-refreshing</span>
        </p>
      )}

      {activity && activity.daily.length > 0 && (
        <section className="card" style={card}>
          <h3 style={h3}><TrendingUp size={17} /> Detections per day</h3>
          <Chart data={activity.daily} x="date" fill="#34d399" xfmt={(d: string) => d.slice(5)} />
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
            <div key={s.scientific_name} style={{ borderTop: "1px solid var(--border,#1e2636)" }}>
              <button onClick={() => toggleSpecies(s.scientific_name)}
                style={{ width: "100%", background: "none", border: "none", color: "inherit", cursor: "pointer",
                  display: "flex", alignItems: "center", gap: ".6rem", padding: ".55rem 0", textAlign: "left" }}>
                {open === s.scientific_name ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                <span style={{ flex: 1 }}>
                  <span>{s.common_name}</span>
                  <span className="muted" style={{ fontStyle: "italic", fontSize: ".78rem", display: "block" }}>{s.scientific_name}</span>
                </span>
                <span className="muted" style={{ fontSize: ".8rem", textAlign: "right" }}>
                  {s.count}× · {s.days_seen}d<br />best {pct(s.max_conf)}
                </span>
              </button>
              {open === s.scientific_name && (
                <div style={{ padding: "0 0 .8rem 1.4rem" }}>
                  {!detail ? <p className="muted">Loading…</p> : (
                    <>
                      <p className="muted" style={{ margin: ".2rem 0 .5rem", fontSize: ".82rem" }}>
                        First {s.first_date} · last {s.last_date} · avg conf {pct(s.avg_conf)}
                      </p>
                      <div className="muted" style={{ fontSize: ".75rem", margin: ".2rem 0" }}>Time of day</div>
                      <Chart data={detail.hourly} x="hour" fill="#a78bfa" xfmt={(h: number) => String(h)} interval={2} labelFmt={hh} height={110} />
                      {detail.daily.length > 1 && (
                        <>
                          <div className="muted" style={{ fontSize: ".75rem", margin: ".4rem 0 .2rem" }}>By day</div>
                          <Chart data={detail.daily} x="date" fill="#f59e0b" xfmt={(d: string) => d.slice(5)} height={110} />
                        </>
                      )}
                      <div className="muted" style={{ fontSize: ".75rem", margin: ".5rem 0 .2rem" }}>Recent</div>
                      <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 150, overflowY: "auto" }}>
                        {detail.recent.map((r, i) => (
                          <li key={i} style={{ display: "flex", justifyContent: "space-between", padding: ".2rem 0", fontSize: ".82rem" }}>
                            <span className="mono muted">{r.at}</span><span className="pill">{pct(r.confidence)}</span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </Wrap>
  );
}

function Chart({ data, x, fill, xfmt, interval, labelFmt, height = 150 }: {
  data: Record<string, unknown>[]; x: string; fill: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  xfmt?: (v: any) => string; interval?: number; labelFmt?: (v: any) => string; height?: number;
}) {
  return (
    <div style={{ height }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border,#222b3a)" />
          <XAxis dataKey={x} tick={{ fontSize: 10 }} tickFormatter={xfmt} interval={interval} minTickGap={16} />
          <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
          <Tooltip labelFormatter={labelFmt} />
          <Bar dataKey="count" fill={fill} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Wrap({ children, station }: { children: React.ReactNode; station?: string }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".5rem", marginBottom: ".75rem" }}>
        <Link to="/apps/birds" className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Yard Birds
        </Link>
        <Link to="/apps/birds/settings" className="muted"
          style={{ display: "inline-flex", alignItems: "center", gap: ".3rem", fontSize: ".85rem" }}>
          <SlidersHorizontal size={15} /> Settings
        </Link>
      </div>
      <h2 style={{ display: "flex", alignItems: "center", gap: ".5rem", marginTop: 0 }}>
        <MapPin size={22} strokeWidth={1.8} /> {station || "Location"}
      </h2>
      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: ".7rem 1rem" }}>
      <div className="muted" style={{ fontSize: ".78rem" }}>{label}</div>
      <div style={{ fontSize: "1.3rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const card: React.CSSProperties = { padding: "1rem", marginBottom: "1rem" };
const h3: React.CSSProperties = { margin: "0 0 .4rem", display: "flex", alignItems: "center", gap: ".4rem", fontSize: "1rem" };
