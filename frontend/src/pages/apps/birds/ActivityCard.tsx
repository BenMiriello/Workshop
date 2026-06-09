import { useMemo, useState } from "react";
import { Sunrise, X } from "lucide-react";
import { Segmented } from "../../../ui";
import { hh, Chart, CHART_SERIES } from "./ui";

// One Activity card with two views you switch between (not two separate cards):
//   Per day      — detections per calendar day; click a bar to drill into that
//                  day's times-of-day.
//   Times of day — the 24-hour histogram. Defaults to All days combined (where
//                  the most active times show); drilling into a day from the Per
//                  day chart scopes it to that day, with a chip to clear back.
// Reused on the home dashboard, location pages, and the species page.

type Day = { date: string; count: number; hourly?: number[] };
type Hour = { hour: number; count: number };
type View = "day" | "hour";

export default function ActivityCard({ daily, hourly }: { daily: Day[]; hourly: Hour[] }) {
  const [view, setView] = useState<View>("day");
  const [day, setDay] = useState<string | null>(null); // a specific day, or null = all days

  const dailyAsc = useMemo(
    () => [...daily].sort((a, b) => (a.date < b.date ? -1 : 1)), [daily]);

  const hourData: Hour[] = useMemo(() => {
    if (day) {
      const d = daily.find((x) => x.date === day);
      if (d?.hourly) return d.hourly.map((count, hour) => ({ hour, count }));
    }
    return hourly;
  }, [day, daily, hourly]);

  const peak = useMemo(() => {
    const m = hourData.reduce((a, b) => (b.count > a.count ? b : a), hourData[0] || { hour: 0, count: 0 });
    return m && m.count > 0 ? m : null;
  }, [hourData]);

  const drillToDay = (datum: { date?: string; hourly?: number[] }) => {
    if (datum?.date && datum.hourly) { setDay(datum.date); setView("hour"); }
  };

  return (
    <section className="card" style={{ padding: "1rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: ".6rem", flexWrap: "wrap", marginBottom: ".6rem" }}>
        <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: ".5rem", fontSize: "1rem" }}>
          <Sunrise size={17} /> Activity
          {view === "hour" && (
            day
              ? <button type="button" className="chip" onClick={() => setDay(null)} title="Back to all days">
                  {day.slice(5)} <X size={12} />
                </button>
              : peak ? <span className="muted" style={{ fontWeight: 400, fontSize: ".85rem" }}>· all days · peak {hh(peak.hour)}</span> : null
          )}
        </h3>
        <Segmented<View>
          ariaLabel="Activity view"
          value={view}
          onChange={(v) => { setView(v); if (v === "day") setDay(null); }}
          options={[{ value: "day", label: "Per day" }, { value: "hour", label: "Times of day" }]}
        />
      </div>

      {view === "day"
        ? <Chart data={dailyAsc} x="date" fill={CHART_SERIES[1]} xfmt={(x: string) => x.slice(5)} onBarClick={drillToDay} />
        : <Chart data={hourData} x="hour" fill={CHART_SERIES[3]} xfmt={(h: number) => String(h)} interval={1} labelFmt={hh} />}

      {view === "day" && dailyAsc.length > 1 && (
        <p className="muted" style={{ fontSize: ".74rem", margin: ".4rem 0 0" }}>Tap a day to see its times of day.</p>
      )}
    </section>
  );
}
