import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sunrise, Sunset, Moon, Droplets, Wind, Bird } from "lucide-react";
import { Skeleton, StateBlock } from "../../../ui";
import BirdsHeader from "./BirdsHeader";
import { API, hh, Chart, CHART_SERIES } from "./ui";
import { wx, OUTLOOK, localTime, dayLen, type Level } from "./forecastUtil";

// The 7-day BIRD forecast: the bird activity is the subject. A week-activity graph
// up top, bird-first day cards (predicted activity level + species count, weather
// small), and a selected-day detail that leads with the expected activity + diversity
// graph, with light/weather demoted to supporting context underneath.

type Astro = { sunrise: string | null; sunset: string | null; day_length_min: number | null; moon_name: string | null; moon_illum: number | null };
type DayWeather = { temp_hi_f: number | null; temp_lo_f: number | null; wx_code: number | null; wind_max: number | null; precip_prob_max: number | null };
type HourW = { hour: number; temp_f: number | null; precip_prob: number | null; wx_code: number | null };
type Day = {
  date: string; weekday: string; is_today: boolean; astro: Astro | null; weather: DayWeather;
  outlook: { level: Level; reason: string }; hourly: HourW[]; has_weather: boolean;
  expected_detections: number; expected_species: number; peak_hours: number[];
};
type Species = { sci: string; common: string | null; score: number; n: number };
type Profiles = { activity: number[]; diversity: number[] };
type Week = { days: Day[]; regulars: Species[]; profiles: Profiles | null; mean_species_per_day: number; history_days: number };

const fmtDate = (d: string) => {
  try { return new Date(`${d}T12:00:00`).toLocaleDateString([], { month: "short", day: "numeric" }); }
  catch { return d; }
};
const fmtFull = (d: string) => {
  try { return new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" }); }
  catch { return d; }
};

export default function ForecastWeek() {
  const [week, setWeek] = useState<Week | null>(null);
  const [err, setErr] = useState(false);
  const [sel, setSel] = useState(0);
  const [locationId] = useState(() => {
    const ls = localStorage.getItem("birds-source");
    if (ls) { try { const v = JSON.parse(ls); if (v?.kind === "loc" && v.id) return Number(v.id); } catch { /* ignore */ } }
    return 6; // the yard station is the default place
  });

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
          <WeekActivity days={week.days} onPick={setSel} historyDays={week.history_days} />
          <div style={{ display: "flex", gap: ".5rem", overflowX: "auto", padding: ".1rem 0 .4rem", marginTop: ".4rem" }}>
            {week.days.map((d, i) => <DayCard key={d.date} day={d} active={i === sel} onClick={() => setSel(i)} />)}
          </div>
          {week.days[sel] && <DayDetail day={week.days[sel]} profiles={week.profiles}
            regulars={week.regulars} locationId={locationId} />}
        </>
      )}
    </div>
  );
}

// Week-activity graph: expected detections per day (bars) + expected species (line).
function WeekActivity({ days, onPick, historyDays }: { days: Day[]; onPick: (i: number) => void; historyDays: number }) {
  const data = days.map((d, i) => ({
    label: d.is_today ? "Today" : d.weekday, count: d.expected_detections, species: d.expected_species, idx: i,
  }));
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: ".4rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".5rem", fontSize: "1rem" }}>
        <Bird size={17} /> Expected activity this week
      </h3>
      <Chart data={data} x="label" fill={CHART_SERIES[1]} height={150}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        onBarClick={(p: any) => onPick(p?.idx ?? 0)} lines={[{ key: "species", name: "Species" }]} />
      <p className="muted" style={{ fontSize: ".76rem", margin: ".3rem 0 0" }}>
        Predicted detections per day (tap a bar) with species count. Built from {historyDays} day{historyDays === 1 ? "" : "s"} of history and tilted by each day's conditions; it sharpens as history grows.
      </p>
    </section>
  );
}

// Bird-first day card: predicted activity level + species count lead; weather small.
function DayCard({ day, active, onClick }: { day: Day; active: boolean; onClick: () => void }) {
  const o = OUTLOOK[day.outlook.level] ?? OUTLOOK.normal;
  const c = wx(day.weather.wx_code);
  return (
    <button type="button" onClick={onClick} className="card" style={{
      flex: "1 0 104px", minWidth: 104, padding: ".6rem .5rem", cursor: "pointer", textAlign: "center",
      border: active ? "1px solid var(--accent)" : "1px solid var(--border)", background: "var(--card)",
    }}>
      <div style={{ fontWeight: 600, fontSize: ".88rem" }}>{day.is_today ? "Today" : day.weekday}</div>
      <div className="muted" style={{ fontSize: ".72rem" }}>{fmtDate(day.date)}</div>
      <div style={{ display: "inline-flex", alignItems: "center", gap: ".25rem", marginTop: ".4rem", color: o.tone, fontSize: ".8rem", fontWeight: 600 }}>
        <o.Icon size={14} /> {o.label}
      </div>
      <div style={{ fontSize: ".8rem", marginTop: ".15rem" }}>~{Math.round(day.expected_species)} species</div>
      <div className="muted" style={{ display: "inline-flex", alignItems: "center", gap: ".25rem", marginTop: ".35rem", fontSize: ".74rem" }}>
        <c.Icon size={13} /> {day.weather.temp_hi_f ?? "-"}°/{day.weather.temp_lo_f ?? "-"}°
      </div>
    </button>
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
      <svg viewBox="0 0 96 18" preserveAspectRatio="none" style={{ width: "100%", height: 36, display: "block" }} role="img" aria-label="Temperature through the day">
        {hourly.map((h) => (h.precip_prob && h.precip_prob >= 20
          ? <rect key={h.hour} x={x(h.hour) - 1.8} y={0} width={3.6} height={18} fill="var(--cat-blue)" opacity={Math.min(0.35, (h.precip_prob / 100) * 0.5)} />
          : null))}
        <polyline points={line} fill="none" stroke="var(--cat-slate)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="muted" style={{ display: "flex", justifyContent: "space-between", fontSize: ".72rem" }}>
        <span>12 AM</span><span>{lo}°F low / {hi}°F high</span><span>11 PM</span>
      </div>
    </div>
  );
}

