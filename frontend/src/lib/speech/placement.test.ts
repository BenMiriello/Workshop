import { describe, expect, it } from "vitest";
import { belowSpot, cornerSpot, freeSpot, rowSpot } from "./placement";

const view = { left: 0, top: 0, width: 400, height: 800 };

describe("cornerSpot", () => {
  it("sits inside the bottom-right corner of a tall field", () => {
    expect(cornerSpot({ left: 10, top: 100, right: 310, bottom: 300 }, 24, view)).toEqual({ left: 282, top: 272 });
  });

  it("centres on a field shorter than the chip plus its inset", () => {
    expect(cornerSpot({ left: 10, top: 100, right: 310, bottom: 128 }, 24, view)).toEqual({ left: 282, top: 102 });
  });

  it("disappears when the corner is off screen", () => {
    expect(cornerSpot({ left: 10, top: 900, right: 310, bottom: 1000 }, 24, view)).toBeNull();
  });
});

describe("freeSpot", () => {
  const field = { left: 10, top: 100, right: 310, bottom: 140 };
  const none = () => false;

  it("takes the corner when nothing is there", () => {
    expect(freeSpot(field, 24, view, none)).toEqual(cornerSpot(field, 24, view));
  });

  it("moves left of a control in the corner", () => {
    const button = { left: 270, top: 100, right: 310, bottom: 140 };
    const overlaps = (c: { left: number; right: number; top: number; bottom: number }) =>
      c.left < button.right && c.right > button.left && c.top < button.bottom && c.bottom > button.top;
    const spot = freeSpot(field, 24, view, overlaps)!;
    expect(spot.left + 24).toBeLessThanOrEqual(270);
    expect(spot.top).toBe(cornerSpot(field, 24, view)!.top);
  });

  it("tries the top edge of a tall field before leaving it", () => {
    const tall = { left: 10, top: 100, right: 310, bottom: 300 };
    const bottomTaken = (c: { top: number }) => c.top > 200;
    expect(freeSpot(tall, 24, view, bottomTaken)).toEqual({ left: 282, top: 104 });
  });

  it("goes just outside when the whole field is taken", () => {
    const inside = (c: { left: number }) => c.left < 310;
    expect(freeSpot(field, 24, view, inside)).toEqual({ left: 314, top: 112 });
  });

  it("disappears when the field is off screen", () => {
    expect(freeSpot({ left: 10, top: 900, right: 310, bottom: 940 }, 24, view, none)).toBeNull();
  });
});

describe("belowSpot", () => {
  it("centres under the selection", () => {
    expect(belowSpot({ left: 100, top: 200, right: 300, bottom: 220 }, 100, 28, view)).toEqual({ left: 150, top: 232 });
  });

  it("is held inside the viewport at the edges", () => {
    expect(belowSpot({ left: 0, top: 200, right: 20, bottom: 220 }, 100, 28, view).left).toBe(8);
    expect(belowSpot({ left: 380, top: 200, right: 400, bottom: 220 }, 100, 28, view).left).toBe(292);
  });

  it("goes above when there is no room below", () => {
    expect(belowSpot({ left: 100, top: 760, right: 300, bottom: 780 }, 100, 28, view).top).toBe(724);
  });
});

describe("rowSpot", () => {
  const view = { left: 0, top: 0, width: 1000, height: 800 };
  const field = { left: 100, top: 500, right: 600, bottom: 580 };
  const end = { x: 300, rowCenter: 520, lineHeight: 20, innerRight: 600 };
  const free = () => false;

  it("sits on the last line, at its right end, when the line's text stops short of it", () => {
    expect(rowSpot(field, end, 24, view, free)).toEqual({ left: 572, top: 508 });
  });

  it("takes the empty row below when the line's text reaches it", () => {
    expect(rowSpot(field, { ...end, x: 590 }, 24, view, free)).toEqual({ left: 572, top: 528 });
  });

  it("goes just outside the field when there is no row below", () => {
    expect(rowSpot(field, { ...end, x: 590, rowCenter: 560 }, 24, view, free)).toEqual({ left: 604, top: 548 });
  });

  it("never covers the line's text: no chip when nothing else is free", () => {
    const narrow = { left: 0, top: 0, width: 600, height: 800 };
    expect(rowSpot(field, { ...end, x: 590, rowCenter: 560 }, 24, narrow, free)).toBeNull();
  });

  it("does not step left onto the text past a control at the line's end", () => {
    const search = { left: 70, top: 106, right: 155, bottom: 136 };
    const tail = { x: 200, rowCenter: 121, lineHeight: 20, innerRight: 155 };
    const button = (c: { right: number }) => c.right > 150;
    expect(rowSpot(search, tail, 32, view, button)).toBeNull();
  });

  it("keeps clear of a placeholder when there is room, and covers its end when there is not", () => {
    const search = { left: 70, top: 106, right: 155, bottom: 136 };
    const hint = { x: 200, rowCenter: 121, lineHeight: 20, innerRight: 155, hint: true };
    const button = (c: { right: number }) => c.right > 150;
    expect(rowSpot(search, hint, 32, view, button)).toEqual({ left: 117, top: 105 });
    expect(rowSpot(search, { ...hint, x: 90 }, 32, view, button)).toEqual({ left: 117, top: 105 });
  });

  it("sits at the end of the caret's line when the last line is out of sight", () => {
    const below = { ...end, x: 120, rowCenter: 900, caret: { x: 250, rowCenter: 540 } };
    expect(rowSpot(field, below, 24, view, free)).toEqual({ left: 572, top: 528 });
    const full = { ...below, caret: { x: 590, rowCenter: 540 } };
    expect(rowSpot(field, full, 24, view, free)).toEqual({ left: 604, top: 528 });
  });

  it("takes a nearby line with room when the caret's line is full and nothing is free outside", () => {
    const narrow = { left: 0, top: 0, width: 600, height: 800 };
    const below = { ...end, x: 120, rowCenter: 900, caret: { x: 590, rowCenter: 540 },
      near: [{ x: 595, rowCenter: 520 }, { x: 200, rowCenter: 560 }] };
    expect(rowSpot(field, below, 24, narrow, free)).toEqual({ left: 572, top: 548 });
  });

  it("does not sit on another line when the last one is scrolled out of sight", () => {
    const below = { ...end, x: 120, rowCenter: 900 };
    expect(rowSpot(field, below, 24, view, free)).toEqual({ left: 604, top: 555 });
    const narrow = { left: 0, top: 0, width: 600, height: 800 };
    expect(rowSpot(field, below, 24, narrow, free)).toBeNull();
  });

  it("stays left of a scrollbar", () => {
    expect(rowSpot(field, { ...end, innerRight: 585 }, 24, view, free)?.left).toBe(557);
  });

  it("steps left past the page's own control on that row", () => {
    const expand = (c: { left: number }) => c.left > 540;
    expect(rowSpot(field, end, 24, view, expand)?.left).toBeLessThanOrEqual(540);
  });

  it("centres on a field shorter than the chip", () => {
    const short = { left: 100, top: 500, right: 600, bottom: 520 };
    expect(rowSpot(short, { ...end, rowCenter: 510 }, 32, view, free)?.top).toBe(494);
  });
});
