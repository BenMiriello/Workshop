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

  it("keeps every piece of chrome out of the flow that scrolls", () => {
    // The top toolbar is gone; the dock, the page column and the phone bars
    // are what has to stay put now. Each of them is `flex: 0 0 auto` on its own
    // axis, which is what makes <main> the only scroller (asserted below).
    expect(kitRule(".ws-rail")).toContain("flex: 0 0 var(--rail-w)");
    expect(kitRule(".ws-pagecol")).toContain("flex: 0 0 auto");
    expect(kitRule(".ws-bar1__wrap")).toContain("flex: 0 0 auto");
    expect(kitRule(".ws-trail")).toContain("flex: 0 0 auto");
  });

  it("collapses the page column by clipping AND sliding, not by hiding it", () => {
    // Two movements at once is what makes the column slide out from under the
    // dock with content arriving behind it. Either one alone is a different,
    // wrong animation, and both have been built by mistake before.
    expect(kitRule(".ws-pagecol:not(.is-open)")).toContain("width: 0");
    expect(kitRule(".ws-pagecol:not(.is-open) .ws-pagecol__inner")).toContain("translateX(-100%)");
  });

  it("only transitions the column when it was told to", () => {
    // Switching place hard cuts. If the transition were on `.ws-pagecol`
    // unconditionally, every navigation between places would animate, which is
    // the exact thing the design forbids.
    expect(kit).toContain(".ws-pagecol.is-animating { transition:");
    expect(kitRule(".ws-pagecol")).not.toContain("transition:");
  });

  it("puts the mobile page toolbar underline on the bar, and raises nothing", () => {
    const tab = kitRule(".ws-tabbar__tab");
    expect(tab).toContain("border-bottom: 2px solid transparent");
    expect(tab).not.toContain("transform");
    expect(kitRule(".ws-tabbar__tab.is-active")).toContain("border-bottom-color: var(--accent)");
  });

  it("keeps every page-toolbar label on one baseline", () => {
    // The parent stretches and the tab centres, so the tab is the bar's full
    // height and its underline lands on the BAR's bottom edge while the label
    // stays where every other label is. `flex-end` gives the same underline and
    // rides the labels low; it was that once.
    expect(kitRule(".ws-tabbar")).toContain("align-items: stretch");
    expect(kitRule(".ws-tabbar__tab")).toContain("align-items: center");
    expect(kitRule(".ws-tabbar__tab")).toContain("padding-bottom: 2px");
  });

  it("changes nothing but colour when a page tab is selected", () => {
    // Design doc section 11 lists raising the selected label as REJECTED, so
    // the selected rule may only touch ink, weight and the underline.
    const active = kitRule(".ws-tabbar__tab.is-active");
    for (const raised of ["background", "box-shadow", "border-radius", "transform", "margin"]) {
      expect(active, `selected tab must not set ${raised}`).not.toContain(raised);
    }
    expect(kitRule(".ws-tabbar__tab")).toContain("transition: color");
  });

  it("gives the page toolbar a stronger hairline than bar one", () => {
    // So the two bars read as one block separated from the content, rather than
    // as two stacked strips.
    expect(kitRule(".ws-bar1")).toContain("border-bottom: 1px solid var(--border)");
    expect(kitRule(".ws-tabbar")).toContain("border-bottom: 1px solid var(--border-strong)");
  });
});

// ONE SCRIM IN THE WHOLE SYSTEM.
//
// Reported 2026-08-23: "why do we have multiple randomly implemented poorly
// implemented different overlay backdrops". There were four (the kit's
// `.ws-overlay`, the chat work sheet's, the place menu's, the birds More
// sheet's), they differed in blur, in z-index and in which part of the screen
// they covered, and one of them dimmed only the chat column. They are one rule
// now, and this is what stops a fifth being written: a dimming surface is
// `--scrim` on a full-bleed box, so the token has exactly one consumer.
describe("there is one overlay backdrop, not four", () => {
  it("spends --scrim in the kit's overlay rule and nowhere else", () => {
    const uses = (text: string) => text.split("var(--scrim)").length - 1;
    // The legacy app sheet must own no scrim of its own.
    expect(uses(css), "frontend/src/styles.css paints its own scrim").toBe(0);
    expect(uses(kit), "the kit has more than one scrim rule").toBe(1);
    expect(kitRule(".ws-overlay")).toContain("background: var(--scrim)");
  });

  it("blurs, because the operator picked the blurred one", () => {
    expect(kitRule(".ws-overlay")).toContain("backdrop-filter: blur(2px)");
  });

  it("keeps the padding deleted on the modifiers that delete it", () => {
    // Both --bare and --sheet set `padding: 0`, and the phone media query
    // re-applied it to every `.ws-overlay` at equal specificity from further
    // down the file. A bare scrim has nothing to frame and a bottom sheet inset
    // from the edges is not a bottom sheet.
    const at = kit.indexOf("@media (max-width: 560px)");
    expect(at).toBeGreaterThan(-1);
    const block = kit.slice(at, at + 600);
    expect(block).not.toContain("\n  .ws-overlay { padding");
    expect(block).toContain(".ws-overlay:not(.ws-overlay--bare):not(.ws-overlay--sheet)");
  });
});

