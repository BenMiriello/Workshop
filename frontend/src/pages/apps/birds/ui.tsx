import { Link } from "react-router-dom";
import {
  Bar, ComposedChart, Line, ReferenceLine, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Bird } from "lucide-react";
import { CHART_SERIES, CHART_TARGET, ChartTooltip } from "../../../ui";

// Re-exported so the bird pages get their chart colors from the design-system
// categorical suite (brand violet first) via the shared birds ui module.
export { CHART_SERIES };
import { useBirdInfo } from "./birdinfo";

// Small shared pieces for the bird pages: formatters, a fetch helper, the stat
// tile, a recharts bar chart, a lightweight CSS sparkbar histogram, and a
// species thumbnail that lazy-loads its photo from the bird-info cache.

export const API = "/api/birds";
export const pct = (c: number) => `${Math.round(c * 100)}%`;
// "##% confidence" — the wording the species/detection rows use.
export const conf = (c: number) => `${Math.round(c * 100)}% confidence`;
// Hour-of-day label in 12-hour am/pm (the app uses 12-hour time everywhere).
export const hh = (h: number) => {
  const ap = h < 12 ? "AM" : "PM";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return `${h12} ${ap}`;
};

// A device is "recording" if it posted very recently. The Pi yard bridge POSTs a
// block every ~3s while it's streaming, and the browser recorder refreshes
// last_seen while live, so a 60s window cleanly separates "recording right now"
// from "went quiet". Used to drive the live indicator on the header + device cards.
export const RECORDING_WINDOW_S = 60;
export function isRecording(lastSeen: string | null, withinSec = RECORDING_WINDOW_S): boolean {
  if (!lastSeen) return false;
  const t = new Date(lastSeen).getTime();
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) / 1000 < withinSec;
}

export async function jget<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

