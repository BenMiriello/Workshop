import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bird, ChevronLeft, Sunrise, Volume2, TrendingUp, Sparkles, MapPin, SlidersHorizontal,
} from "lucide-react";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import MicTest from "./birds/MicTest";
import { useShowNonBird, nonBirdQuery } from "./birds/settings";

// Backyard bird-song dashboard. Reads the BirdNET-Go detection DB through the
// Workshop backend (/api/birds/*). PRIVATE build (this one) shows full fidelity:
// audio playback, exact detection times. The public Cloudflare build is a
// SEPARATE app fed only the sanitized aggregate dataset (no audio, no times,
// no location) - see birdnet/public-site. PUBLIC flips off the sensitive bits
// if this same component is ever reused there.
const PUBLIC = import.meta.env.VITE_PUBLIC === "1";

const API = "/api/birds";

type Summary = {
  available: boolean;
  total_detections: number;
  species_count: number;
  first_date?: string | null;
  last_date?: string | null;
  active_days?: number;
  today: { detections: number; species: number; date: string | null };
  last_detection: { date: string; time: string; common_name: string; scientific_name: string; confidence: number } | null;
};
type Species = {
  scientific_name: string; common_name: string; species_code: string;
  count: number; first_date: string; last_date: string; max_conf: number; avg_conf: number;
};
type Arrival = { scientific_name: string; common_name: string; first_date: string; count: number; max_conf: number };
type Detection = {
  id: number; date: string; time: string; common_name: string; scientific_name: string;
  species_code: string; confidence: number; clip_name: string | null;
};

