import { describe, it, expect } from "vitest";
import { appWorkInFlight, type AppStatus, type Service } from "./appServices";

function svc(status: Service["status"]): Service {
  return {
    name: `svc-${status}`, status, pid: null, port: null, url: null,
    expose: false, healthy: null, error: null, restarts: 0,
  };
}

function status(phase: string, ...services: Service[]): AppStatus {
  return { slug: "s", phase, desired: "up", services, deps: [] };
}

describe("appWorkInFlight", () => {
  it("is false before anything has been read", () => {
    expect(appWorkInFlight(null)).toBe(false);
    expect(appWorkInFlight(undefined)).toBe(false);
  });

  it("is true while the app as a whole is coming up", () => {
    expect(appWorkInFlight(status("starting"))).toBe(true);
  });

  it("is true while any single service is building or starting", () => {
    expect(appWorkInFlight(status("up", svc("running"), svc("building")))).toBe(true);
    expect(appWorkInFlight(status("up", svc("running"), svc("starting")))).toBe(true);
  });

  it("is false once every service has settled, however it settled", () => {
    expect(appWorkInFlight(status("up", svc("running"), svc("stopped")))).toBe(false);
    expect(appWorkInFlight(status("down", svc("failed"), svc("crashlooping")))).toBe(false);
    expect(appWorkInFlight(status("up", svc("pending"), svc("disabled")))).toBe(false);
  });

  it("is false for a settled app with no services at all", () => {
    expect(appWorkInFlight(status("down"))).toBe(false);
  });
});
