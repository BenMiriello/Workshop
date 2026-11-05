// The page download hand-off (shared/native-page-downloads.ts): the kit's
// mirror of the contract matches the shell's, a download event reaches only
// the app whose page it came from, and the rule that turns it on travels
// with the app's other navigation rules.
import { buildSync } from "esbuild";
import { fileURLToPath } from "node:url";
import { describe, expect, it, expectTypeOf } from "vitest";
import type { PageDownloadEvent as KitDownload, PageNavigationRules as KitRules } from "@workshop/ui";
import type { BridgeRequest, WorkshopNative } from "@shared/native-bridge";
import type { PageDownloadEvent, PageNavigationRules } from "@shared/native-pages";
import { PAGES_METHODS } from "@shared/native-pages";

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

function install() {
  const sent: BridgeRequest[] = [];
  const win: Record<string, unknown> = {
    ReactNativeWebView: { postMessage: (m: string) => sent.push(JSON.parse(m)) },
    dispatchEvent: () => true,
  };
  new Function("window", "Event", buildInjectedBridge("free", "macos", ["pages"]))(win, class {});
  const push = (topic: string, payload: unknown) =>
    (win.__WORKSHOP_NATIVE_RECV__ as (m: unknown) => void)({ t: "evt", topic, payload });
  return { bridge: win.WorkshopNative as WorkshopNative, push, requests: () => sent.filter((m) => m.t === "req") };
}

describe("page downloads contract", () => {
  it("is the same in the kit and in the shell's contract", () => {
    expectTypeOf<KitDownload>().toEqualTypeOf<PageDownloadEvent>();
    expectTypeOf<KitRules["downloadsToApp"]>().toEqualTypeOf<PageNavigationRules["downloadsToApp"]>();
    expectTypeOf<PageDownloadEvent["reason"]>().toEqualTypeOf<"attachment" | "unshowable">();
  });

  it("is set with the app's navigation rules, which the shell already routes", () => {
    expect(PAGES_METHODS).toContain("navigationRules.set");
    const { bridge, requests } = install();
    const rules: PageNavigationRules = { blockUntappedJumps: true, downloadsToApp: true };
    void bridge.forApp!("app-a").pages.navigationRules.set(rules);
    expect(requests().map((r) => [r.method, r.app, r.args])).toEqual([
      ["navigationRules.set", "app-a", [rules]],
    ]);
  });

  it("passes pages:download to the app whose page it came from, whole", () => {
    const { bridge, push } = install();
    const seen: Record<string, PageDownloadEvent[]> = { a: [], b: [] };
    bridge.forApp!("app-a").on<PageDownloadEvent>("pages:download", (e) => seen.a.push(e));
    bridge.forApp!("app-b").on<PageDownloadEvent>("pages:download", (e) => seen.b.push(e));
    const event: PageDownloadEvent = {
      id: "p1", app: "app-a", url: "https://files.example.org/a.zip", from: "https://example.org/",
      filename: "a.zip", mime: "application/zip", bytes: 10, reason: "attachment",
    };
    push("pages:download", event);
    push("pages:download", { id: "p2", app: "app-b", url: "https://files.example.org/b", from: "", reason: "unshowable" });
    expect(seen.a).toEqual([event]);
    expect(seen.b.map((e) => e.id)).toEqual(["p2"]);
  });
});
