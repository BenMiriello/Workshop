import type { StatusKind } from "../../ui";
import type { McpServer } from "./contextBreakdown";

// MCP servers: what they COST, and whether they WORK.
//
// Those two facts arrive from two different places and neither is complete on
// its own, which is the whole reason this module exists:
//
//   `/context`  knows the token cost, and only sees servers whose tools LOADED.
//   `mcp list`  knows the health, and only sees servers that are CONFIGURED.
//
// A server that fails to connect loads no tools, so it appears in the second
// list and NOT the first. Rendering only the first is exactly the bug this is
// here to fix: on 2026-08-10 three servers on this host were failing and the
// panel showed four healthy ones, because the broken ones had contributed
// nothing to count. The union is the only honest list.
//
// The join is by name, and the two sources spell names differently —
// `/context` reports `claude_ai_Gmail`, `mcp list` reports `claude.ai Gmail` —
// so both sides are normalised before matching.

export type McpServerHealth = {
  name: string;
  target: string;
  /** claude.ai | plugin | remote | local. Derived from the CLI line; the CLI
   *  prints no user/project/local scope column, so this is not that. */
  origin: string;
  transport: string;
  kind: StatusKind;
  /** The CLI's own words: "Connected", "Needs authentication", … */
  state: string;
  /** Why, when the CLI says why: `ENOENT: Executable not found in $PATH: "bun"`.
   *  Empty from a terminal, which prints no reason. The most actionable string
   *  in the section when it is there, so it gets its own line rather than being
   *  crushed into the status. */
  detail?: string;
};

export type McpHealth = {
  available: boolean;
  reason?: string;
  servers?: McpServerHealth[];
  counts?: Record<string, number>;
  /** Epoch SECONDS at which the CLI actually ran. Null when it never did. */
  checked_at?: number | null;
  cached?: boolean;
};

export type McpRow = {
  key: string;
  name: string;
  tokens: number;
  tools: { name: string; tokens: number }[];
  health: McpServerHealth | null;
};

/** `claude_ai_Gmail` and `claude.ai Gmail` are the same server. Anything that
 *  is not a letter or digit is a separator, and separators do not distinguish
 *  servers. */
export function normalizeServerName(name: string): string {
  return (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

// How urgently a row wants to be looked at. Broken first: the question the
// operator opens this section with is "is anything wrong", and a list whose top
// row is the failure has already answered it. Rows with no health at all sort
// last — when the health probe is unavailable EVERY row is in that bucket, so
// the ordering falls back to cost, which is what it was before health existed.
const RANK: Record<string, number> = { danger: 0, warn: 1, stopped: 2, ok: 3 };

function rank(row: McpRow): number {
  return row.health ? (RANK[row.health.kind] ?? 2) : 4;
}

/**
 * One row per server the machine has, whether or not it produced tools.
 *
 * Ordered by how broken it is, then by what it costs, then by name — the last
 * for stability, so two openings of the same panel never disagree.
 */
export function mergeMcpServers(
  fromContext: McpServer[] | undefined,
  health: McpServerHealth[] | undefined,
): McpRow[] {
  const byName = new Map<string, McpServerHealth>();
  for (const h of health ?? []) byName.set(normalizeServerName(h.name), h);

  const rows: McpRow[] = [];
  const claimed = new Set<string>();
  for (const s of fromContext ?? []) {
    const key = normalizeServerName(s.server);
    const h = byName.get(key) ?? null;
    if (h) claimed.add(key);
    rows.push({ key, name: h?.name || s.server, tokens: s.tokens, tools: s.tools, health: h });
  }
  for (const [key, h] of byName) {
    if (claimed.has(key)) continue;
    // Zero is the true cost of a server whose tools never loaded, and saying so
    // is the point: it is a startup attempt on every turn that buys nothing.
    rows.push({ key, name: h.name, tokens: 0, tools: [], health: h });
  }

  rows.sort((a, b) =>
    rank(a) - rank(b) || b.tokens - a.tokens || a.name.localeCompare(b.name));
  return rows;
}

/** How many servers are not working. The number that decides whether the
 *  section needs a warning on its label. */
export function brokenCount(rows: McpRow[]): number {
  return rows.filter((r) => r.health && (r.health.kind === "danger" || r.health.kind === "warn")).length;
}

/** The worst state present, for the section label's own pill. `null` when there
 *  is no health to report. */
export function worstKind(rows: McpRow[]): StatusKind | null {
  let worst: StatusKind | null = null;
  let best = 99;
  for (const r of rows) {
    if (!r.health) continue;
    const n = RANK[r.health.kind] ?? 2;
    if (n < best) { best = n; worst = r.health.kind; }
  }
  return worst;
}

/** "3 of 8 servers not working" / "8 servers, all connected". Says the count
 *  either way: "all connected" is only meaningful next to how many that is. */
export function healthSummaryLine(rows: McpRow[]): string {
  const withHealth = rows.filter((r) => r.health);
  if (!withHealth.length) return "";
  const bad = brokenCount(rows);
  const n = withHealth.length;
  const servers = `${n} server${n === 1 ? "" : "s"}`;
  return bad ? `${bad} of ${n} not working` : `${servers}, all connected`;
}