function DayDetail({ day, profiles, regulars, locationId }: {
  day: Day; profiles: Profiles | null; regulars: Species[]; locationId: number;
}) {
  const o = OUTLOOK[day.outlook.level] ?? OUTLOOK.normal;
  const a = day.astro;
  const w = day.weather;
  const peaks = (day.peak_hours || []).length ? day.peak_hours.map(hh).join(" and ") : null;
  const actData = profiles
    ? profiles.activity.map((v, h) => ({ hour: h, count: v, diversity: profiles.diversity[h] }))
    : [];

  return (
    <section className="card" style={{ padding: "1rem", marginTop: ".6rem" }}>
      {/* PRIMARY: the bird forecast for the day */}
      <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0, fontSize: "1rem" }}>{day.is_today ? "Today, " : ""}{fmtFull(day.date)}</h3>
        <span style={{ display: "inline-flex", alignItems: "center", gap: ".3rem", fontWeight: 600, color: o.tone }}>
          <o.Icon size={16} /> Activity {o.label.toLowerCase()}
        </span>
      </div>
      <p style={{ margin: ".4rem 0 0", fontSize: ".92rem" }}>
        Expect roughly <strong>{day.expected_detections}</strong> detections across <strong>~{Math.round(day.expected_species)}</strong> species{peaks ? <>, busiest around <strong>{peaks}</strong></> : null}.
      </p>
      <p className="muted" style={{ margin: ".25rem 0 0", fontSize: ".84rem" }}>{day.outlook.reason}</p>

      {profiles && (
        <div style={{ marginTop: ".8rem" }}>
          <span className="muted" style={{ fontSize: ".76rem" }}>Expected activity (bars) and diversity (line) by hour</span>
          <Chart data={actData} x="hour" fill={CHART_SERIES[3]} height={150} interval={2}
            xfmt={(h: number) => String(h)} labelFmt={hh} lines={[{ key: "diversity", name: "Diversity" }]} />
        </div>
      )}

      {regulars.length > 0 && (
        <div style={{ marginTop: ".6rem", fontSize: ".88rem" }}>
          <span className="muted">Usual regulars: </span>
          {regulars.slice(0, 5).map((s, i) => (
            <span key={s.sci}>
              {i > 0 ? ", " : ""}
              <Link to={`/apps/birds/bird?sci=${encodeURIComponent(s.sci)}&location_id=${locationId}`}
                style={{ textDecoration: "none", color: "inherit", fontWeight: 500 }}>{s.common || s.sci}</Link>
            </span>
          ))}
          <span className="muted" style={{ fontSize: ".74rem", display: "block", marginTop: ".2rem" }}>
            Same residents each day for now; the mix will vary by day once there's weather-conditioned history.
          </span>
        </div>
      )}

      {/* SUPPORTING: the weather and light behind the forecast */}
      <div style={{ marginTop: ".9rem", paddingTop: ".8rem", borderTop: "1px solid var(--border)" }}>
        <span className="muted" style={{ fontSize: ".76rem" }}>Conditions</span>
        {!day.has_weather && (
          <p className="muted" style={{ fontSize: ".82rem", marginTop: ".4rem" }}>
            Weather forecast for this day isn't available yet; it fills in as the forecast window advances.
          </p>
        )}
        {day.has_weather && day.hourly.length > 1 && <div style={{ marginTop: ".4rem" }}><TempSpark hourly={day.hourly} /></div>}
        <div style={{ marginTop: ".7rem", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(128px, 1fr))", gap: ".7rem .9rem" }}>
          <Fact icon={<Sunrise size={18} />} label="Sunrise" value={localTime(a?.sunrise)} />
          <Fact icon={<Sunset size={18} />} label={`Sunset · ${dayLen(a?.day_length_min)} light`} value={localTime(a?.sunset)} />
          <Fact icon={<Moon size={18} />} label={a?.moon_name || "Moon"} value={a?.moon_illum != null ? `${Math.round(a.moon_illum * 100)}% lit` : "-"} />
          {w.precip_prob_max != null && <Fact icon={<Droplets size={18} />} label="Rain chance" value={`${w.precip_prob_max}%`} />}
          {w.wind_max != null && <Fact icon={<Wind size={18} />} label="Peak wind" value={`${w.wind_max} mph`} />}
        </div>
      </div>
    </section>
  );
}
