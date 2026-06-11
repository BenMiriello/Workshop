import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Sunrise, Sunset, Moon, Droplets, Wind, CalendarDays } from "lucide-react";
import { Skeleton, StateBlock } from "../../../ui";
import BirdsHeader from "./BirdsHeader";
import { API } from "./ui";
import { wx, OUTLOOK, localTime, dayLen, type Level } from "./forecastUtil";

// The 7-day bird forecast: a tappable week strip of per-day outlooks, and a detail
// panel for the selected day (its light, weather through the day, activity outlook,
// and the typical daily activity shape). Honest that the per-day species list is the
// same residents until weather-conditioned history exists.

type Astro = { sunrise: string | null; sunset: string | null; day_length_min: number | null; moon_name: string | null; moon_illum: number | null };
type DayWeather = { temp_hi_f: number | null; temp_lo_f: number | null; wx_code: number | null; wind_max: number | null; precip_prob_max: number | null };
type HourW = { hour: number; temp_f: number | null; precip_prob: number | null; wx_code: number | null };
type Day = {
  date: string; weekday: string; is_today: boolean; astro: Astro | null;
  weather: DayWeather; outlook: { level: Level; reason: string }; hourly: HourW[]; has_weather: boolean;
};
type Species = { sci: string; common: string | null; score: number; n: number };
type Week = { days: Day[]; regulars: Species[]; regulars_confidence: string; expected_shape: { expected: number[] } | null };

