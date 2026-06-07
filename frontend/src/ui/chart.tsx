import type { ReactNode } from "react";
import { cx } from "./cx";

// Chart series colors — the categorical suite (brand violet first). Recharts
// needs concrete color strings for stroke/fill props, so these mirror the
// --cat-* token hexes from tokens.css. NEVER use status hues for series, so a
// trending line is never misread as good/bad.
export const CHART_SERIES = [
  "#7a57e0", // cat-violet (brand)
  "#10b4bf", // cat-turquoise
  "#b35fd6", // cat-orchid
  "#3f7df0", // cat-blue
  "#5160ee", // cat-indigo
  "#7c8aa0", // cat-slate
] as const;

// Reference / target lines are a dashed neutral slate — quieter than the data.
export const CHART_TARGET = "#9594a6"; // stopped-solid

// Wrap any recharts surface in ChartFrame so the bundled .recharts-* rules in
// components.css restyle it (muted axes, hairline grid, no heavy frame) without
// touching the chart JSX. Override --chart-* via the style prop per chart.
export function ChartFrame({
  children,
  className,
  style,
}: {
  children: ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div className={cx("ws-chart", className)} style={style}>
      {children}
    </div>
  );
}

// Custom recharts tooltip that reuses the card/elevation language (never a raw
// white box). Pass as <Tooltip content={<ChartTooltip />} /> — recharts injects
// `active` / `payload` / `label`. `format` maps a raw value to display text.
export function ChartTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean;
  payload?: Array<{ value?: number | string; name?: string; color?: string }>;
  label?: ReactNode;
  format?: (value: number | string) => ReactNode;
}) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="ws-chart-tooltip">
      {label != null && <div className="ws-chart-tooltip__label">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="ws-chart-tooltip__val" style={{ color: p.color }}>
          {p.value != null ? (format ? format(p.value) : p.value) : "—"}
        </div>
      ))}
    </div>
  );
}
