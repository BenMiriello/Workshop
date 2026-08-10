import { describe, expect, it } from "vitest";
import { autonomyIsLive, describeAutonomy } from "./autonomy";

describe("describeAutonomy", () => {
  it("is running only when a worker is resident AND work is permitted", () => {
    const d = describeAutonomy({ running: true, enabled: true });
    expect(d.kind).toBe("running");
    expect(d.label).toBe("Running");
  });

  it("never reports running from the permission flag alone", () => {
    // The exact bug: the override flag is clear, so the old summary row said
    // "all autonomy: running" while no worker existed.
    const d = describeAutonomy({ running: false, enabled: true });
    expect(d.kind).not.toBe("running");
    expect(d.kind).toBe("warn");
    expect(d.hint).toMatch(/queue/i);
  });

  it("says paused when a worker is resident but work is forbidden", () => {
    const d = describeAutonomy({ running: true, enabled: false });
    expect(d.kind).toBe("warn");
    expect(d.label).toBe("Paused");
  });

  it("says stopped when nothing runs and nothing is permitted", () => {
    const d = describeAutonomy({ running: false, enabled: false });
    expect(d.kind).toBe("stopped");
    expect(d.label).toBe("Stopped");
  });

  it("only ever uses the five design status kinds", () => {
    const kinds = new Set(["ok", "running", "stopped", "warn", "danger"]);
    for (const running of [true, false]) {
      for (const enabled of [true, false]) {
        expect(kinds.has(describeAutonomy({ running, enabled }).kind)).toBe(true);
      }
    }
  });
});

describe("autonomyIsLive", () => {
  it("requires both halves", () => {
    expect(autonomyIsLive({ running: true, enabled: true })).toBe(true);
    expect(autonomyIsLive({ running: true, enabled: false })).toBe(false);
    expect(autonomyIsLive({ running: false, enabled: true })).toBe(false);
    expect(autonomyIsLive({ running: false, enabled: false })).toBe(false);
  });
});