const fmtDate = (d: string) => {
  try { return new Date(`${d}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" }); }
  catch { return d; }
};
const fmtFull = (d: string) => {
  try { return new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }); }
  catch { return d; }
};

// Temperature line across the day (its own min/max scale), precip shaded under.
function TempSpark({ hourly }: { hourly: HourW[] }) {
  const pts = hourly.filter((h) => h.temp_f != null);
  if (pts.length < 2) return null;
  const temps = pts.map((h) => h.temp_f as number);
  const lo = Math.min(...temps), hi = Math.max(...temps), span = hi - lo || 1;
  const x = (h: number) => (h / 23) * 96;
  const y = (t: number) => 2 + (1 - (t - lo) / span) * 14;
  const line = pts.map((h) => `${x(h.hour).toFixed(1)},${y(h.temp_f as number).toFixed(1)}`).join(" ");
  return (
    <div>
      <svg viewBox="0 0 96 18" preserveAspectRatio="none" style={{ width: "100%", height: 40, display: "block" }} role="img" aria-label="Temperature through the day">
        {hourly.map((h) => (h.precip_prob && h.precip_prob >= 20
          ? <rect key={h.hour} x={x(h.hour) - 1.8} y={0} width={3.6} height={18} fill="var(--cat-blue)" opacity={Math.min(0.35, (h.precip_prob / 100) * 0.5)} />
          : null))}
        <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="muted" style={{ display: "flex", justifyContent: "space-between", fontSize: ".72rem" }}>
        <span>12 AM</span><span>{lo}°F low / {hi}°F high</span><span>11 PM</span>
      </div>
    </div>
  );
}

// The typical daily activity shape (shared across days), current-hour-agnostic here.
function ShapeBar({ expected }: { expected: number[] }) {
  const max = Math.max(...expected, 1);
  return (
    <svg viewBox="0 0 96 18" preserveAspectRatio="none" style={{ width: "100%", height: 34, display: "block" }} role="img" aria-label="Typical activity by hour">
      {expected.map((v, h) => {
        const bh = Math.max(0.5, (v / max) * 16);
        return <rect key={h} x={h * 4 + 0.4} y={17 - bh} width={3.2} height={bh} rx={0.6} fill="var(--cat-violet)" />;
      })}
    </svg>
  );
}

export default function ForecastWeek() {
  const [week, setWeek] = useState<Week | null>(null);
  const [err, setErr] = useState(false);
  const [sel, setSel] = useState(0);
  // Resolve the location the dashboard is scoped to (same source the home page uses).
  const locationId = useMemo(() => {
    const m = document.cookie.match(/birds_loc=(\d+)/);
    if (m) return Number(m[1]);
    const ls = localStorage.getItem("birds-source");
    if (ls) { try { const v = JSON.parse(ls); if (v?.kind === "loc" && v.id) return Number(v.id); } catch { /* ignore */ } }
    return 6; // the yard station is the default place
  }, []);

  useEffect(() => {
    let live = true;
    fetch(`${API}/forecast/week?location_id=${locationId}&days=7`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => { if (live) setWeek(d); })
      .catch(() => { if (live) setErr(true); });
    return () => { live = false; };
  }, [locationId]);

  return (
    <div>
      <BirdsHeader title="7-day forecast" />
      {err ? (
        <StateBlock variant="error" title="Couldn't load the forecast"
          hint={`Fetch failed: GET ${API}/forecast/week. The weather spine may not have run yet.`} />
      ) : !week ? (
        <Skeleton style={{ height: 320 }} />
      ) : week.days.length === 0 ? (
        <StateBlock title="No forecast yet" hint="The weather spine has no forecast data for this place yet. It refreshes automatically." />
      ) : (
        <>
          {/* Week strip */}
          <div style={{ display: "flex", gap: ".5rem", overflowX: "auto", paddingBottom: ".3rem" }}>
            {week.days.map((d, i) => {
              const c = wx(d.weather.wx_code);
              const o = OUTLOOK[d.outlook.level] ?? OUTLOOK.normal;
              return (
                <button key={d.date} type="button" onClick={() => setSel(i)}
                  className="card" style={{
                    flex: "1 0 92px", minWidth: 92, padding: ".6rem .5rem", cursor: "pointer", textAlign: "center",
                    border: i === sel ? "1px solid var(--accent)" : "1px solid var(--border)", background: "var(--card)",
                  }}>
                  <div style={{ fontWeight: 600, fontSize: ".88rem" }}>{d.is_today ? "Today" : d.weekday}</div>
                  <div className="muted" style={{ fontSize: ".72rem", marginBottom: ".3rem" }}>{fmtDate(d.date)}</div>
                  <c.Icon size={22} style={{ color: "var(--muted)" }} />
                  <div style={{ fontSize: ".82rem", marginTop: ".25rem" }}>
                    {d.weather.temp_hi_f ?? "-"}° <span className="muted">/ {d.weather.temp_lo_f ?? "-"}°</span>
                  </div>
                  <div style={{ display: "inline-flex", alignItems: "center", gap: ".2rem", marginTop: ".3rem", color: o.tone, fontSize: ".74rem", fontWeight: 600 }}>
                    <o.Icon size={12} /> {o.label}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Selected day detail */}
          {week.days[sel] && <DayDetail day={week.days[sel]} regulars={week.regulars}
            shape={week.expected_shape?.expected ?? null} locationId={locationId} />}
        </>
      )}
    </div>
  );
}

function Fact({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: ".45rem", minWidth: 0 }}>
      <span className="muted" style={{ display: "inline-flex" }}>{icon}</span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: "block", fontWeight: 600, fontSize: ".9rem", whiteSpace: "nowrap" }}>{value}</span>
        <span className="muted" style={{ display: "block", fontSize: ".74rem" }}>{label}</span>
      </span>
    </div>
  );
}

function DayDetail({ day, regulars, shape, locationId }: {
  day: Day; regulars: Species[]; shape: number[] | null; locationId: number;
}) {
  const o = OUTLOOK[day.outlook.level] ?? OUTLOOK.normal;
  const a = day.astro;
  const w = day.weather;
  return (
    <section className="card" style={{ padding: "1rem", marginTop: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: "1rem" }}>{day.is_today ? "Today, " : ""}{fmtFull(day.date)}</h3>
        <span style={{ display: "inline-flex", alignItems: "center", gap: ".3rem", fontWeight: 600, color: o.tone }}>
          <o.Icon size={16} /> Activity {o.label.toLowerCase()}
        </span>
      </div>
      <p className="muted" style={{ margin: ".4rem 0 0", fontSize: ".88rem" }}>{day.outlook.reason}</p>

      {!day.has_weather && (
        <p className="muted" style={{ fontSize: ".82rem", marginTop: ".4rem" }}>
          Weather forecast for this day isn't available yet; it fills in as the forecast window advances.
        </p>
      )}

      {day.has_weather && day.hourly.length > 1 && (
        <div style={{ marginTop: ".8rem" }}>
          <span className="muted" style={{ fontSize: ".76rem" }}>Temperature through the day (rain shaded)</span>
          <TempSpark hourly={day.hourly} />
        </div>
      )}

      <div style={{ marginTop: ".9rem", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: ".7rem .9rem" }}>
        <Fact icon={<Sunrise size={18} />} label="Sunrise" value={localTime(a?.sunrise)} />
        <Fact icon={<Sunset size={18} />} label={`Sunset · ${dayLen(a?.day_length_min)} light`} value={localTime(a?.sunset)} />
        <Fact icon={<Moon size={18} />} label={a?.moon_name || "Moon"} value={a?.moon_illum != null ? `${Math.round(a.moon_illum * 100)}% lit` : "-"} />
        {w.precip_prob_max != null && <Fact icon={<Droplets size={18} />} label="Rain chance" value={`${w.precip_prob_max}%`} />}
        {w.wind_max != null && <Fact icon={<Wind size={18} />} label="Peak wind" value={`${w.wind_max} mph`} />}
      </div>

      {shape && (
        <div style={{ marginTop: "1rem", paddingTop: ".8rem", borderTop: "1px solid var(--border)" }}>
          <span className="muted" style={{ fontSize: ".76rem", display: "inline-flex", alignItems: "center", gap: ".3rem" }}>
            <CalendarDays size={13} /> Typical daily activity shape
          </span>
          <ShapeBar expected={shape} />
        </div>
      )}

      {regulars.length > 0 && (
        <div style={{ marginTop: ".8rem", fontSize: ".88rem" }}>
          <span className="muted">Usual regulars: </span>
          {regulars.slice(0, 5).map((s, i) => (
            <span key={s.sci}>
              {i > 0 ? ", " : ""}
              <Link to={`/apps/birds/bird?sci=${encodeURIComponent(s.sci)}&location_id=${locationId}`}
                style={{ textDecoration: "none", color: "inherit", fontWeight: 500 }}>
                {s.common || s.sci}
              </Link>
            </span>
          ))}
          <span className="muted" style={{ fontSize: ".74rem", display: "block", marginTop: ".2rem" }}>
            The species mix is the same residents each day for now; it will vary by day once there's weather-conditioned history.
          </span>
        </div>
      )}
    </section>
  );
}
