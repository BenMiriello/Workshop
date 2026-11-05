import { describe, expect, it } from "vitest";
import { clampBar, dragBar, parsePoint, SNAP_PX, STRETCH } from "./barSpot";

const size = { width: 300, height: 40 };
const view = { width: 1000, height: 800 };
const home = { left: 350, top: 744 };

describe("dragBar", () => {
  it("is held at its own spot near it, leaning a little toward the pointer", () => {
    const got = dragBar({ left: home.left + 20, top: home.top - 20 }, home, false, size, view);
    expect(got.pulled).toBe(true);
    expect(got.at).toEqual({ left: home.left + 20 * STRETCH, top: home.top - 20 * STRETCH });
    expect(dragBar(home, home, true, size, view).at).toEqual(home);
    expect(dragBar({ left: home.left + SNAP_PX, top: home.top }, home, false, size, view).pulled).toBe(true);
  });

  it("breaks away just past the distance it is pulled in at", () => {
    expect(dragBar({ left: home.left + SNAP_PX + 10, top: home.top }, home, true, size, view).pulled).toBe(false);
    expect(dragBar({ left: home.left + SNAP_PX + 2, top: home.top }, home, true, size, view).pulled).toBe(true);
    expect(dragBar({ left: home.left + SNAP_PX + 2, top: home.top }, home, false, size, view).pulled).toBe(false);
  });

  it("follows the pointer further away, kept inside the screen", () => {
    expect(dragBar({ left: 100, top: 100 }, home, false, size, view)).toEqual({ at: { left: 100, top: 100 }, pulled: false });
    expect(dragBar({ left: -50, top: 300 }, home, false, size, view).at).toEqual({ left: 8, top: 300 });
  });
});

describe("clampBar", () => {
  it("brings a bar back on screen after the window shrinks", () => {
    expect(clampBar({ left: 900, top: 10 }, size, { width: 600, height: 400 })).toEqual({ left: 292, top: 10 });
  });
});

describe("parsePoint", () => {
  it("reads a stored spot and survives junk", () => {
    expect(parsePoint('{"left":1,"top":2}')).toEqual({ left: 1, top: 2 });
    expect(parsePoint("{bad")).toBeNull();
    expect(parsePoint(null)).toBeNull();
    expect(parsePoint('{"left":"x","top":2}')).toBeNull();
  });
});
