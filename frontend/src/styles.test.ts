import { describe, it, expect } from "vitest";
// Read as TEXT. Both vitest configs stub `.css` imports to an empty string,
// even with `?raw`, and an empty string makes a `not.toContain` assertion pass
// vacuously. See src/node-shims.d.ts for why the imports are declared locally.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const css = read("./styles.css");
const kit = read("../../packages/ui/src/styles/components.css");

// The other half of the shell guarantee. `packages/ui` pins the shell to one
// viewport and clips it; this pins the DOCUMENT so it cannot scroll underneath
// that shell. Both are needed: a body free to scroll carries a correctly sized
// shell off the top of the screen, which is what happened on 2026-08-12.
describe("the document itself never scrolls", () => {
  it("fixes html and body to the viewport with no overflow", () => {
    const at = css.indexOf("html, body {");
    expect(at, "no combined html, body rule").toBeGreaterThan(-1);
    const rule = css.slice(at, css.indexOf("}", at));
    expect(rule).toContain("height: 100%");
    expect(rule).toContain("overflow: hidden");
    // Kills the iOS rubber-band that shows the page edge under the shell.
    expect(rule).toContain("overscroll-behavior: none");
  });

  it("does not leave body free to grow past the viewport", () => {
    // `min-height: 100vh` on a scrollable body is what let the document grow.
    const bodyAt = css.indexOf("\nbody {");
    const rule = css.slice(bodyAt, css.indexOf("}", bodyAt));
    expect(rule).not.toContain("min-height: 100vh");
  });
});

// The shell's guarantee, executable, and it lives HERE rather than in
// packages/ui because the kit's own vitest run stubs CSS imports to empty and
// the assertions passed vacuously there.
//
// On 2026-08-12 the toolbar scrolled off the top of the screen. `.ws-root` and
// `.ws-app` are the same element (App.tsx), and the box-sizing reset read
// `.ws-root *` (descendants only), so the shell itself was content-box. Its
// `height: 100dvh` plus `padding-top: env(safe-area-inset-top)` then resolved
// TALLER than the viewport wherever the notch inset is non-zero: the document
// scrolled and carried the chrome with it. It reproduced on phones and installed
// PWAs only, so it read as a mobile layout bug rather than a broken invariant.
//
// None of that is visible in a diff and there is no render harness, so the rules
// that make it impossible are pinned as text.

/** The body of a single rule in the kit sheet, by exact selector. */
function kitRule(selector: string): string {
  const at = kit.indexOf(`\n${selector} {`);
  expect(at, `no rule for ${selector}`).toBeGreaterThan(-1);
  return kit.slice(at, kit.indexOf("}", at));
}

describe("the app shell cannot scroll its own chrome away", () => {
  it("reads the real kit stylesheet", () => {
    // Guards the guard: an empty import would make every assertion below pass.
    expect(kit.length).toBeGreaterThan(1000);
    expect(kit).toContain(".ws-app {");
  });

  it("applies box-sizing to the root element itself, not only its descendants", () => {
    const reset = kit.slice(kit.indexOf(".ws-root,"), kit.indexOf("box-sizing: border-box;"));
    expect(reset).toContain(".ws-root,");
    expect(reset).toContain(".ws-root *,");
  });

  it("pins the shell to exactly one viewport and clips it", () => {
    const app = kitRule(".ws-app");
    expect(app).toContain("height: 100dvh");
    expect(app).toContain("max-height: 100dvh");
    expect(app).toContain("overflow: hidden");
  });

  it("keeps the top nav out of the flow that scrolls", () => {
    expect(kitRule(".ws-topnav")).toContain("flex: 0 0 auto");
  });

  it("leaves main as the only scroller", () => {
    const main = kitRule(".ws-app .ws-app__body > main");
    expect(main).toContain("overflow-y: auto");
    expect(main).toContain("min-height: 0");
  });
});

describe("a list row never scrolls sideways", () => {
  // The table it replaced set `min-width: 520px` inside a horizontal scroller,
  // which on a phone is a narrow window onto a wide grid.
  it("gives list rows no minimum width", () => {
    expect(kitRule(".ws-listrow")).toContain("min-width: 0");
  });

  it("clips the title and the meta line rather than wrapping them", () => {
    expect(kitRule(".ws-listrow__title")).toContain("white-space: nowrap");
    const meta = kitRule(".ws-listrow__meta");
    expect(meta).toContain("flex-wrap: nowrap");
    expect(meta).toContain("overflow: hidden");
  });

  it("carries no board or card rules for the surfaces that were deleted", () => {
    expect(kit).not.toContain(".ws-board");
    expect(kit).not.toContain(".ws-ticket-card");
    expect(kit).not.toContain(".ws-ticket-grid");
  });
});

// A record page (one ticket, one run, one store) is not a list page, and the
// three ways that went wrong are all invisible in a diff.
describe("a detail page reads as a record, not as a banner", () => {
  it("sets an entity title at reading size, not the page-name size", () => {
    const title = kitRule(".ws-page-header--entity .ws-page-header__title");
    expect(title).toContain("font-size: var(--fs-lg)");
    // --fs-xl is 24px: right for "Tickets", a banner for one ticket's sentence.
    expect(title).not.toContain("--fs-xl");
  });

  it("keeps the way out of the page quieter than the page", () => {
    // A router Link takes the MINE hue by default and a bare icon inherits the
    // h1's 24px. Back is chrome, so it is muted with its own icon size.
    const back = kitRule(".ws-page-header__back a");
    expect(back).toContain("color: var(--muted)");
    expect(kit).toContain(".ws-page-header__back svg { width: 14px");
  });

  it("puts the properties rail FIRST in the DOM and moves it right with grid", () => {
    // This is the whole reason the narrow layout is correct: source order is
    // rail, then main, so a phone shows properties under the header instead of
    // below the comments. Only the wide breakpoint reorders.
    const at = kit.indexOf(".ws-detail { display: grid");
    expect(at, "no .ws-detail rule").toBeGreaterThan(-1);
    const wide = kit.slice(kit.indexOf("@media (min-width: 900px)", at));
    expect(wide.slice(0, wide.indexOf("\n}\n"))).toContain(".ws-detail__rail { grid-column: 2; grid-row: 1; }");
  });

  it("labels a read-only value in muted ink, not in form-label ink", () => {
    // `Field`'s label is --fg at medium weight, which is what made every rail
    // look like a disabled form.
    const label = kitRule(".ws-props__label");
    expect(label).toContain("color: var(--muted)");
    expect(label).not.toContain("--fw-medium");
  });
});