// The phone's place menu. Geometry:
// docs/platform-redesign/shell-mockup-spec-2026-08-22.md section 3.
describe("the place menu is a dropdown, not a drawer", () => {
  it("never lets the scrim cover bar one", () => {
    // The tile that opened the menu stays visible and stays live. Everything
    // below the bar's hairline is covered; the bar itself never is.
    //
    // The menu has no scrim rule of its own any more: it composes the kit's
    // ONE dimming surface plus the modifier that carries this geometry. See
    // PlaceMenu.tsx for the composition and the overlay block for why there is
    // only one.
    const base = kitRule(".ws-overlay");
    expect(base).toContain("position: fixed");
    expect(base).toContain("inset: 0");
    expect(kitRule(".ws-overlay--under-bar1"))
      .toContain("top: calc(env(safe-area-inset-top) + var(--bar1-h))");
    // And it stays UNDER the panel it belongs to, rather than over it.
    expect(kitRule(".ws-overlay--dropdown")).toContain("z-index: var(--z-dropdown)");
    expect(kit).not.toContain(".ws-placemenu__scrim");
  });

  it("anchors the panel under the tile at the left edge", () => {
    const panel = kitRule(".ws-placemenu");
    expect(panel).toContain("position: fixed");
    expect(panel).toContain("left: var(--space-2)");
    expect(panel).toContain("width: var(--placemenu-w)");
    // Capped at 520px, and at the room below the bar when that is less.
    expect(panel).toContain("max-height: min(");
    expect(panel).toContain("var(--placemenu-max-h)");
    expect(panel).toContain("overflow-y: auto");
  });

  it("rises and fades rather than sliding in from an edge", () => {
    // An earlier left slide-over was replaced during design and a dead
    // `wsDrawerIn` keyframe survives in the mockup. A translateX here would be
    // that drawer coming back.
    expect(kitRule(".ws-placemenu")).toContain("animation: ws-rise");
    const rise = kit.slice(kit.indexOf("@keyframes ws-rise"));
    const frames = rise.slice(0, rise.indexOf("}") + 2);
    expect(frames).toContain("translateY(10px)");
    expect(frames).not.toContain("translateX");
  });

  it("spends no raw colour on the panel, which the dark-only mockup does", () => {
    const panel = kitRule(".ws-placemenu") + kitRule(".ws-overlay");
    expect(panel).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(panel).not.toContain("rgba(");
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

  // This used to assert `.ws-board` was absent too. That is REVERSED, on
  // operator direction (2026-08-14 redesign), and the reversal is narrow:
  //
  // The board was deleted in August as "a surface you drag work across, which
  // nothing here does". The cards still do not drag — status is changed on the
  // ticket, where the consequences are — so the original reasoning is intact
  // for the thing it was actually about. What came back is the READ: six lanes
  // answering "what is stuck, what is waiting, what is in flight" at a glance,
  // which one date-sorted column cannot do at any length.
  //
  // The other two stay banned, because nothing overruled them: `.ws-ticket-card`
  // was the four-band card and `.ws-ticket-grid` the multi-column list, and the
  // redesign reintroduces neither.
  it("carries no card or grid rules for the surfaces that were deleted", () => {
    expect(kit).not.toContain(".ws-ticket-card");
    expect(kit).not.toContain(".ws-ticket-grid");
  });

  // The board is back, so the reason it is not the old one gets pinned instead
  // of assumed: a lane is a fixed-width column in a scroller, and a card is a
  // link with no drag affordance anywhere near it.
  it("draws a board of fixed lanes, with no drag surface", () => {
    expect(kitRule(".ws-board")).toContain("overflow-x: auto");
    expect(kitRule(".ws-board__col")).toContain("flex: 0 0 var(--ws-lane)");
    expect(kit).not.toContain("draggable");
    expect(kit).not.toContain("ws-board__drop");
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

// A control that paints part of itself with a background LAYER cannot have its
// states written with the `background` shorthand. Reported 2026-08-14: "any
// time there's a dropdown it'll transform to the left and then back to the
// right in a really weird way and it'll disappear."
//
// `.ws-select` draws its chevron with background-image + background-position.
// The shorthand resets every longhand it omits, so `:hover { background: X }`
// meant `background-image: none; background-position: 0% 0%`, and naming the
// shorthand as the transition-property expanded it to every background
// longhand, so the reset ANIMATED: the caret slid left, disappeared halfway
// (a discrete type flips at 50%), and slid back on un-hover. `:focus` did it
// too, so the caret was gone the entire time a select was open.
//
// Not visible in a diff and not catchable by eye in review, so it is pinned.
describe("a select's caret cannot be animated away by a background shorthand", () => {
  // Every rule in the kit sheet whose selector list mentions .ws-select.
  const selectRules = kit
    .split("}")
    .map((chunk) => {
      const brace = chunk.indexOf("{");
      return brace === -1
        ? null
        : { sel: chunk.slice(0, brace), body: chunk.slice(brace + 1) };
    })
    // The whole class token, not a substring: `.ws-selectpill` is a different
    // control that happens to start with the same eleven characters, and it is
    // allowed its own `background` shorthand because it paints no layer.
    .filter((r): r is { sel: string; body: string } =>
      !!r && /\.ws-select(?![\w-])/.test(r.sel));

  it("finds the select rules it is meant to be guarding", () => {
    expect(selectRules.length).toBeGreaterThanOrEqual(4);
  });

  it("never sets the background shorthand on a select", () => {
    for (const r of selectRules) {
      // `background-color`, `background-image` and friends are longhands and
      // are fine; a bare `background:` is the one that resets the caret.
      expect(
        /(^|[;\s])background\s*:/.test(r.body),
        `background shorthand in a select rule: ${r.sel.trim()}`,
      ).toBe(false);
    }
  });

  it("never transitions the background shorthand on a select", () => {
    for (const r of selectRules) {
      const m = r.body.match(/transition\s*:([^;]*)/);
      if (!m) continue;
      // "background 120ms" expands to every background longhand, including
      // position and image. "background-color 120ms" does not.
      expect(
        /(^|,)\s*background\s+/.test(m[1]),
        `bare background in a transition: ${r.sel.trim()}`,
      ).toBe(false);
    }
  });

  it("still paints a caret, at the right edge", () => {
    const rule = kitRule(".ws-select");
    expect(rule).toContain("appearance: none");
    expect(rule).toContain("background-image: url(");
    expect(rule).toContain("background-position: right var(--space-2) center");
  });
});

// A bar pinned to the bottom of the page has to know the scroller's bottom
// padding, and there is exactly one place that number is allowed to live.
//
// `position: sticky; bottom: 0` measures from the scrollport's CONTENT edge,
// not its border edge. <main> is the scroller and it pads its bottom, so a bar
// that does not subtract that padding hangs that far above the real bottom and
// the list scrolls visibly through the gap underneath it. Reported 2026-08-14:
// "it floats above the bottom and you can see the list scroll down below it."
//
// The padding is also NOT one value: the mobile block narrows it and adds the
// home-indicator inset. So the fix cannot be a number written at the call site,
// and this pins the token instead.
describe("a bottom-pinned bar reaches the bottom", () => {
  it("keeps the scroller's bottom padding in a token, not a literal", () => {
    const main = kitRule(".ws-app .ws-app__body > main");
    expect(main).toContain("var(--page-pad-b)");
    // The literal it replaced. If this comes back, the bar and the scroller
    // have two different ideas of where the bottom is.
    expect(main).not.toContain("/ 2) var(--space-8)");
  });

  it("re-tokenises the same padding on mobile rather than restating it", () => {
    // The mobile block is where the number differs (narrower, plus the
    // home-indicator inset), which is exactly where a second copy would rot.
    const at = kit.indexOf("--page-pad-b: calc(");
    expect(at, "mobile block does not set --page-pad-b").toBeGreaterThan(-1);
    expect(kit.slice(at, at + 120)).toContain("env(safe-area-inset-bottom)");
    // <main>'s mobile rule must no longer carry its own bottom padding.
    const mob = kit.slice(kit.indexOf(".ws-app .ws-app__body > main { padding-top"));
    expect(mob.slice(0, 120)).not.toContain("padding-bottom");
  });

  // The sticky bar is GONE, and the fix that compensated for the scroller's
  // padding with a negative page margin went with it. Both were guesses at
  // where a scrollport's bottom is; the bar now renders into the shell's
  // overlay host, where `bottom: 0` is the bottom of the content region by
  // construction. What is pinned is that it stays out of the scroller.
  it("puts a page's bottom bar outside the scroller, not sticky inside it", () => {
    const bar = kitRule(".ws-pagebar");
    expect(bar).toContain("position: absolute");
    expect(bar).toContain("bottom: 0");
    expect(bar).not.toContain("position: sticky");
    // The page reserves room for it, because a bar outside the scroller cannot
    // lengthen the page itself.
    expect(kitRule(".ws-tickets--footed")).toContain("var(--ws-pagebar-h)");
    // The compensating negative margin must not come back.
    expect(kit).not.toContain("margin-bottom: calc(var(--page-pad-b) * -1)");
  });
});

// A NAME CANNOT PUSH THE ROW IT SITS IN OFF THE SCREEN.
//
// The chat's name is the only unbounded string in the toolbar: everything else
// is a fixed-size icon or a label that already truncates. So it is the one
// element that can overflow the bar, and on 2026-08-15 it did — with
// `flex-wrap: nowrap` on a phone, a long name carried the actions off the right
// edge, where they cannot be reached at all.
//
// The rule was there and it was on the wrong element. `.chat-tb__titlemenu` was
// given `flex: 0 1 auto` in the mobile block, but the toolbar's actual flex item
// is `.chat-tb__namegroup` (the name and the pencil, clustered), which kept
// `flex: 0 0 auto` at every width. A parent that will not shrink makes a
// shrinkable child unreachable, so the declaration that looked like the fix
// could never fire.
//
// Both are asserted, because the defect was the GAP between them: an unbroken
// chain from the flex container to the text, or the name overflows again.
describe("the chat toolbar cannot be pushed sideways by a long name", () => {
  /** The body of a single rule in the app sheet, by exact selector. */
  const appRule = (selector: string): string => {
    const at = css.indexOf(`\n${selector} {`);
    expect(at, `no rule for ${selector}`).toBeGreaterThan(-1);
    return css.slice(at, css.indexOf("}", at));
  };

  it("lets the name cluster give up room, since it is the toolbar's flex item", () => {
    const group = appRule(".chat-tb__namegroup");
    expect(group).toContain("flex: 0 1 auto");
    // Without this a flex item cannot shrink below its content, and the shrink
    // factor above is decorative.
    expect(group).toContain("min-width: 0");
  });

  it("lets the menu wrapper inside it give up room too", () => {
    const menu = appRule(".chat-tb__titlemenu");
    expect(menu).toContain("flex: 0 1 auto");
    expect(menu).toContain("min-width: 0");
  });

  it("truncates the name rather than letting it set the width", () => {
    const title = appRule(".chat-tb__title");
    expect(title).toContain("min-width: 0");
    expect(title).toContain("overflow: hidden");
    expect(title).toContain("text-overflow: ellipsis");
    expect(title).toContain("white-space: nowrap");
  });

  // `max-width: 100%` resolved against the TOOLBAR, so it allowed a name to fill
  // the whole bar and push every control out of it. It read as a floor and was
  // not one; the shrink chain is the floor.
  it("does not cap the name against the toolbar and call that a floor", () => {
    expect(appRule(".chat-tb__namegroup")).not.toContain("max-width: 100%");
    expect(appRule(".chat-tb__titlemenu")).not.toContain("max-width: 100%");
  });

  // The two viewports differ by whether the row may take a second line, and
  // nothing else. A shrink override scoped to one width is what hid the broken
  // chain last time: it looked like the narrow case was handled.
  it("carries the viewport difference in flex-wrap alone", () => {
    const at = css.indexOf("@media (max-width: 640px) {\n  /* Tighter vertically");
    expect(at, "no chat toolbar mobile block").toBeGreaterThan(-1);
    const block = css.slice(at, css.indexOf("\n}", at));
    expect(block).toContain("flex-wrap: nowrap");
    expect(block).not.toContain("flex: 0 1 auto");
  });

  // THE MOBILE BAR'S PADDING IS HALF A STEP, ON PURPOSE.
  //
  // It was --space-2, went to --space-1, and the operator asked for half way
  // back on 2026-08-16. That is 6px, and the 4px grid has no token there, so
  // this is one deliberate exception to "use the steps" and it is the kind that
  // a later tidy-up pass silently rounds back onto the grid. Expressed as the
  // midpoint of the two steps it sits between, and pinned as that, so the
  // reason survives the next reader.
  it("keeps the mobile toolbar's padding a half step, not a grid step", () => {
    const at = css.indexOf("@media (max-width: 640px) {\n  /* Tighter vertically");
    const block = css.slice(at, css.indexOf("\n}", at));
    expect(block).toContain("padding-block: calc((var(--space-1) + var(--space-2)) / 2)");
    // The two values it is deliberately BETWEEN, and the bare literal it must
    // not decay into. Matched as DECLARATIONS: the prose above it names all
    // three, so a substring test on the block passes or fails on the comment.
    expect(block).not.toContain("padding-block: var(--space-1)");
    expect(block).not.toContain("padding-block: var(--space-2)");
    expect(block).not.toContain("padding-block: 0.375rem");
  });
});

// ---- THE 16px FLOOR --------------------------------------------------------
//
// iOS Safari zooms the viewport in when you focus a text field computing under
// 16px, and every size in the Workshop type scale is under 16px. Reported
// 2026-08-22 on a phone: focusing any input, textarea or the code editor zoomed
// the page. There is no DOM harness here and computed styles cannot be
// asserted, so the rules that make it impossible are pinned as text, the same
// way the shell's invariants above are.
describe("no text-entry surface can zoom an iOS page on focus", () => {
  const tokens = read("../../packages/ui/src/styles/tokens.css");

  /** The body of the kit's `@media (pointer: coarse)` block that holds the floor. */
  const floorBlock = (): string => {
    const at = kit.indexOf("@media (pointer: coarse) {\n  .ws-root {\n    /* 1rem");
    expect(at, "no coarse-pointer 16px floor block in the kit sheet").toBeGreaterThan(-1);
    return kit.slice(at, kit.indexOf("\n}", at));
  };

  it("routes every editable control through the two field tokens", () => {
    // The indirection IS the fix: one override moves all of them. A control
    // that goes back to naming a scale size directly drops out of the floor.
    expect(tokens).toContain("--fs-field:      var(--fs-base);");
    expect(tokens).toContain("--fs-field-code: var(--fs-sm);");
    expect(kitRule(".ws-input, .ws-select, .ws-textarea")).toContain("font-size: var(--fs-field)");
    expect(kit).toContain("font-family: inherit; font-size: var(--fs-field); color: var(--fg);");
    expect(kitRule(".ws-stepper__input")).toContain("font-size: var(--fs-field)");
  });

  it("raises both tokens to at least 16px on a coarse pointer", () => {
    const block = floorBlock();
    expect(block).toContain("--fs-field: 1rem;");
    expect(block).toContain("--fs-field-code: 1rem;");
  });

  it("covers the surfaces that name a size of their own", () => {
    const block = floorBlock();
    // The phone ticket bar's tall search field (--fs-md, 15px).
    expect(block).toContain(".ws-searchfield--lg input { font-size: var(--fs-field); }");
    // An inline title editor inherits its heading's size, which is not
    // guaranteed to clear the floor. `1em` resolves against the parent.
    expect(block).toContain(".ws-titleedit__form .ws-input { font-size: max(1rem, 1em); }");
    // CodeMirror's own base theme sizes the search panel's field at 70% (~11px),
    // and the editor turns that panel on. It is the one editable surface the kit
    // does not author, so it is easy to miss.
    expect(block).toContain(".ws-editor .cm-textfield { font-size: var(--fs-field); }");
  });

  it("sizes the code editor from the token, on the element the gutter shares", () => {
    const editor = read("../../packages/ui/src/Editor.tsx");
    expect(editor).toContain('fontSize: "var(--fs-field-code)"');
    // .cm-content must NOT carry its own size: the gutter would stay behind.
    const content = editor.slice(editor.indexOf('".cm-content": {'));
    expect(content.slice(0, content.indexOf("}"))).not.toContain("fontSize");
  });

  it("is a pointer query, never a width breakpoint", () => {
    // An iPhone Pro Max in landscape is 932px wide: it is the device with the
    // problem and it is outside every breakpoint in this system.
    expect(floorBlock()).not.toContain("max-width");
  });

  it("leaves the fine-pointer scale and the viewport meta alone", () => {
    // Raising the scale would have been an app-wide typography change.
    expect(tokens).toContain("--fs-base: 0.875rem;");
    expect(tokens).toContain("--fs-sm:   0.8125rem;");
    // Killing pinch-zoom is a WCAG 1.4.4 failure and iOS ignores it anyway.
    const html = read("../index.html");
    expect(html).not.toContain("user-scalable");
    expect(html).not.toContain("maximum-scale");
  });
});
