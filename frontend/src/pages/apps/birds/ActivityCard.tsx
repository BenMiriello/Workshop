import { useEffect, useMemo, useRef, useState } from "react";
import { Sunrise, ChevronLeft, ChevronRight, ChevronDown } from "lucide-react";
import { Segmented, Toggle } from "../../../ui";
import { hh, Chart, CHART_SERIES } from "./ui";

// One Activity card with two views you switch between (not two separate cards):
//   Per day      — detections per calendar day; click a bar to drill into that
//                  day's times-of-day.
//   Times of day — the 24-hour histogram. Scoped by a second toggle: All days
//                  (everything combined) or a single day picked from a calendar
//                  where only days WITH activity are selectable. Drilling in from
//                  a Per-day bar selects that day here.
// Reused on the home dashboard, location pages, and the species page.

type Day = { date: string; count: number; windows?: number; hourly?: number[]; hourly_species?: number[]; hourly_shannon?: number[] };
type Hour = { hour: number; count: number; species?: number; shannon?: number };
type View = "day" | "hour";
// Sun/dusk hours (local, fractional) + per-local-hour temperature for the day in
// view — used to overlay dawn/dusk markers and a temperature line on the
// times-of-day chart. Populated from /environment when a real location is in scope.
type EnvData = { srHour: number | null; ssHour: number | null; temps: (number | null)[] };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n: number) => String(n).padStart(2, "0");
// "YYYY-MM-DD" -> a comparable month index (year*12 + month), no Date/timezone.
const monthIdx = (date: string) => { const [y, m] = date.split("-").map(Number); return y * 12 + (m - 1); };
const fmtDay = (date: string) => { const [, m, d] = date.split("-").map(Number); return `${MONTHS[m - 1]} ${d}`; };

