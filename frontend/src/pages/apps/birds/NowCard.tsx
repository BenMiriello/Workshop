import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sunrise, Sunset, Moon, Sun, Bird, Gauge, AlertTriangle, CalendarDays, ChevronRight } from "lucide-react";
import { API, hh } from "./ui";
import { wx, OUTLOOK, cToF, localTime, dayLen, pressureSub, untilLabel, type Level } from "./forecastUtil";

// The "Bird forecast" card. The forecast LEADS: activity level, what's likely now
// (Tier 1 conditional ranking), and the day's expected shape. Reasoning + calibration
// are SECONDARY, then light/weather conditions. The moon is shown only when it's
// actually relevant (night/twilight), not in broad daylight. Built from /birds/now.

type Astro = {
  sunrise: string | null; sunset: string | null; day_length_min: number | null;
  moon_illum: number | null; moon_name: string | null;
};
type Weather = { temp: number | null; wind_speed: number | null; wx_code: number | null } | null;
type Forecast = {
  level: Level; reason: string; incoming: string | null; phase: string;
  pressure_hpa: number | null; pressure_trend_3h: number | null; wind_mph: number;
};
type Likely = { sci: string; common: string | null; score: number; n: number };
type DayShape = { expected: number[]; actual: number[]; days: number; peak_hours: number[] } | null;
type Calib = { graded_hours: number; precision_at_k: number | null; status: string };
type Now = {
  hour: number; today: string; astro: Astro | null; weather: Weather; forecast: Forecast;
  early_bird: { sci: string; common: string | null; ts: string } | null;
  likely: Likely[]; likely_match: string; likely_confidence: string; likely_n: number;
  day_shape: DayShape; calibration: Calib;
};

// A compact 24-bar expected-activity sparkline with the current hour marked.
function DayShapeBar({ shape, hour }: { shape: DayShape; hour: number }) {
  if (!shape) return null;
  const max = Math.max(...shape.expected, 1);
  return (
    <svg viewBox="0 0 96 18" preserveAspectRatio="none" style={{ width: "100%", height: 32, display: "block" }} role="img" aria-label="Today's expected activity by hour">
      {shape.expected.map((v, h) => {
        const bh = Math.max(0.5, (v / max) * 16);
        return <rect key={h} x={h * 4 + 0.4} y={17 - bh} width={3.2} height={bh} rx={0.6}
          fill={h === hour ? "var(--accent)" : "var(--border)"} />;
      })}
    </svg>
  );
}

function Tile({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: ".5rem", minWidth: 0 }}>
      <span className="muted" style={{ display: "inline-flex" }}>{icon}</span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontWeight: 600, fontSize: ".95rem", whiteSpace: "nowrap" }}>{value}</span>
        <span className="muted" style={{ display: "block", fontSize: ".76rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {label}{sub ? ` · ${sub}` : ""}
        </span>
      </span>
    </div>
  );
}

