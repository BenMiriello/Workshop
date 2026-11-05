import { afterEach, describe, expect, it, vi } from "vitest";
import { frameApp, pagesBridge } from "./pages";
import type { WorkshopNative } from "./types";

describe("frameApp", () => {
  it("reads the slug from both framings of an app", () => {
    expect(frameApp("/base-apps/run/app-a/proxy/web/")).toBe("app-a");
    expect(frameApp("/api/base-apps/run/app.b/proxy/web/deep/page")).toBe("app.b");
    expect(frameApp("/api/base-apps/run/app%2Dc/proxy/web/")).toBe("app-c");
  });

  it("is undefined outside an app's served pages", () => {
    expect(frameApp("/")).toBeUndefined();
    expect(frameApp("/apps/app-a/ui/web/")).toBeUndefined();
    expect(frameApp("/api/base-apps/run/app-a/status")).toBeUndefined();
    expect(frameApp("/x/base-apps/run/app-a/proxy/web/")).toBeUndefined();
  });
});

type Listener = () => void;

function fakeBridge(scoped?: (app: string) => WorkshopNative) {
  const closeAll = vi.fn(async () => {});
  const bridge = {
    has: (cap: string) => cap === "pages",
    pages: { closeAll },
    forApp: scoped,
  } as unknown as WorkshopNative;
  return { bridge, closeAll };
}

function frameAt(pathname: string, top: WorkshopNative) {
  const listeners: Record<string, Listener[]> = {};
  const parent = { WorkshopNative: top } as unknown as Window;
  (parent as unknown as { parent: unknown }).parent = parent;
  const win = {
    location: { pathname },
    parent,
    addEventListener: (type: string, fn: Listener) => (listeners[type] ??= []).push(fn),
  };
  vi.stubGlobal("window", win);
  return listeners;
}

afterEach(() => vi.unstubAllGlobals());

describe("pagesBridge from an app frame", () => {
  it("acts for the frame's app, and closes only its pages when the frame goes", () => {
    const apps: string[] = [];
    const own = fakeBridge();
    const top = fakeBridge((app) => {
      apps.push(app);
      return own.bridge;
    });
    const listeners = frameAt("/api/base-apps/run/app-a/proxy/web/", top.bridge);
    expect(pagesBridge()).toBe(own.bridge);
    expect(apps).toEqual(["app-a"]);
    listeners.pagehide.forEach((fn) => fn());
    expect(own.closeAll).toHaveBeenCalledTimes(1);
    expect(top.closeAll).not.toHaveBeenCalled();
  });

  it("uses a shell bridge without forApp as it is", () => {
    const top = fakeBridge();
    frameAt("/base-apps/run/app-a/proxy/web/", top.bridge);
    expect(pagesBridge()).toBe(top.bridge);
  });
});