export default function ActivityCard({ daily, hourly, locationId }: { daily: Day[]; hourly: Hour[]; locationId?: number }) {
  const [view, setView] = useState<View>("day");
  const [day, setDay] = useState<string | null>(null); // a specific day, or null = all days
  const [showWeather, setShowWeather] = useState(false); // temperature overlay — off by default
  const [showDiversity, setShowDiversity] = useState(false); // species-richness line — off by default
  const [dayMetric, setDayMetric] = useState<"presence" | "count">("presence"); // day-over-day: presence over chatter
  const [env, setEnv] = useState<EnvData | null>(null);

  const dailyAsc = useMemo(
    () => [...daily].sort((a, b) => (a.date < b.date ? -1 : 1)), [daily]);
  const canPresence = useMemo(() => dailyAsc.some((d) => d.windows != null), [dailyAsc]);

  // The days that actually have activity — the only ones selectable in the picker.
  const activeDays = useMemo(
    () => new Set(daily.filter((d) => d.count > 0).map((d) => d.date)), [daily]);

  const hourData: Hour[] = useMemo(() => {
    if (day) {
      const d = daily.find((x) => x.date === day);
      if (d?.hourly) return d.hourly.map((count, hour) => ({ hour, count, species: d.hourly_species?.[hour], shannon: d.hourly_shannon?.[hour] }));
    }
    return hourly;
  }, [day, daily, hourly]);

  const drillToDay = (datum: { date?: string; hourly?: number[] }) => {
    if (datum?.date && datum.hourly) { setDay(datum.date); setView("hour"); }
  };

  // Pull dawn/dusk + hourly temperature for the day in view (local-time aligned
  // to the chart's hour buckets). Only when a real location with coords is in
  // scope and we're on the times-of-day view.
  useEffect(() => {
    if (locationId == null || view !== "hour") { setEnv(null); return; }
    const date = day ?? today();
    const start = new Date(`${date}T00:00:00`).toISOString();
    const end = new Date(`${date}T23:59:59`).toISOString();
    let cancelled = false;
    Promise.all([
      fetch(`/api/environment/astro?location_id=${locationId}&date=${date}`).then((r) => (r.ok ? r.json() : null)),
      fetch(`/api/environment/conditions?location_id=${locationId}&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`).then((r) => (r.ok ? r.json() : null)),
    ]).then(([a, c]: [any, any]) => {
      if (cancelled) return;
      // Conditions are stored in °C (Open-Meteo default); show °F to match the
      // rest of the app (US locale).
      const temps: (number | null)[] = Array(24).fill(null);
      for (const row of c?.conditions ?? []) {
        if (row.temp != null) temps[new Date(row.ts_hour).getHours()] = Math.round(row.temp * 9 / 5 + 32);
      }
      const localHour = (iso?: string | null) => {
        if (!iso) return null;
        const dt = new Date(iso);
        return dt.getHours() + dt.getMinutes() / 60;
      };
      setEnv({ srHour: localHour(a?.sunrise), ssHour: localHour(a?.sunset), temps });
    }).catch(() => { if (!cancelled) setEnv(null); });
    return () => { cancelled = true; };
  }, [locationId, view, day]);

  // Merge temperature into the hour data; build dawn/dusk markers + the optional
  // temperature overlay.
  const hourView = useMemo(
    () => hourData.map((d) => ({ ...d, temp: env?.temps[d.hour] ?? null })), [hourData, env]);
  const canWeather = env != null && env.temps.some((t) => t != null);
  const markers = env
    ? ([
        env.srHour != null ? { x: Math.round(env.srHour), label: "Sunrise" } : null,
        env.ssHour != null ? { x: Math.round(env.ssHour), label: "Sunset" } : null,
      ].filter(Boolean) as { x: number; label: string }[])
    : undefined;
  const tempOverlay = showWeather && canWeather
    ? { key: "temp", name: "Temperature", color: "var(--warn)", fmt: (v: number) => `${Math.round(v)}°F` }
    : undefined;
  // Diversity layer: Shannon H per hour (evenness-weighted — high when many species
  // share the hour, low when one dominates). On its own hidden auto-scaled axis so
  // the shape reads. Available whenever any hour has it populated.
  const canDiversity = hourView.some((d) => d.shannon != null);
  const diversityLines = showDiversity && canDiversity
    ? [{ key: "shannon", name: "Diversity" }]
    : undefined;

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".6rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem", minWidth: 0 }}>
          <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: ".5rem", fontSize: "1rem" }}>
            <Sunrise size={17} /> Activity
          </h3>
          {view === "day" && canPresence && (
            <Segmented<"presence" | "count">
              ariaLabel="Day metric"
              value={dayMetric}
              onChange={setDayMetric}
              options={[{ value: "presence", label: "Presence" }, { value: "count", label: "Detections" }]}
            />
          )}
          {view === "hour" && (
            <DayScope day={day} activeDays={activeDays} onAll={() => setDay(null)} onPick={setDay} />
          )}
          {view === "hour" && canWeather && (
            <Toggle checked={showWeather} onChange={setShowWeather} label="Temperature" />
          )}
          {view === "hour" && canDiversity && (
            <Toggle checked={showDiversity} onChange={setShowDiversity} label="Diversity" />
          )}
        </div>
        <Segmented<View>
          ariaLabel="Activity view"
          value={view}
          onChange={(v) => { setView(v); if (v === "day") setDay(null); }}
          options={[{ value: "day", label: "Per day" }, { value: "hour", label: "Times of day" }]}
        />
      </div>

      {view === "day"
        ? <Chart data={dailyAsc} x="date" fill={CHART_SERIES[1]} xfmt={(x: string) => x.slice(5)} onBarClick={drillToDay}
            barKey={dayMetric === "presence" && canPresence ? "windows" : "count"}
            barName={dayMetric === "presence" && canPresence ? "Presence" : "Detections"}
            unit={dayMetric === "presence" && canPresence ? "window" : "detection"} />
        : <Chart data={hourView} x="hour" fill={CHART_SERIES[3]} xfmt={(h: number) => String(h)} interval={1} labelFmt={hh}
            markers={markers} overlay={tempOverlay} lines={diversityLines}
            sky={env ? { sunrise: env.srHour, sunset: env.ssHour } : undefined} />}
    </section>
  );
}

