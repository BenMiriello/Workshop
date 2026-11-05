// The shell's Live Activity module (mobile/native/liveActivity.ts), bundled
// with a stand-in for the Swift side: an activity belongs to the app that
// started it, update and end refuse another app's, and a state is checked and
// reduced to the contract's fields before it reaches the system.
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it } from "vitest";
import type { LiveActivityState } from "@shared/native-bridge";

const repo = (rel: string) => fileURLToPath(new URL(`../../../${rel}`, import.meta.url));

interface Module {
  isSupported(): Promise<boolean>;
  start(app: string, state: unknown): Promise<{ id: string }>;
  update(app: string, id: string, state: unknown): Promise<void>;
  end(app: string, id: string): Promise<void>;
  checked(state: unknown): LiveActivityState;
}

interface Call { method: string; args: unknown[] }

const fakeSwift = {
  activities: [] as { id: string; app: string }[],
  calls: [] as Call[],
  isSupported: () => true,
  list() { return this.activities.map((a) => ({ ...a })); },
  async start(app: string, state: unknown) {
    this.calls.push({ method: "start", args: [app, state] });
    const id = `act-${this.activities.length + 1}`;
    this.activities.push({ id, app });
    return id;
  },
  async update(id: string, state: unknown) { this.calls.push({ method: "update", args: [id, state] }); },
  async end(id: string) {
    this.calls.push({ method: "end", args: [id] });
    this.activities = this.activities.filter((a) => a.id !== id);
  },
};

function load(): Module {
  const out = buildSync({
    entryPoints: [repo("mobile/native/liveActivity.ts")],
    tsconfig: repo("macos/bridge/tsconfig.json"),
    bundle: true, write: false, format: "cjs", platform: "node", logLevel: "silent",
    external: ["expo-modules-core"],
  });
  const expo = { requireOptionalNativeModule: () => fakeSwift };
  const mod = { exports: {} as Module };
  new Function("module", "exports", "require", out.outputFiles[0].text)(mod, mod.exports, () => expo);
  return mod.exports;
}

const la = load();
const state = { headline: "Upload", status: "ok", symbol: "timer" };

beforeEach(() => {
  fakeSwift.activities = [];
  fakeSwift.calls = [];
});

describe("Live Activities are each app's own", () => {
  it("records the starting app as the owner", async () => {
    const { id } = await la.start("app-a", state);
    expect(fakeSwift.activities).toEqual([{ id, app: "app-a" }]);
  });

  it("refuses a start that names no app", async () => {
    await expect(la.start("", state)).rejects.toThrow(/belongs to an app/);
    await expect(la.start("../x", state)).rejects.toThrow(/belongs to an app/);
    expect(fakeSwift.calls).toEqual([]);
  });

  it("lets the owner update and end, and no other app", async () => {
    const { id } = await la.start("app-a", state);
    await expect(la.update("app-b", id, state)).rejects.toThrow(/No Live Activity/);
    await expect(la.end("app-b", id)).rejects.toThrow(/No Live Activity/);
    await expect(la.end("", id)).rejects.toThrow();
    expect(fakeSwift.activities).toHaveLength(1);
    await la.update("app-a", id, { ...state, headline: "Upload done" });
    await la.end("app-a", id);
    expect(fakeSwift.calls.map((c) => c.method)).toEqual(["start", "update", "end"]);
    expect(fakeSwift.activities).toEqual([]);
  });

  it("rejects an update to an activity that is gone", async () => {
    await expect(la.update("app-a", "act-9", state)).rejects.toThrow(/No Live Activity act-9/);
  });
});

describe("the state an app sends", () => {
  it("keeps only the contract's fields and clamps progress", () => {
    const out = la.checked({
      ...state, subtitle: "3 of 12 files", detail: "", progress: 1.4, extra: "dropped",
      countdownTo: "2026-10-11T07:11:00-07:00",
    });
    expect(out).toEqual({
      headline: "Upload", status: "ok", symbol: "timer", subtitle: "3 of 12 files",
      progress: 1, countdownTo: "2026-10-11T14:11:00.000Z",
    });
    expect(la.checked({ ...state, progress: -2 }).progress).toBe(0);
  });

  it("takes the kit's five status kinds and nothing else", () => {
    for (const status of ["ok", "running", "stopped", "warn", "danger"]) {
      expect(la.checked({ ...state, status }).status).toBe(status);
    }
    expect(() => la.checked({ ...state, status: "urgent" })).toThrow(/status/);
  });

  it("needs a headline and a symbol, and a readable countdown", () => {
    expect(() => la.checked({ ...state, headline: "" })).toThrow(/headline/);
    expect(() => la.checked({ status: "ok", headline: "h" })).toThrow(/symbol/);
    expect(() => la.checked({ ...state, countdownTo: "soon" })).toThrow(/countdownTo/);
    expect(() => la.checked({ ...state, progress: "half" })).toThrow(/progress/);
  });
});