// Form-encoded POST for the trust-model actions (confirm/reject/mute/auto/review).
export async function apost<T = unknown>(path: string, body: Record<string, string | number | boolean>): Promise<T> {
  const fd = new FormData();
  for (const [k, v] of Object.entries(body)) fd.append(k, String(v));
  const r = await fetch(`${API}${path}`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(String(r.status));
  return r.json() as Promise<T>;
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: ".55rem .6rem" }}>
      <div className="muted" style={{ fontSize: ".72rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</div>
      <div style={{ fontSize: "1.2rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

export function StatGrid({ children }: { children: React.ReactNode }) {
  // Narrow min track so the four summary tiles fit on ONE row even on a phone
  // (~360px), instead of wrapping to two rows; they still grow to fill wider
  // screens via 1fr.
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(74px,1fr))", gap: ".5rem", marginBottom: "1rem" }}>
      {children}
    </div>
  );
}

// Optional overlays for the times-of-day view: vertical reference lines (dawn /
// dusk) and a secondary line series (e.g. temperature) on a right axis. Both are
// off unless their props are passed, so every existing caller is unchanged.
export type ChartMarker = { x: number | string; label?: string };
export type ChartOverlay = { key: string; name?: string; color?: string; fmt?: (v: number) => string };
// A secondary series sharing the LEFT count axis (same scale as the bars), e.g.
// species-diversity per hour. Distinct from `overlay`, which gets its own right axis.
export type ChartLine = { key: string; name?: string; color?: string };

export function Chart({ data, x, fill, xfmt, interval, labelFmt, height = 150, onBarClick, markers, overlay, lines }: {
  data: Record<string, unknown>[]; x: string; fill: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  xfmt?: (v: any) => string; interval?: number; labelFmt?: (v: any) => string; height?: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onBarClick?: (datum: any) => void;
  markers?: ChartMarker[]; overlay?: ChartOverlay; lines?: ChartLine[];
}) {
  const hasOverlay = !!overlay;
  const hasLines = !!(lines && lines.length);
  const namedSeries = hasOverlay || hasLines;
  return (
    <div style={{ height }}>
      <ResponsiveContainer>
        {/* ComposedChart renders the bar identically to BarChart but also lets us
            layer the optional temperature Line + dawn/dusk ReferenceLines. */}
        <ComposedChart data={data} margin={{ top: 4, right: hasOverlay ? 4 : 8, left: -18, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey={x} tick={{ fontSize: 10 }} tickFormatter={xfmt} interval={interval} minTickGap={16} />
          <YAxis yAxisId="count" tick={{ fontSize: 10 }} allowDecimals={false} />
          {hasOverlay && (
            <YAxis yAxisId="ov" orientation="right" width={30} tick={{ fontSize: 10 }}
              tickFormatter={overlay!.fmt} />
          )}
          {/* Lines (e.g. diversity) get their own hidden, auto-scaled axis so the
              SHAPE shows regardless of magnitude — a 0–2.5 Shannon line isn't
              flattened against a 0–60 detection-count axis. Value lives in tooltip. */}
          {hasLines && <YAxis yAxisId="ln" hide domain={["auto", "auto"]} />}
          <Tooltip
            isAnimationActive={false}
            cursor={{ fill: "rgba(120,160,255,0.08)" }}
            content={({ active, payload, label }: any) => (
              <ChartTooltip
                active={active}
                payload={payload}
                label={labelFmt ? labelFmt(label) : label}
                showName={namedSeries}
                format={(v) => (namedSeries ? String(v) : `${v} detection${v === 1 ? "" : "s"}`)}
              />
            )}
          />
          {markers?.map((m, i) => (
            <ReferenceLine key={`mk${i}`} yAxisId="count" x={m.x} stroke={CHART_TARGET}
              strokeDasharray="3 3"
              label={{ value: m.label, position: "top", fontSize: 9, fill: "var(--muted)" }} />
          ))}
          {/* Cap bar width so a chart with one or two days doesn't render a
              single absurdly-wide bar across the whole panel. */}
          <Bar yAxisId="count" dataKey="count" name="Detections" fill={fill} radius={[2, 2, 0, 0]} maxBarSize={48}
            onClick={onBarClick ? (e: any) => onBarClick(e?.payload ?? e) : undefined}
            style={onBarClick ? { cursor: "pointer" } : undefined} />
          {lines?.map((ln, i) => (
            <Line key={`ln${ln.key}`} yAxisId="ln" type="monotone" dataKey={ln.key}
              name={ln.name ?? ln.key} stroke={ln.color ?? CHART_SERIES[(i + 2) % CHART_SERIES.length]}
              strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} />
          ))}
          {hasOverlay && (
            <Line yAxisId="ov" type="monotone" dataKey={overlay!.key} name={overlay!.name ?? overlay!.key}
              stroke={overlay!.color ?? CHART_SERIES[0]} strokeWidth={1.5} dot={false}
              connectNulls isAnimationActive={false} />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

// 24-bucket hour-of-day histogram drawn with plain divs - cheap enough to render
// once per day row in a long list. Highlights the peak hour.
export function HourSpark({ hourly, fill = CHART_SERIES[3] }: { hourly: number[]; fill?: string }) {
  const max = Math.max(1, ...hourly);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 1, height: 34 }} title="Detections by hour of day (0-23)">
      {hourly.map((c, h) => (
        <div key={h} title={`${hh(h)} · ${c}`}
          style={{
            flex: 1, height: `${Math.max(c > 0 ? 12 : 2, (c / max) * 100)}%`,
            background: c > 0 ? fill : "var(--border)", borderRadius: 1,
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
      background: "var(--bg-inset)", display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      {src
        ? <img src={src} alt={common} loading="lazy" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        : <Bird size={size * 0.5} strokeWidth={1.6} className="muted" />}
    </div>
  );
}

// A clickable species row used in species lists; links to the bird page,
// carrying the location filter when present.
export function SpeciesLink({ sci, common, locationId, status, children, style }: {
  sci: string; common?: string; locationId?: number | null; status?: string; children: React.ReactNode; style?: React.CSSProperties;
}) {
  // Carry the common name so the species page header shows it instantly, with no
  // latin-name flash while /bird loads.
  const q = `?sci=${encodeURIComponent(sci)}${common ? `&common=${encodeURIComponent(common)}` : ""}`
    + (locationId ? `&location_id=${locationId}` : "")
    + (status && status !== "confirmed" ? `&status=${status}` : "");
  return (
    <Link to={`/apps/birds/bird${q}`} style={{ textDecoration: "none", color: "inherit", ...style }}>
      {children}
    </Link>
  );
}
