import {
  Sun, Cloud, CloudSun, CloudRain, CloudSnow, CloudFog, CloudDrizzle, Zap,
  TrendingUp, TrendingDown, Activity,
} from "lucide-react";

// Shared forecast display helpers, used by the "Right now" card and the week view.

export type Level = "elevated" | "normal" | "subdued" | "low";

// WMO weather code -> short label + icon (Open-Meteo `weather_code`).
export function wx(code: number | null | undefined): { label: string; Icon: typeof Sun } {
  if (code == null) return { label: "-", Icon: CloudSun };
  if (code <= 1) return { label: code === 0 ? "Clear" : "Mainly clear", Icon: Sun };
  if (code === 2) return { label: "Partly cloudy", Icon: CloudSun };
  if (code === 3) return { label: "Overcast", Icon: Cloud };
  if (code === 45 || code === 48) return { label: "Fog", Icon: CloudFog };
  if (code >= 51 && code <= 57) return { label: "Drizzle", Icon: CloudDrizzle };
  if (code >= 61 && code <= 67) return { label: "Rain", Icon: CloudRain };
  if (code >= 71 && code <= 77) return { label: "Snow", Icon: CloudSnow };
  if (code >= 80 && code <= 82) return { label: "Showers", Icon: CloudRain };
  if (code >= 85 && code <= 86) return { label: "Snow showers", Icon: CloudSnow };
  if (code >= 95) return { label: "Thunderstorm", Icon: Zap };
  return { label: "-", Icon: CloudSun };
}

// Activity level -> icon + accent token (not status hues; this isn't ok/warn/danger).
export const OUTLOOK: Record<Level, { label: string; Icon: typeof Sun; tone: string }> = {
  elevated: { label: "Elevated", Icon: TrendingUp, tone: "var(--accent)" },
  normal: { label: "Normal", Icon: Activity, tone: "var(--text)" },
  subdued: { label: "Subdued", Icon: TrendingDown, tone: "var(--muted)" },
  low: { label: "Low", Icon: TrendingDown, tone: "var(--muted)" },
};

export const cToF = (c: number) => Math.round(c * 9 / 5 + 32);
export const localTime = (iso: string | null | undefined) => {
  if (!iso) return "-";
  try { return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
  catch { return "-"; }
};
export const dayLen = (min: number | null | undefined) =>
  (min == null ? "-" : `${Math.floor(min / 60)}h ${Math.round(min % 60)}m`);
export const pressureSub = (t: number | null | undefined) => {
  if (t == null) return undefined;
  if (t <= -1) return `falling ${Math.abs(t)} / 3h`;
  if (t >= 1) return `rising ${t} / 3h`;
  return "steady";
};
// Time from now until a future ISO instant, as "Xh Ym", or null if it's past.
export const untilLabel = (iso: string | null | undefined) => {
  if (!iso) return null;
  try {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return null;
    const h = Math.floor(ms / 3600000), m = Math.round((ms % 3600000) / 60000);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  } catch { return null; }
};
