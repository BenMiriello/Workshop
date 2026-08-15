import { describe, it, expect } from "vitest";
import { narrowClip, placeMenu, type Rect } from "./menuPlacement";

const rect = (left: number, top: number, w: number, h: number): Rect =>
  ({ left, top, right: left + w, bottom: top + h });

const VIEWPORT = rect(0, 0, 1440, 900);
const LIST = { width: 240, height: 200 };
const GAP = 4;

// The chat's real geometry: a 1200px column centred in a 1440px viewport, whose
// log clips horizontally. That is the case the viewport-only rule got wrong.
const CHAT_LOG = rect(120, 60, 1200, 780);

describe("placeMenu", () => {
  it("opens left-anchored and downward when there is room", () => {
    expect(placeMenu(rect(100, 100, 20, 20), LIST, VIEWPORT, "bottom", GAP))
      .toEqual({ align: "start", side: "bottom" });
  });

  it("flips to right-anchored at the viewport edge", () => {
    expect(placeMenu(rect(1400, 100, 20, 20), LIST, VIEWPORT, "bottom", GAP).align).toBe("end");
  });

  describe("the clipping ancestor, not just the viewport", () => {
    it("flips at the CLIP edge while the viewport still has room", () => {
      // A control near the right edge of the chat column. 1130 + 240 = 1370,
      // well inside the 1440 viewport, and well past the log's right edge of
      // 1320. Measured against the viewport this does not overflow, which is
      // precisely why the menu was being sliced off.
      const trigger = rect(1130, 400, 20, 20);
      expect(placeMenu(trigger, LIST, VIEWPORT, "bottom", GAP).align).toBe("start");
      expect(placeMenu(trigger, LIST, CHAT_LOG, "bottom", GAP).align).toBe("end");
    });

    it("stays left-anchored on a narrow bubble near the left of the log", () => {
      // Flipping here would push the list off the LEFT edge instead, which is
      // the same bug mirrored.
      expect(placeMenu(rect(200, 400, 20, 20), LIST, CHAT_LOG, "bottom", GAP).align).toBe("start");
    });

    it("does not flip when the other side has no room either", () => {
      // A clip box narrower than the list: neither placement fits, so it keeps
      // the default and scrolls rather than thrashing.
      const narrow = rect(0, 0, 200, 780);
      expect(placeMenu(rect(150, 400, 20, 20), LIST, narrow, "bottom", GAP).align).toBe("start");
    });
  });

  describe("vertical", () => {
    it("flips a 'top' menu down when the trigger is near the top of the clip box", () => {
      // The first message in a scrolled-to-top chat: the branch control asks to
      // open upward and there is nothing above it.
      expect(placeMenu(rect(600, 80, 20, 20), LIST, CHAT_LOG, "top", GAP).side).toBe("bottom");
    });

    it("keeps 'top' when there is room above", () => {
      expect(placeMenu(rect(600, 500, 20, 20), LIST, CHAT_LOG, "top", GAP).side).toBe("top");
    });

    it("flips a 'bottom' menu up near the bottom of the clip box", () => {
      expect(placeMenu(rect(600, 800, 20, 20), LIST, CHAT_LOG, "bottom", GAP).side).toBe("top");
    });

    it("keeps the preference when neither side fits", () => {
      // A clip box shorter than the list: neither above nor below fits.
      const short = rect(0, 0, 1440, 220);
      expect(placeMenu(rect(600, 100, 20, 20), LIST, short, "bottom", GAP).side).toBe("bottom");
      expect(placeMenu(rect(600, 100, 20, 20), LIST, short, "top", GAP).side).toBe("top");
    });
  });

  it("is idempotent: the projection does not read back its own result", () => {
    // The latent bug in the old measure. Whatever it answered, asking again with
    // the same geometry must answer the same, or a resize could never undo a
    // flip.
    const trigger = rect(1130, 400, 20, 20);
    const first = placeMenu(trigger, LIST, CHAT_LOG, "bottom", GAP);
    expect(placeMenu(trigger, LIST, CHAT_LOG, "bottom", GAP)).toEqual(first);
  });
});

// The other half of the rule, shared by Menu and Popover through
// useAnchoredPlacement: reducing the ancestors that clip into the one box the
// surface must stay inside. The DOM walk that produces these clippers is not
// testable here; what it produces them FOR is.
describe("narrowClip", () => {
  it("returns the viewport untouched when nothing clips", () => {
    expect(narrowClip(VIEWPORT, [])).toEqual(VIEWPORT);
  });

  it("narrows only the axes that actually clip", () => {
    // The chat's real geometry: a log that is overflow-x: hidden and visible on
    // y. Narrowing y here would invent a bottom edge that does not exist and
    // flip menus upward in the middle of a tall page.
    const log = rect(120, 60, 1200, 780);
    expect(narrowClip(VIEWPORT, [{ rect: log, clipsX: true, clipsY: false }]))
      .toEqual({ left: 120, top: 0, right: 1320, bottom: 900 });
  });

  it("takes the tightest edge across several clipping ancestors", () => {
    const outer = rect(100, 50, 1200, 800);
    const inner = rect(200, 40, 400, 900);
    expect(narrowClip(VIEWPORT, [
      { rect: outer, clipsX: true, clipsY: true },
      { rect: inner, clipsX: true, clipsY: true },
    ])).toEqual({ left: 200, top: 50, right: 600, bottom: 850 });
  });

  it("never widens past the box it started from", () => {
    // A scroller taller and wider than the viewport must not hand back edges
    // outside it: the surface still has to stay on screen.
    const huge = rect(-500, -500, 4000, 4000);
    expect(narrowClip(VIEWPORT, [{ rect: huge, clipsX: true, clipsY: true }]))
      .toEqual(VIEWPORT);
  });

  it("feeds placeMenu the edge that makes a menu flip", () => {
    // The two halves together, which is what Menu and Popover both run: the
    // trigger sits well inside the viewport and well past the log's right edge.
    const log = rect(120, 60, 1200, 780);
    const trigger = rect(1130, 400, 20, 20);
    const clip = narrowClip(VIEWPORT, [{ rect: log, clipsX: true, clipsY: false }]);
    expect(placeMenu(trigger, LIST, VIEWPORT, "bottom", GAP).align).toBe("start");
    expect(placeMenu(trigger, LIST, clip, "bottom", GAP).align).toBe("end");
  });
});