export default function NowCard({ locationId }: { locationId: number }) {
  const [now, setNow] = useState<Now | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let live = true;
    const load = () => fetch(`${API}/now?location_id=${locationId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => { if (live) { setNow(d); setErr(false); } })
      .catch(() => { if (live) setErr(true); });
    load();
    const t = setInterval(load, 5 * 60 * 1000);
    return () => { live = false; clearInterval(t); };
  }, [locationId]);

  if (err || !now || !now.astro) return null;
  const a = now.astro;
  const w = now.weather;
  const cond = wx(w?.wx_code ?? null);
  const eb = now.early_bird;
  const f = now.forecast;
  const ol = OUTLOOK[f?.level] ?? OUTLOOK.normal;
  const cal = now.calibration;
  const link = (sci: string) => `/apps/birds/bird?sci=${encodeURIComponent(sci)}&location_id=${locationId}`;
  const calText = cal && cal.graded_hours > 0 && cal.precision_at_k != null
    ? `, and has been right ${Math.round(cal.precision_at_k * 100)}% of the time over ${cal.graded_hours} graded hour${cal.graded_hours === 1 ? "" : "s"}`
    : ", with its accuracy still being measured";
  const rankingNote = now.likely.length > 0
    ? `The ranking draws on ${now.likely_n} past detection${now.likely_n === 1 ? "" : "s"} at this time of day${calText}.`
    : "";
  // The moon only matters at night / twilight. In broad daylight show how much
  // daylight is left instead (the relevant "right now" light fact).
  const isDark = ["night", "dawn", "dusk"].includes(f?.phase);
  const dLeft = untilLabel(a.sunset);

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      {/* PRIMARY: the forecast itself */}
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: ".5rem", fontSize: "1rem" }}>
          <Bird size={17} /> Bird forecast
        </h3>
        {f && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: ".3rem", fontWeight: 600, color: ol.tone }}>
            <ol.Icon size={16} /> Activity {ol.label.toLowerCase()}
          </span>
        )}
        <Link to="/apps/birds/forecast" style={{ marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: ".25rem", fontSize: ".82rem", color: "var(--accent)", textDecoration: "none" }}>
          <CalendarDays size={14} /> 7-day forecast <ChevronRight size={13} />
        </Link>
      </div>

      {now.likely.length > 0 && (
        <p style={{ margin: ".5rem 0 0", fontSize: "1rem" }}>
          <span className="muted" style={{ fontSize: ".9rem" }}>Likely around {hh(now.hour)}: </span>
          {now.likely.slice(0, 4).map((s, i) => (
            <span key={s.sci}>
              {i > 0 ? ", " : ""}
              <Link to={link(s.sci)} style={{ textDecoration: "none", color: "inherit", fontWeight: 600 }}>
                {s.common || s.sci}
              </Link>
            </span>
          ))}
        </p>
      )}

      {now.day_shape && (
        <div style={{ marginTop: ".7rem" }}>
          <DayShapeBar shape={now.day_shape} hour={now.hour} />
          <span className="muted" style={{ fontSize: ".74rem" }}>
            Today's expected shape{now.day_shape.peak_hours.length ? `, usual peaks ${now.day_shape.peak_hours.map(hh).join(" and ")}` : ""} (from {now.day_shape.days} day{now.day_shape.days === 1 ? "" : "s"})
          </span>
        </div>
      )}

      {/* SECONDARY: why, calibration, and any incoming shift */}
      {f && (
        <p className="muted" style={{ margin: ".75rem 0 0", fontSize: ".84rem" }}>
          {f.reason} {rankingNote}
        </p>
      )}
      {f?.incoming && (
        <p className="muted" style={{ margin: ".3rem 0 0", fontSize: ".84rem", display: "flex", alignItems: "flex-start", gap: ".35rem" }}>
          <AlertTriangle size={14} style={{ marginTop: ".1rem", flexShrink: 0 }} /> {f.incoming}
        </p>
      )}

      <div style={{ marginTop: ".6rem", fontSize: ".88rem", display: "flex", alignItems: "center", gap: ".5rem" }}>
        <Bird size={15} className="muted" />
        {eb ? (
          <span>
            <span className="muted">Early bird today: </span>
            <Link to={link(eb.sci)} style={{ fontWeight: 600, textDecoration: "none", color: "inherit" }}>{eb.common || eb.sci}</Link>
            <span className="muted">, {localTime(eb.ts)}</span>
          </span>
        ) : <span className="muted">Quiet so far today, no birds heard yet.</span>}
      </div>

      {/* SUPPORTING: current weather + pressure + the day's light. */}
      <div style={{ marginTop: ".9rem", paddingTop: ".8rem", borderTop: "1px solid var(--border)",
        display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: ".8rem .9rem" }}>
        {w?.temp != null && (
          <Tile icon={<cond.Icon size={20} />} value={`${cToF(w.temp)}°F`} label={`${cond.label} now`}
            sub={w.wind_speed != null ? `wind ${f?.wind_mph ?? Math.round(w.wind_speed * 0.621)} mph` : undefined} />
        )}
        {f?.pressure_hpa != null && (
          <Tile icon={<Gauge size={20} />} value={`${Math.round(f.pressure_hpa)} hPa`} label="Pressure" sub={pressureSub(f.pressure_trend_3h)} />
        )}
        <Tile icon={<Sunrise size={20} />} value={localTime(a.sunrise)} label="Sunrise" />
        <Tile icon={<Sunset size={20} />} value={localTime(a.sunset)} label="Sunset" sub={`${dayLen(a.day_length_min)} of light`} />
        {isDark
          ? <Tile icon={<Moon size={20} />} value={a.moon_illum != null ? `${Math.round(a.moon_illum * 100)}% lit` : "-"} label={a.moon_name || "Moon"} />
          : dLeft && <Tile icon={<Sun size={20} />} value={dLeft} label="Daylight left" />}
      </div>
    </section>
  );
}
