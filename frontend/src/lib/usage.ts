// Usage API — the rolling 5-hour token budget for autonomous background work.
// Backed by GET /api/usage (backend routes/usage -> services.usage_ledger.status).
// Interactive chat turns are never metered, so they never appear here; only
// autonomous sweeps (cron, project/agent Ralph, drained events) write tokens.

export type ProjectUsage = {
  used: number;
  allowance: number;
  remaining: number;
  share_of_app: number; // 0..1 fraction of the window's app-wide usage
};

export type UsageStatus = {
  window_s: number; // trailing window length in seconds (18000 = 5h)
  app_total: number; // app-wide token ceiling for the window
  app_used: number;
  app_remaining: number;
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
  projects: string[];
  points: UsagePoint[];
};

export const usageApi = {
  status: (): Promise<UsageStatus> =>
    fetch("/api/usage").then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => r.statusText)}`);
      return r.json() as Promise<UsageStatus>;
    }),
  setBudget: (patch: BudgetPatch): Promise<UsageStatus> =>
    fetch("/api/usage/budget", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "x-workshop-client": "1" },
      body: JSON.stringify(patch),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => r.statusText)}`);
      return r.json() as Promise<UsageStatus>;
    }),
  timeseries: (): Promise<UsageSeries> =>
    fetch("/api/usage/timeseries").then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text().catch(() => r.statusText)}`);
      return r.json() as Promise<UsageSeries>;
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
