import type { CatHue } from "../../ui";

// Turning the server's context report into something a bar and a table can
// render. Pure, so the rules below are executable rather than only observable by
// opening the panel.
//
// THE HEADLINE NUMBER IS OURS, NOT THE CLI'S. The backend returns both: our
// measured `used_tokens` against our `window_tokens`, and the CLI's own totals
// from `/context`. The CLI computes against whatever window it believes it has,
// which is not always the one the thread runs. Showing its figure would put a
// second, disagreeing context percentage a few pixels from the meter that opened
// the panel — the exact class of bug this month was spent removing.
//
// So: the CLI supplies the SHAPE (which categories, how big relative to each
// other), and our measurement supplies the SIZE. When the two totals disagree,
// the breakdown is scaled to ours rather than being allowed to contradict it.

export type ContextCategory = { name: string; tokens: number };
export type McpServer = { server: string; tokens: number; tools: { name: string; tokens: number }[] };

export type ContextDetail = {
  available: boolean;
  reason?: string;
  model?: string;
  used_tokens: number;
  window_tokens: number;
  /** Epoch SECONDS at which the CLI actually ran — not when this response was
   *  served. A cached reading keeps the timestamp of the run that produced it,
   *  because that is when it was true. */
  checked_at?: number | null;
  cached?: boolean;
  cli_used_tokens?: number;
  categories?: ContextCategory[];
  mcp?: McpServer[];
  agents?: { name: string; source: string; tokens: number }[];
  memory?: { kind: string; path: string; tokens: number }[];
  skills?: { name: string; source: string; tokens: number }[];
};

export type Slice = {
  key: string;
  tokens: number;
  /** Share of the WINDOW, 0..1 — what the bar draws. */
  fraction: number;
  hue: CatHue;
};

/** "8.7k", "241", "1.2M". Tokens are read at a glance, not audited, so a
 *  thousands figure keeps one decimal and nothing keeps more. */
export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n < 1000) return String(Math.round(n));
  if (n < 1_000_000) {
    const k = n / 1000;
    return `${k < 10 ? k.toFixed(1) : Math.round(k)}k`;
  }
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** A whole percent, floored to "<1%" so a small-but-real slice never reads as
 *  nothing. Same rule the meter uses, deliberately. */
export function formatPct(fraction: number): string {
  if (!Number.isFinite(fraction) || fraction <= 0) return "0%";
  const pct = fraction * 100;
  if (pct < 1) return "<1%";
  return `${Math.round(pct)}%`;
}

// Fixed hues for the categories the CLI always reports, so the bar does not
// reshuffle its colors between two openings of the same panel. Anything
// unrecognised falls back to the stable hash (catFor), which is what that helper
// is for.
const CATEGORY_HUE: Record<string, CatHue> = {
  "messages": "violet",
  "system prompt": "blue",
  "system tools": "turquoise",
  "system tools (deferred)": "turquoise",
  "mcp tools": "orchid",
  "mcp tools (deferred)": "orchid",
  "memory files": "indigo",
  "skills": "slate",
  "custom agents": "slate",
};

function hueFor(name: string, fallback: (k: string) => CatHue): CatHue {
  return CATEGORY_HUE[name.trim().toLowerCase()] ?? fallback(name);
}

/**
 * The bar: one slice per category, as a fraction of OUR window.
 *
 * Scaled when the CLI's total disagrees with ours. The CLI's per-category
 * numbers are estimates against its own accounting; our `used_tokens` is what
 * the model actually reported reading. Rather than showing a bar whose segments
 * sum to something other than the number printed above it, the shape is kept and
 * the magnitude is taken from the measurement.
 */
export function contextSlices(
  d: ContextDetail, catFor: (k: string) => CatHue,
): Slice[] {
  const cats = d.categories ?? [];
  if (!cats.length || d.window_tokens <= 0) return [];
  const cliTotal = cats.reduce((a, c) => a + c.tokens, 0);
  if (cliTotal <= 0) return [];
  // Ours when we have one, else the CLI's own sum: a thread whose first turn has
  // not landed has no measurement to scale to, and the breakdown is still true
  // in shape.
  const total = d.used_tokens > 0 ? d.used_tokens : cliTotal;
  const scale = total / cliTotal;
  return cats.map((c) => ({
    key: c.name,
    tokens: Math.round(c.tokens * scale),
    fraction: (c.tokens * scale) / d.window_tokens,
    hue: hueFor(c.name, catFor),
  }));
}

/** What is left. The bar's last segment, and deliberately NOT a category: free
 *  space is not a thing filling the window, it is the absence of one. */
export function freeFraction(d: ContextDetail): number {
  if (d.window_tokens <= 0) return 0;
  return Math.max(0, Math.min(1, 1 - d.used_tokens / d.window_tokens));
}

/** One line for the panel's header: "440k of 1M used". */
export function contextHeadline(d: ContextDetail): string {
  if (d.window_tokens <= 0) return `${formatTokens(d.used_tokens)} used`;
  return `${formatTokens(d.used_tokens)} of ${formatTokens(d.window_tokens)} used`;
}

/** Every MCP server's share, largest first, with its own total. The one line in
 *  the panel the operator can actually act on: a server's tools are a standing
 *  tax on every turn, paid whether or not they are ever called. */
export function mcpTotal(d: ContextDetail): number {
  return (d.mcp ?? []).reduce((a, s) => a + s.tokens, 0);
}

/**
 * "Refreshed 2m ago" for the panel's footer.
 *
 * The reading is now CACHED server-side, so a panel can open instantly with a
 * report that was computed minutes earlier. That is only acceptable if the
 * panel says how old it is — an unlabelled stale number is worse than a slow
 * fresh one, and this month has been spent removing screens that showed a
 * figure without saying which figure it was.
 *
 * `checkedAt` is epoch SECONDS (the server's unit); `now` is epoch millis
 * (JS's). Empty string when there is nothing to date, so the caller can render
 * it unconditionally.
 */
export function refreshedLabel(checkedAt: number | null | undefined, now: number): string {
  if (!checkedAt || !Number.isFinite(checkedAt)) return "";
  const seconds = Math.max(0, (now - checkedAt * 1000) / 1000);
  if (seconds < 45) return "Refreshed just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `Refreshed ${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `Refreshed ${hours}h ago`;
  return `Refreshed ${Math.round(hours / 24)}d ago`;
}
