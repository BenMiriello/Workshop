import { describe, expect, it } from "vitest";
import {
  brokenCount, healthSummaryLine, mergeMcpServers, normalizeServerName,
  worstKind, type McpServerHealth,
} from "./mcpHealth";
import type { McpServer } from "./contextBreakdown";

// The two real shapes, taken from this host on 2026-08-10: `/context` grouped
// four servers by tool, `claude mcp list` reported eight configured, three of
// them failing. Everything below is about the gap between those two numbers.

const ctx: McpServer[] = [
  { server: "claude_ai_Gmail", tokens: 398, tools: [{ name: "authenticate", tokens: 173 }, { name: "complete", tokens: 225 }] },
  { server: "plaid", tokens: 351, tools: [{ name: "get_balances", tokens: 109 }, { name: "list_transactions", tokens: 242 }] },
];

function h(name: string, kind: McpServerHealth["kind"], state: string, detail = ""): McpServerHealth {
  return { name, target: "", origin: "local", transport: "stdio", kind, state, detail };
}

const health: McpServerHealth[] = [
  h("claude.ai Gmail", "warn", "Needs authentication"),
  h("plaid", "ok", "Connected"),
  h("playwright", "danger", "Failed to connect"),
  h("google-docs", "danger", "Failed to connect"),
];

describe("normalizeServerName", () => {
  it("joins the two spellings of the same server", () => {
    expect(normalizeServerName("claude_ai_Gmail")).toBe(normalizeServerName("claude.ai Gmail"));
  });

  it("does not fuse different servers", () => {
    expect(normalizeServerName("google-docs")).not.toBe(normalizeServerName("google_drive"));
  });

  it("survives the plugin naming", () => {
    expect(normalizeServerName("plugin:discord:discord")).toBe("plugin_discord_discord");
  });
});

describe("mergeMcpServers", () => {
  it("shows servers that cost nothing because they never connected", () => {
    // The whole bug: a failing server loads no tools, so `/context` never
    // mentions it, so the panel used to show four healthy servers on a host
    // where three were broken.
    const rows = mergeMcpServers(ctx, health);
    expect(rows.map((r) => r.name)).toContain("playwright");
    expect(rows).toHaveLength(4);
  });

  it("reports a broken server's cost as zero rather than omitting it", () => {
    const rows = mergeMcpServers(ctx, health);
    const pw = rows.find((r) => r.name === "playwright")!;
    expect(pw.tokens).toBe(0);
    expect(pw.tools).toEqual([]);
  });

  it("attaches health to the row that carries the cost, across the spelling gap", () => {
    const gmail = mergeMcpServers(ctx, health).find((r) => r.tokens === 398)!;
    expect(gmail.health?.state).toBe("Needs authentication");
    expect(gmail.tools).toHaveLength(2);
  });

  it("puts the broken servers first", () => {
    const rows = mergeMcpServers(ctx, health);
    expect(rows.map((r) => r.health?.kind)).toEqual(["danger", "danger", "warn", "ok"]);
  });

  it("falls back to cost order when there is no health at all", () => {
    // The health probe being unavailable must not reshuffle the list into
    // something arbitrary; it should look exactly like it did before.
    const rows = mergeMcpServers(ctx, undefined);
    expect(rows.map((r) => r.tokens)).toEqual([398, 351]);
    expect(rows.every((r) => r.health === null)).toBe(true);
  });

  it("is stable: two identical costs order by name, not by luck", () => {
    const same: McpServer[] = [
      { server: "zeta", tokens: 100, tools: [] },
      { server: "alpha", tokens: 100, tools: [] },
    ];
    expect(mergeMcpServers(same, undefined).map((r) => r.name)).toEqual(["alpha", "zeta"]);
  });

  it("handles both sides being absent", () => {
    expect(mergeMcpServers(undefined, undefined)).toEqual([]);
  });

  it("carries the failure reason through to the row that renders it", () => {
    const why = 'ENOENT: Executable not found in $PATH: "bun"';
    const rows = mergeMcpServers([], [h("plugin:discord:discord", "danger", "Failed to connect", why)]);
    expect(rows[0].health?.detail).toBe(why);
  });

  it("prefers the CLI's spelling of a name once they are matched", () => {
    const rows = mergeMcpServers(ctx, health);
    expect(rows.map((r) => r.name)).toContain("claude.ai Gmail");
    expect(rows.map((r) => r.name)).not.toContain("claude_ai_Gmail");
  });
});

describe("the section's one-line verdict", () => {
  it("counts what is not working", () => {
    const rows = mergeMcpServers(ctx, health);
    expect(brokenCount(rows)).toBe(3);
    expect(healthSummaryLine(rows)).toBe("3 of 4 not working");
  });

  it("says how many are fine, not just that they are", () => {
    const rows = mergeMcpServers(ctx, [h("plaid", "ok", "Connected")]);
    expect(healthSummaryLine(rows)).toBe("1 server, all connected");
  });

  it("says nothing when there is no health to report", () => {
    expect(healthSummaryLine(mergeMcpServers(ctx, undefined))).toBe("");
    expect(worstKind(mergeMcpServers(ctx, undefined))).toBeNull();
  });

  it("the label's pill takes the WORST state present, not the first", () => {
    expect(worstKind(mergeMcpServers(ctx, health))).toBe("danger");
    expect(worstKind(mergeMcpServers([], [h("a", "ok", "Connected"), h("b", "warn", "Needs authentication")])))
      .toBe("warn");
  });

  it("an unknown state is not counted as broken", () => {
    // "Unknown" means the CLI said something we have not seen — not evidence
    // of a failure, and claiming otherwise would cry wolf.
    const rows = mergeMcpServers([], [h("a", "stopped", "Unknown"), h("b", "ok", "Connected")]);
    expect(brokenCount(rows)).toBe(0);
    expect(worstKind(rows)).toBe("stopped");
  });
});
