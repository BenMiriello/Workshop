import { Link } from "react-router-dom";
import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Bird } from "lucide-react";
import { useBirdInfo } from "./birdinfo";

// Small shared pieces for the bird pages: formatters, a fetch helper, the stat
// tile, a recharts bar chart, a lightweight CSS sparkbar histogram, and a
// species thumbnail that lazy-loads its photo from the bird-info cache.

export const API = "/api/birds";
export const pct = (c: number) => `${Math.round(c * 100)}%`;
export const hh = (h: number) => `${String(h).padStart(2, "0")}:00`;

export async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: ".7rem 1rem" }}>
      <div className="muted" style={{ fontSize: ".78rem" }}>{label}</div>
      <div style={{ fontSize: "1.3rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

export function StatGrid({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: ".7rem", marginBottom: "1rem" }}>
      {children}
    </div>
  );
}

// Reusable dark, compact hover card that floats near the cursor - replaces
// recharts' default white box. Used by every Chart so the hover styling is
// consistent across the app.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ChartTip({ active, payload, label, labelFmt }: any) {
  if (!active || !payload?.length) return null;
  const v = payload[0].value;
  return (
    <div style={{
      background: "#0d1424", border: "1px solid #2a3650", borderRadius: 8,
      padding: ".3rem .55rem", fontSize: ".78rem", lineHeight: 1.3,
      boxShadow: "0 6px 18px rgba(0,0,0,.45)", color: "var(--fg,#e7ecf3)", pointerEvents: "none",
    }}>
      <div className="muted" style={{ fontSize: ".7rem" }}>{labelFmt ? labelFmt(label) : label}</div>
      <div><strong>{v}</strong> detection{v === 1 ? "" : "s"}</div>
    </div>
  );
}

export function Chart({ data, x, fill, xfmt, interval, labelFmt, height = 150 }: {
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
          <Tooltip
            cursor={{ fill: "rgba(120,160,255,0.08)" }}
            wrapperStyle={{ outline: "none", zIndex: 20 }}
            content={(props) => <ChartTip {...props} labelFmt={labelFmt} />}
          />
          <Bar dataKey="count" fill={fill} radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// 24-bucket hour-of-day histogram drawn with plain divs - cheap enough to render
// once per day row in a long list. Highlights the peak hour.
export function HourSpark({ hourly, fill = "#4ea1ff" }: { hourly: number[]; fill?: string }) {
  const max = Math.max(1, ...hourly);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 1, height: 34 }} title="Detections by hour of day (0-23)">
      {hourly.map((c, h) => (
        <div key={h} title={`${hh(h)} · ${c}`}
          style={{
            flex: 1, height: `${Math.max(c > 0 ? 12 : 2, (c / max) * 100)}%`,
            background: c > 0 ? fill : "var(--border,#1e2636)", borderRadius: 1,
            opacity: c > 0 ? 1 : 0.5,
          }} />
      ))}
    </div>
  );
}

// Square species thumbnail. Falls back to a bird glyph while loading or when the
// species has no taxon (e.g. non-bird noise classes).
export function BirdThumb({ sci, common, size = 44 }: { sci: string; common: string; size?: number }) {
  const info = useBirdInfo(sci, common);
  const src = info?.thumb || null;
  return (
    <div style={{
      width: size, height: size, borderRadius: 8, flexShrink: 0, overflow: "hidden",
      background: "#10182a", display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      {src
        ? <img src={src} alt={common} loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        : <Bird size={size * 0.5} strokeWidth={1.6} className="muted" />}
    </div>
  );
}

// A clickable species row used in species lists; links to the bird page,
// carrying the location filter when present.
export function SpeciesLink({ sci, locationId, children, style }: {
  sci: string; locationId?: number | null; children: React.ReactNode; style?: React.CSSProperties;
}) {
  const q = `?sci=${encodeURIComponent(sci)}${locationId ? `&location_id=${locationId}` : ""}`;
  return (
    <Link to={`/apps/birds/bird${q}`} style={{ textDecoration: "none", color: "inherit", ...style }}>
      {children}
    </Link>
  );
}
