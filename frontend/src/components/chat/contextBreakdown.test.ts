import { describe, expect, it } from "vitest";
import {
  contextHeadline, contextSlices, formatPct, formatTokens, freeFraction, mcpTotal,
  type ContextDetail,
} from "./contextBreakdown";

const catFor = () => "slate" as const;

const detail = (over: Partial<ContextDetail> = {}): ContextDetail => ({
  available: true,
  used_tokens: 100_000,
  window_tokens: 1_000_000,
  cli_used_tokens: 50_000,
  categories: [
    { name: "Messages", tokens: 30_000 },
    { name: "System tools", tokens: 15_000 },
    { name: "MCP tools", tokens: 5_000 },
  ],
  mcp: [
    { server: "plaid", tokens: 3_000, tools: [{ name: "a", tokens: 3_000 }] },
    { server: "gmail", tokens: 2_000, tools: [{ name: "b", tokens: 2_000 }] },
  ],
  ...over,
});

describe("formatTokens", () => {
  it("reads at a glance", () => {
    expect(formatTokens(241)).toBe("241");
    expect(formatTokens(8_700)).toBe("8.7k");
    expect(formatTokens(136_400)).toBe("136k");
    expect(formatTokens(1_200_000)).toBe("1.2M");
  });

  it("is zero rather than blank for nothing", () => {
    expect(formatTokens(0)).toBe("0");
  });
});

describe("formatPct", () => {
  it("never renders a real slice as nothing", () => {
    // Same rule the meter uses: a small-but-present slice reads "<1%", never 0%.
    expect(formatPct(0.004)).toBe("<1%");
    expect(formatPct(0)).toBe("0%");
    expect(formatPct(0.44)).toBe("44%");
  });
});

describe("contextSlices", () => {
  it("scales the CLI's shape to OUR measured total", () => {
    // The panel is opened from a meter. If the meter says 100k and the bar's
    // segments sum to the CLI's 50k, the two disagree a few pixels apart —
    // which is the bug this whole rule exists to prevent.
    const s = contextSlices(detail(), catFor);
    const sum = s.reduce((a, x) => a + x.tokens, 0);
    expect(sum).toBe(100_000);
  });

  it("keeps the relative shape while doing it", () => {
    const s = contextSlices(detail(), catFor);
    expect(s[0].tokens / s[1].tokens).toBeCloseTo(2, 5);
  });

  it("measures the bar against the WINDOW, not against what is used", () => {
    // A bar whose segments fill their track at 10% usage would say "full".
    const s = contextSlices(detail(), catFor);
    expect(s.reduce((a, x) => a + x.fraction, 0)).toBeCloseTo(0.1, 5);
  });

  it("falls back to the CLI's own total before the first turn has measured", () => {
    const s = contextSlices(detail({ used_tokens: 0 }), catFor);
    expect(s.reduce((a, x) => a + x.tokens, 0)).toBe(50_000);
  });

  it("gives the same category the same hue every time", () => {
    const a = contextSlices(detail(), catFor);
    const b = contextSlices(detail(), catFor);
    expect(a.map((x) => x.hue)).toEqual(b.map((x) => x.hue));
    // Messages and System tools must not collide, or the bar is unreadable.
    expect(a[0].hue).not.toBe(a[1].hue);
  });

  it("returns nothing rather than dividing by zero", () => {
    expect(contextSlices(detail({ categories: [] }), catFor)).toEqual([]);
    expect(contextSlices(detail({ window_tokens: 0 }), catFor)).toEqual([]);
  });
});

describe("freeFraction", () => {
  it("is what is left", () => {
    expect(freeFraction(detail())).toBeCloseTo(0.9, 5);
  });

  it("never goes negative on an over-full window", () => {
    expect(freeFraction(detail({ used_tokens: 2_000_000 }))).toBe(0);
  });
});

describe("the header and the MCP total", () => {
  it("says both numbers, so the percentage is checkable", () => {
    expect(contextHeadline(detail())).toBe("100k of 1.0M used");
  });

  it("totals what the MCP servers cost every turn", () => {
    expect(mcpTotal(detail())).toBe(5_000);
  });
});
