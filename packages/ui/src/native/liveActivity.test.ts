import { afterEach, describe, expect, it, vi } from "vitest";
import { liveActivities } from "./liveActivity";
import type { WorkshopNative } from "./types";

function fakeBridge(name: string, scoped?: (app: string) => WorkshopNative) {
  return {
    has: (cap: string) => cap === "liveActivity",
    liveActivity: { name },
    forApp: scoped,
  } as unknown as WorkshopNative;
}

function frameAt(pathname: string, top: WorkshopNative) {
  const parent = { WorkshopNative: top } as unknown as Window;
  (parent as unknown as { parent: unknown }).parent = parent;
  vi.stubGlobal("window", { location: { pathname }, parent });
}

afterEach(() => vi.unstubAllGlobals());

describe("liveActivities", () => {
  it("is the API acting for the frame's app", () => {
    const apps: string[] = [];
    const own = fakeBridge("app-a's");
    const top = fakeBridge("unscoped", (app) => {
      apps.push(app);
      return own;
    });
    frameAt("/api/base-apps/run/app-a/proxy/web/", top);
    expect(liveActivities()).toBe(own.liveActivity);
    expect(apps).toEqual(["app-a"]);
  });

  it("is undefined outside an app's served pages", () => {
    frameAt("/apps/app-a/overview", fakeBridge("unscoped", () => fakeBridge("x")));
    expect(liveActivities()).toBeUndefined();
  });

  it("is undefined in a shell from before activities were per app", () => {
    frameAt("/base-apps/run/app-a/proxy/web/", fakeBridge("unscoped"));
    expect(liveActivities()).toBeUndefined();
  });

  it("is undefined in a plain browser", () => {
    const parent = {} as Window;
    (parent as unknown as { parent: unknown }).parent = parent;
    vi.stubGlobal("window", { location: { pathname: "/base-apps/run/a/proxy/w/" }, parent });
    expect(liveActivities()).toBeUndefined();
  });
});