const fmtPct = (c: number) => `${Math.round(c * 100)}%`;
const fmtDate = (d?: string | null) => (d ? d : "—");

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status}`);
  return r.json() as Promise<T>;
}

export default function Birds() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [species, setSpecies] = useState<Species[]>([]);
  const [arrivals, setArrivals] = useState<Arrival[]>([]);
  const [hourly, setHourly] = useState<{ hour: number; count: number }[]>([]);
  const [daily, setDaily] = useState<{ date: string; count: number; species: number }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [sort, setSort] = useState<"count" | "recent" | "conf">("count");
  const [err, setErr] = useState<string | null>(null);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);

  useEffect(() => {
    Promise.all([
      jget<Summary>(`/summary?_=1${nb}`),
      jget<{ species: Species[] }>(`/species?_=1${nb}`),
      jget<{ arrivals: Arrival[] }>(`/recent-arrivals?days=14${nb}`),
      jget<{ hourly: { hour: number; count: number }[] }>(`/activity/hourly?days=30${nb}`),
      jget<{ daily: { date: string; count: number; species: number }[] }>(`/activity/daily?days=45${nb}`),
    ])
      .then(([s, sp, ar, h, d]) => {
        setSummary(s); setSpecies(sp.species); setArrivals(ar.arrivals);
        setHourly(h.hourly); setDaily(d.daily);
      })
      .catch((e) => setErr(String(e)));
  }, [nb]);

  const loadDetections = useCallback((sci: string) => {
    setSelected(sci);
    jget<{ detections: Detection[] }>(`/detections?species=${encodeURIComponent(sci)}&limit=50${nb}`)
      .then((d) => setDetections(d.detections))
      .catch(() => setDetections([]));
  }, [nb]);

  const sortedSpecies = useMemo(() => {
    const s = [...species];
    if (sort === "count") s.sort((a, b) => b.count - a.count);
    else if (sort === "recent") s.sort((a, b) => b.last_date.localeCompare(a.last_date));
    else s.sort((a, b) => b.max_conf - a.max_conf);
    return s;
  }, [species, sort]);

  const peakHour = useMemo(() => {
    if (!hourly.length) return null;
    return hourly.reduce((m, h) => (h.count > m.count ? h : m), hourly[0]);
  }, [hourly]);

  if (err) return <Shell><p className="error">Could not load bird data: {err}</p></Shell>;
  if (!summary) return <Shell><p className="muted">Loading…</p></Shell>;

  if (!summary.available || summary.total_detections === 0) {
    return (
      <Shell>
        <div className="card" style={{ padding: "1.5rem" }}>
          <h3 style={{ marginTop: 0 }}>No detections yet</h3>
          <p className="muted">
            The capture node and BirdNET-Go are not reporting yet. Once the Pi is
            streaming and the Brain is detecting, species and activity will appear
            here automatically. Nothing to configure in this view.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <Shell>
      {/* Summary stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: "0.75rem", marginBottom: "1rem" }}>
        <Stat label="Species" value={summary.species_count} />
        <Stat label="Detections" value={summary.total_detections.toLocaleString()} />
        <Stat label="Today" value={`${summary.today.detections} · ${summary.today.species} sp`} />
        <Stat label="Active days" value={summary.active_days ?? 0} />
      </div>

      {summary.last_detection && (
        <p className="muted" style={{ margin: "0 0 1rem" }}>
          Last: <strong>{summary.last_detection.common_name}</strong> at{" "}
          {summary.last_detection.time} ({fmtPct(summary.last_detection.confidence)})
        </p>
      )}

      {/* Recent arrivals */}
      {arrivals.length > 0 && (
        <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
          <h3 style={{ margin: "0 0 0.6rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <Sparkles size={18} strokeWidth={1.8} /> Recent arrivals
          </h3>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
            {arrivals.map((a) => (
              <button key={a.scientific_name} className="pill" onClick={() => loadDetections(a.scientific_name)}
                style={{ cursor: "pointer", border: "1px solid var(--border,#2a3344)" }}>
                {a.common_name} <span className="muted">· first {fmtDate(a.first_date)}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* Diel activity */}
      <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: "0 0 0.4rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <Sunrise size={18} strokeWidth={1.8} /> Activity by hour
          {peakHour && <span className="muted" style={{ fontWeight: 400, fontSize: "0.85rem" }}>
            · peak {String(peakHour.hour).padStart(2, "0")}:00</span>}
        </h3>
        <div style={{ height: 160 }}>
          <ResponsiveContainer>
            <BarChart data={hourly} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border,#222b3a)" />
              <XAxis dataKey="hour" tick={{ fontSize: 11 }} interval={1} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip labelFormatter={(h) => `${String(h).padStart(2, "0")}:00`} />
              <Bar dataKey="count" fill="#4ea1ff" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* Daily activity */}
      <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
        <h3 style={{ margin: "0 0 0.4rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <TrendingUp size={18} strokeWidth={1.8} /> Detections per day
        </h3>
        <div style={{ height: 160 }}>
          <ResponsiveContainer>
            <BarChart data={daily} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border,#222b3a)" />
              <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={(d) => d.slice(5)} minTickGap={24} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Bar dataKey="count" fill="#34d399" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      {/* Species table */}
      <section className="card" style={{ padding: "1rem" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "0.6rem" }}>
          <h3 style={{ margin: 0 }}>Species ({species.length})</h3>
          <div style={{ display: "flex", gap: "0.3rem" }}>
            {(["count", "recent", "conf"] as const).map((k) => (
              <button key={k} className="pill" onClick={() => setSort(k)}
                style={{ cursor: "pointer", opacity: sort === k ? 1 : 0.55 }}>
                {k === "count" ? "Most" : k === "recent" ? "Recent" : "Confidence"}
              </button>
            ))}
          </div>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                <th style={th}>Bird</th><th style={th}>Count</th>
                <th style={th}>First</th><th style={th}>Last</th><th style={th}>Best</th>
              </tr>
            </thead>
            <tbody>
              {sortedSpecies.map((s) => (
                <tr key={s.scientific_name}
                  onClick={() => loadDetections(s.scientific_name)}
                  style={{ cursor: "pointer", borderTop: "1px solid var(--border,#1e2636)",
                    background: selected === s.scientific_name ? "rgba(78,161,255,0.08)" : undefined }}>
                  <td style={td}>
                    <div>{s.common_name}</div>
                    <div className="muted" style={{ fontStyle: "italic", fontSize: "0.78rem" }}>{s.scientific_name}</div>
                  </td>
                  <td style={td}>{s.count}</td>
                  <td style={td}>{fmtDate(s.first_date)}</td>
                  <td style={td}>{fmtDate(s.last_date)}</td>
                  <td style={td}>{fmtPct(s.max_conf)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Per-species detections + audio (PRIVATE only) */}
      {selected && (
        <section className="card" style={{ padding: "1rem", marginTop: "1rem" }}>
          <h3 style={{ marginTop: 0 }}>
            {species.find((s) => s.scientific_name === selected)?.common_name ?? selected}
            <span className="muted" style={{ fontWeight: 400 }}> · {detections.length} recent</span>
          </h3>
          {detections.length === 0 && <p className="muted">No detections loaded.</p>}
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {detections.map((d) => (
              <li key={d.id} style={{ display: "flex", alignItems: "center", gap: "0.75rem",
                padding: "0.4rem 0", borderTop: "1px solid var(--border,#1e2636)" }}>
                <span className="mono" style={{ fontSize: "0.82rem", minWidth: 150 }}>
                  {d.date} {PUBLIC ? "" : d.time}
                </span>
                <span className="pill">{fmtPct(d.confidence)}</span>
                {!PUBLIC && d.clip_name && (
                  <audio controls preload="none" src={`${API}/clip/${d.id}`} style={{ height: 30, flex: 1 }}>
                    <Volume2 size={14} />
                  </audio>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.75rem" }}>
        <Link to="/apps" className="muted" style={{ display: "inline-flex", alignItems: "center" }}>
          <ChevronLeft size={18} /> Apps
        </Link>
      </div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".5rem" }}>
        <h2 style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: 0 }}>
          <Bird size={24} strokeWidth={1.8} /> Yard Birds
        </h2>
        {!PUBLIC && (
          <Link to="/apps/birds/settings" className="muted"
            style={{ display: "inline-flex", alignItems: "center", gap: ".3rem", fontSize: ".85rem" }}>
            <SlidersHorizontal size={15} /> Settings
          </Link>
        )}
      </div>
      {/* Live mic-test tool is always available at the top — it needs no
          hardware and is the way to test detection from a phone/laptop. */}
      {!PUBLIC && <MicTest />}
      {!PUBLIC && <StationsList />}
      {children}
    </div>
  );
}

type StationRow = { station: string; detections: number; species: number; last_ts: string | null };

function StationsList() {
  const [stations, setStations] = useState<StationRow[]>([]);
  const [showNonBird] = useShowNonBird();
  const nb = nonBirdQuery(showNonBird);
  useEffect(() => {
    const load = () => fetch(`${API}/stations?_=1${nb}`).then((r) => r.json())
      .then((d) => setStations(d.stations || [])).catch(() => {});
    load();
    const t = setInterval(load, 8000); // auto-update the list as devices report
    return () => clearInterval(t);
  }, [nb]);
  if (stations.length === 0) return null;
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <h3 style={{ margin: "0 0 0.6rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <MapPin size={18} strokeWidth={1.8} /> Your locations
      </h3>
      <div style={{ display: "grid", gap: "0.5rem" }}>
        {stations.map((s) => (
          <Link key={s.station} to={`/apps/birds/station?s=${encodeURIComponent(s.station)}`}
            className="card" style={{ padding: "0.6rem 0.8rem", display: "flex", justifyContent: "space-between",
              alignItems: "center", textDecoration: "none", color: "inherit" }}>
            <span style={{ fontWeight: 500 }}>{s.station}</span>
            <span className="muted" style={{ fontSize: "0.82rem" }}>
              {s.species} species · {s.detections} detections
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: "0.75rem 1rem" }}>
      <div className="muted" style={{ fontSize: "0.78rem" }}>{label}</div>
      <div style={{ fontSize: "1.3rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const th: React.CSSProperties = { padding: "0.3rem 0.5rem", fontWeight: 500, fontSize: "0.8rem" };
const td: React.CSSProperties = { padding: "0.45rem 0.5rem", verticalAlign: "top" };
