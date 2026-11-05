// The shell's injected bridge client (mobile/native/inject.ts), bundled the
// way scripts/macos-build.sh bundles it and run against a stand-in window:
// `forApp` tags each request with the app, and its `on` passes only that
// app's page events.
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { BridgeRequest, WorkshopNative } from "@shared/native-bridge";

const repo = (rel: string) => fileURLToPath(new URL(`../../../${rel}`, import.meta.url));

type Build = (tier: "free", platform: "macos", caps: string[]) => string;

function loadBuilder(): Build {
  const out = buildSync({
    entryPoints: [repo("mobile/native/inject.ts")],
    tsconfig: repo("macos/bridge/tsconfig.json"),
    bundle: true, write: false, format: "cjs", platform: "node", logLevel: "silent",
  });
  const mod = { exports: {} as { buildInjectedBridge: Build } };
  new Function("module", "exports", out.outputFiles[0].text)(mod, mod.exports);
  return mod.exports.buildInjectedBridge;
}

const buildInjectedBridge = loadBuilder();

const RECV = "__WORKSHOP_NATIVE_RECV__";

function install(caps: string[] = ["pages"]) {
  const sent: BridgeRequest[] = [];
  const win: Record<string, unknown> = {
    ReactNativeWebView: { postMessage: (m: string) => sent.push(JSON.parse(m)) },
    dispatchEvent: () => true,
  };
  new Function("window", "Event", buildInjectedBridge("free", "macos", caps))(win, class {});
  const bridge = win.WorkshopNative as WorkshopNative;
  const push = (topic: string, payload: unknown) =>
    (win[RECV] as (m: unknown) => void)({ t: "evt", topic, payload });
  return { bridge, push, requests: () => sent.filter((m) => m.t === "req") };
}

describe("WorkshopNative.forApp", () => {
  it("tags every request with its app, and the plain bridge with none", () => {
    const { bridge, requests } = install();
    void bridge.forApp!("app-a").pages.cookies.export("default");
    void bridge.forApp!("app-b").pages.closeAll();
    void bridge.pages.close("p1");
    expect(requests().map((r) => [r.method, r.app])).toEqual([
      ["cookies.export", "app-a"],
      ["closeAll", "app-b"],
      ["close", undefined],
    ]);
  });

  it("tags Live Activity calls with their app too", () => {
    const { bridge, requests } = install(["liveActivity"]);
    const state = { headline: "h", status: "ok" as const, symbol: "timer" };
    void bridge.forApp!("app-a").liveActivity.start(state);
    void bridge.forApp!("app-a").liveActivity.update("act-1", state);
    void bridge.forApp!("app-b").liveActivity.end("act-1");
    expect(requests().map((r) => [r.cap, r.method, r.app])).toEqual([
      ["liveActivity", "start", "app-a"],
      ["liveActivity", "update", "app-a"],
      ["liveActivity", "end", "app-b"],
    ]);
  });

  it("passes an app only its own page events, and other topics whole", () => {
    const { bridge, push } = install();
    const seen: Record<string, unknown[]> = { a: [], b: [], all: [] };
    bridge.forApp!("app-a").on("pages:state", (p) => seen.a.push(p));
    bridge.forApp!("app-b").on("pages:state", (p) => seen.b.push(p));
    bridge.on("pages:state", (p) => seen.all.push(p));
    push("pages:state", { id: "p1", app: "app-a" });
    push("pages:state", { id: "p1", app: "app-b" });
    expect(seen.a).toEqual([{ id: "p1", app: "app-a" }]);
    expect(seen.b).toEqual([{ id: "p1", app: "app-b" }]);
    expect(seen.all).toHaveLength(2);
  });

  it("stops passing events once unsubscribed", () => {
    const { bridge, push } = install();
    const seen: unknown[] = [];
    const off = bridge.forApp!("app-a").on("pages:error", (p) => seen.push(p));
    push("pages:error", { id: "p1", app: "app-a" });
    off();
    push("pages:error", { id: "p2", app: "app-a" });
    expect(seen).toHaveLength(1);
  });

  it("passes an app its page-less commands, and offers shell only where given", () => {
    const { bridge, push, requests } = install(["pages", "shell"]);
    const seen: unknown[] = [];
    bridge.forApp!("app-a").on("pages:command", (p) => seen.push(p));
    push("pages:command", { id: null, app: "app-a", command: "closeTab" });
    push("pages:command", { id: null, app: "app-b", command: "closeTab" });
    expect(seen).toEqual([{ id: null, app: "app-a", command: "closeTab" }]);
    void bridge.shell.foreground("app-a");
    void bridge.shell.foreground(null);
    expect(requests().map((r) => [r.cap, r.method, r.args])).toEqual([
      ["shell", "foreground", ["app-a"]],
      ["shell", "foreground", [null]],
    ]);
    expect(install(["pages"]).bridge.has("shell")).toBe(false);
  });

  it("is one object per app and refuses what is not an app id", () => {
    const { bridge } = install();
    expect(bridge.forApp!("app-a")).toBe(bridge.forApp!("app-a"));
    expect(bridge.forApp!("app-a")).not.toBe(bridge.forApp!("app-b"));
    expect(() => bridge.forApp!("../app-a")).toThrow();
    expect(() => bridge.forApp!("")).toThrow();
  });
});
