import { shortHttpError } from "@workshop/ui";
// Usage API — the rolling 5-hour token budget for autonomous background work.
// Backed by GET /api/usage (backend routes/usage -> services.usage_ledger.status).
// Interactive chat turns are never metered, so they never appear here; only
// autonomous sweeps (cron, project/agent Ralph, drained events) write tokens.

// Selectable lookback. Only "5h" is the BUDGET window; the rest are
// reporting-only, because the cap is defined as a rolling 5h ceiling and is
// meaningless against a 7-day total.
export type UsageWindow = "5h" | "24h" | "7d" | "30d" | "all";

export type ProjectUsage = {
  used: number;
  // null on an uncapped window — there is no allowance to report, and a number
  // here would be a 5h figure masquerading as a bound on a longer total.
  allowance: number | null;
  remaining: number | null;
  share_of_app: number; // 0..1 fraction of the window's app-wide usage
};

export type UsageStatus = {
  window_s: number; // trailing window length in seconds (18000 = 5h)
  capped: boolean; // true only for the 5h budget window
  app_total: number | null; // app-wide ceiling; null when uncapped
  app_used: number;
  app_remaining: number | null;
  projects: Record<string, ProjectUsage>;
};

export type BudgetPatch = {
  app_total?: number;
  autonomy_budget_fraction?: number;
};

// A point in the rolling-window time series: an ISO timestamp plus a cumulative
// token total per project key. The last point equals the current window total.
export type UsagePoint = { t: string } & Record<string, number | string>;

export type UsageSeries = {
  window_s: number;
  bin_s?: number; // width of each bin in seconds (per-interval usage)
  projects: string[];
  points: UsagePoint[];
};

export type ChatSeries = {
  window_s: number;
  bin_s?: number;
  chats: string[]; // thread IDs; "_unbound" for entries without a chat field
  points: UsagePoint[];
};

export const usageApi = {
  status: (window: UsageWindow = "5h"): Promise<UsageStatus> =>
    fetch(`/api/usage?window=${encodeURIComponent(window)}`).then(async (r) => {
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json() as Promise<UsageStatus>;
    }),
  setBudget: (patch: BudgetPatch): Promise<UsageStatus> =>
    fetch("/api/usage/budget", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "x-workshop-client": "1" },
      body: JSON.stringify(patch),
    }).then(async (r) => {
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json() as Promise<UsageStatus>;
    }),
  timeseries: (window: UsageWindow = "5h"): Promise<UsageSeries> =>
    fetch(`/api/usage/timeseries?window=${encodeURIComponent(window)}`).then(async (r) => {
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json() as Promise<UsageSeries>;
    }),
  chatTimeseries: (): Promise<ChatSeries> =>
    fetch("/api/usage/chat-timeseries").then(async (r) => {
      if (!r.ok) throw new Error(shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
      return r.json() as Promise<ChatSeries>;
    }),
};

// Compact token count: 1_950_000 -> "1.95M", 150_000 -> "150K", 900 -> "900".
export function fmtTokens(n: number): string {
  if (!Number.isFinite(n)) return "0";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(Math.round(n));
}