// All days | <day> toggle. The right side is a calendar trigger, not a plain
// option: tapping it opens a month picker where only days WITH activity click.
function DayScope({ day, activeDays, onAll, onPick }: {
  day: string | null; activeDays: Set<string>; onAll: () => void; onPick: (date: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="bird-dayscope" style={{ position: "relative" }}>
      <div className="ws-segmented" role="group" aria-label="Activity day scope">
        <button type="button" aria-pressed={day === null} onClick={() => { setOpen(false); onAll(); }}>
          All days
        </button>
        <button type="button" aria-pressed={day !== null} aria-haspopup="dialog" aria-expanded={open}
          onClick={() => setOpen((v) => !v)}>
          {day ? fmtDay(day) : "Today"} <ChevronDown size={13} />
        </button>
      </div>
      {open && (
        <DayPicker activeDays={activeDays} selected={day}
          onClose={() => setOpen(false)}
          onPick={(d) => { onPick(d); setOpen(false); }} />
      )}
    </div>
  );
}

function DayPicker({ activeDays, selected, onPick, onClose }: {
  activeDays: Set<string>; selected: string | null; onPick: (date: string) => void; onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const months = useMemo(() => [...activeDays].map(monthIdx).sort((a, b) => a - b), [activeDays]);
  const minM = months[0] ?? null;
  const maxM = months[months.length - 1] ?? null;
  // Open on the selected day's month, else the most recent month with activity.
  const [cursor, setCursor] = useState(() => (selected ? monthIdx(selected) : maxM) ?? monthIdx(today()));

  // Close on outside click or Escape.
  useEffect(() => {
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [onClose]);

  const year = Math.floor(cursor / 12);
  const month0 = cursor % 12;
  const firstDow = new Date(year, month0, 1).getDay();
  const daysInMonth = new Date(year, month0 + 1, 0).getDate();
  const cells: (string | null)[] = [
    ...Array(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => `${year}-${pad(month0 + 1)}-${pad(i + 1)}`),
  ];

  return (
    <div className="bird-cal" ref={ref} role="dialog" aria-label="Pick a day">
      <div className="bird-cal__head">
        <button type="button" className="bird-cal__nav" title="Previous month"
          disabled={minM == null || cursor <= minM} onClick={() => setCursor((c) => c - 1)}>
          <ChevronLeft size={15} />
        </button>
        <span className="bird-cal__title">{MONTHS[month0]} {year}</span>
        <button type="button" className="bird-cal__nav" title="Next month"
          disabled={maxM == null || cursor >= maxM} onClick={() => setCursor((c) => c + 1)}>
          <ChevronRight size={15} />
        </button>
      </div>
      <div className="bird-cal__grid">
        {["S", "M", "T", "W", "T", "F", "S"].map((d, i) => (
          <span key={`dow${i}`} className="bird-cal__dow">{d}</span>
        ))}
        {cells.map((date, i) => {
          if (!date) return <span key={`b${i}`} />;
          const has = activeDays.has(date);
          const d = Number(date.slice(8));
          return (
            <button key={date} type="button" disabled={!has}
              className={`bird-cal__day${date === selected ? " is-selected" : ""}`}
              title={has ? fmtDay(date) : "No activity"}
              onClick={() => onPick(date)}>
              {d}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Local "today" as YYYY-MM-DD — only a fallback when there's no activity yet.
function today(): string {
  const n = new Date();
  return `${n.getFullYear()}-${pad(n.getMonth() + 1)}-${pad(n.getDate())}`;
}
