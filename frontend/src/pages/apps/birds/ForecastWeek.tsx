import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sunrise, Sunset, Moon, Droplets, Wind, Bird } from "lucide-react";
import {
  Area, Bar, CartesianGrid, ComposedChart, ErrorBar, LabelList, Line,
  ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Skeleton, StateBlock, ChartTooltip, CHART_TARGET } from "../../../ui";
import BirdsHeader from "./BirdsHeader";
import { API, hh, CHART_SERIES } from "./ui";
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

// A forecast is a guess, so it's drawn as a range, never a false-precise value.
// The model yields a point estimate; counts have ~Poisson spread, so a ±1.5·√n
// band is a principled likely-range (not fabricated uncertainty).
function band(n: number): [number, number] {
  const s = Math.sqrt(Math.max(n, 0));
  return [Math.max(0, Math.round(n - 1.5 * s)), Math.round(n + 1.5 * s)];
}
function localHourOf(iso?: string | null): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d.getHours() + d.getMinutes() / 60;
}

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
          hint={`GET ${API}/forecast/week failed.`} />
      ) : !week ? (
        <Skeleton style={{ height: 320 }} />
      ) : week.days.length === 0 ? (
        <StateBlock title="No forecast yet" hint="Nothing for this place yet. It refreshes on its own." />
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

// Week-activity graph: expected detections per day as bars with a likely-range
// "I" bar (capped top AND bottom), the species count above each bar (the week's
// diversity read), and a weather gutter below — off the activity axis.
function WeekActivity({ days, onPick, historyDays }: { days: Day[]; onPick: (i: number) => void; historyDays: number }) {
  const data = days.map((d, i) => ({
    label: d.is_today ? "Today" : d.weekday, count: d.expected_detections,
    species: Math.round(d.expected_species),
    // Symmetric Poisson half-width (±1.5·√n) — drawn as the capped "I" range bar.
    err: Math.round(1.5 * Math.sqrt(Math.max(d.expected_detections, 0))), idx: i,
  }));
  return (
    <section className="card" style={{ padding: "1rem", marginBottom: ".4rem" }}>
      <h3 style={{ margin: "0 0 .5rem", display: "flex", alignItems: "center", gap: ".5rem", fontSize: "1rem" }}>
        <Bird size={17} /> Expected activity this week
      </h3>
      <div style={{ height: 168 }}>
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 18, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis dataKey="label" tick={{ fontSize: 10 }} interval={0} />
            <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
            <Tooltip isAnimationActive={false} cursor={{ fill: "rgba(120,160,255,0.08)" }}
              content={({ active, payload, label }: any) => (
                <ChartTooltip active={active} payload={payload} label={label}
                  format={(v) => `${v} detection${v === 1 ? "" : "s"}`} />
              )} />
            <Bar dataKey="count" name="Expected" fill={CHART_SERIES[1]} radius={[2, 2, 0, 0]} maxBarSize={48}
              isAnimationActive={false}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onClick={(p: any) => onPick(p?.idx ?? 0)} style={{ cursor: "pointer" }}>
              <ErrorBar dataKey="err" width={5} strokeWidth={1.2} stroke="var(--muted)" direction="y" />
              <LabelList dataKey="species" position="top" fontSize={10} fill="var(--muted)" />
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <WeatherGutter days={days} />
      <p className="muted" style={{ fontSize: ".76rem", margin: ".4rem 0 0" }}>
        Predicted detections per day (tap a bar). The I-bar is the likely range; the number above it is the species count. Built from {historyDays} day{historyDays === 1 ? "" : "s"} of history.
      </p>
    </section>
  );
}

// Weather context under the week chart — one column per day, aligned with the
// bars, kept off the activity axis.
function WeatherGutter({ days }: { days: Day[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: `repeat(${days.length}, 1fr)`, gap: ".25rem", marginTop: ".35rem", paddingLeft: 6 }}>
      {days.map((d) => {
        const c = wx(d.weather.wx_code);
        return (
          <div key={d.date} style={{ textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: ".1rem" }}>
            <c.Icon size={14} className="muted" />
            {/* Font scales down with the column so the temp/precip don't clip
                when 7 days share a ~358px phone width. */}
            <span className="muted" style={{ fontSize: "clamp(.55rem, 2.4vw, .68rem)", whiteSpace: "nowrap" }}>{d.weather.temp_hi_f ?? "-"}°/{d.weather.temp_lo_f ?? "-"}°</span>
            {d.weather.precip_prob_max != null && (
              <span className="muted" style={{ fontSize: "clamp(.54rem, 2.3vw, .66rem)", display: "inline-flex", alignItems: "center", gap: "2px" }}>
                <Droplets size={9} /> {d.weather.precip_prob_max}%
              </span>
            )}
          </div>
        );
      })}
    </div>
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
          <span className="muted" style={{ fontSize: ".76rem" }}>Expected activity by hour. Shaded band is the likely range, rain behind on its own axis</span>
          <DayShape day={day} activity={profiles.activity} />
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
            Same birds each day for now. The mix will vary once there is more history to go on.
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

// A single day's expected shape: the expected curve inside a shaded likely-range
// band, with rain on its own % axis behind it, on the day/night backdrop. (A
// forecast is a guess, drawn as one — band over plain bars.)
function DayShape({ day, activity }: { day: Day; activity: number[] }) {
  const rain: (number | null)[] = Array(24).fill(null);
  for (const h of day.hourly) if (h.precip_prob != null) rain[h.hour] = h.precip_prob;
  const data = activity.map((v, h) => {
    const [low, high] = band(v);
    return { hour: h, expected: v, band: [low, high] as [number, number], rain: rain[h] };
  });
  const sr = localHourOf(day.astro?.sunrise);
  const ss = localHourOf(day.astro?.sunset);
  return (
    <div style={{ height: 160 }}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 6, right: 4, left: -18, bottom: 0 }}>
          {sr != null && <ReferenceArea yAxisId="act" x1={0} x2={Math.round(sr)} fill="var(--muted)" fillOpacity={0.12} stroke="none" />}
          {ss != null && <ReferenceArea yAxisId="act" x1={Math.round(ss)} x2={23} fill="var(--muted)" fillOpacity={0.12} stroke="none" />}
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="hour" tick={{ fontSize: 10 }} interval={2} tickFormatter={(h: number) => String(h)} />
          <YAxis yAxisId="act" tick={{ fontSize: 10 }} allowDecimals={false} />
          <YAxis yAxisId="rain" orientation="right" width={32} domain={[0, 100]}
            tick={{ fontSize: 10, fill: "var(--cat-blue)" }} stroke="var(--cat-blue)" tickFormatter={(v: number) => `${v}%`} />
          <Tooltip isAnimationActive={false} cursor={{ fill: "rgba(120,160,255,0.08)" }}
            content={({ active, payload, label }: any) => (
              <ChartTooltip active={active} payload={(payload || []).filter((p: any) => p.dataKey !== "band")}
                label={hh(label)} showName format={(v) => String(v)} />
            )} />
          {sr != null && <ReferenceLine yAxisId="act" x={Math.round(sr)} stroke={CHART_TARGET} strokeDasharray="3 3" label={{ value: "Sunrise", position: "top", fontSize: 9, fill: "var(--muted)" }} />}
          {ss != null && <ReferenceLine yAxisId="act" x={Math.round(ss)} stroke={CHART_TARGET} strokeDasharray="3 3" label={{ value: "Sunset", position: "top", fontSize: 9, fill: "var(--muted)" }} />}
          <Area yAxisId="act" dataKey="band" name="Likely range" stroke="none" fill={CHART_SERIES[3]} fillOpacity={0.18} isAnimationActive={false} legendType="none" />
          <Line yAxisId="rain" type="monotone" dataKey="rain" name="Rain" stroke="var(--cat-blue)" strokeWidth={1.2} strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />
          <Line yAxisId="act" type="monotone" dataKey="expected" name="Expected" stroke={CHART_SERIES[3]} strokeWidth={1.8} dot={false} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
