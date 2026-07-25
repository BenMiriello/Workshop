import { useEffect } from "react";

// Pin a full-height shell to the *visual* viewport, so a composer sits right
// above the mobile keyboard and the content above it stays put.
//
// The shell is position:fixed (see `.ws-app--chatfull`). On iOS the keyboard
// does NOT resize the layout viewport — it overlays, and the browser scrolls the
// page up to reveal the focused input, which would carry a static layout up and
// out of view. `visualViewport` gives the true visible window: its height shrinks
// for the keyboard and its offsetTop is how far the page scrolled under it. Both
// feed `--app-vh` / `--app-top`, which the fixed shell consumes.
//
// This lives in one place because every full-height chat surface needs it, not
// only the focused route: a surface without it is exactly where the "huge blank
// chin above the keyboard, app squeezed into the top" symptom comes from.
//
// Two iOS-specific details that a naive resize listener gets wrong:
//   * the browser commonly reports an intermediate height mid-keyboard-animation
//     and then never fires again, so a few settle passes are scheduled after the
//     last event rather than trusting a single measurement;
//   * `offsetTop` is clamped at 0 — Firefox iOS and Safari disagree about its
//     sign/semantics, and a negative value would push the shell off-screen.
export function useVisualViewport(active: boolean = true): void {
  useEffect(() => {
    if (!active) return;
    const vv = window.visualViewport;
    const root = document.documentElement.style;
    let timers: number[] = [];

    const set = () => {
      const h = vv ? vv.height : window.innerHeight;
      const top = vv ? Math.max(0, vv.offsetTop) : 0;
      root.setProperty("--app-vh", `${Math.round(h)}px`);
      root.setProperty("--app-top", `${Math.round(top)}px`);
    };
    // Re-measure after the keyboard animation settles, not just when the event
    // fires. Without this the shell keeps a mid-animation height and the gap it
    // leaves below is the blank chin.
    const settle = () => {
      set();
      timers.forEach(window.clearTimeout);
      timers = [120, 320, 600].map((ms) => window.setTimeout(set, ms));
    };

    settle();
    // Keep the page itself from scrolling behind the fixed shell. `body {
    // overflow: hidden }` alone does NOT stop iOS from scrolling the *layout*
    // viewport to reveal a focused input, and that scroll is what opens the
    // blank band below the shell. Pin both elements, and paint them with the app
    // background so any residual gap reads as the app rather than as a white
    // chin. The previous values are restored on cleanup.
    const body = document.body.style;
    const html = document.documentElement.style;
    const prev = {
      bodyOverflow: body.overflow, bodyPosition: body.position, bodyWidth: body.width,
      bodyHeight: body.height, bodyBg: body.background,
      htmlOverflow: html.overflow, htmlBg: html.background,
    };
    body.overflow = "hidden";
    body.position = "fixed";
    body.width = "100%";
    body.height = "100%";
    body.background = "var(--bg)";
    html.overflow = "hidden";
    html.background = "var(--bg)";
    vv?.addEventListener("resize", settle);
    vv?.addEventListener("scroll", set);
    window.addEventListener("resize", settle);
    window.addEventListener("orientationchange", settle);
    // Focusing the composer is what summons the keyboard; measure from there too
    // so the settle passes start before the first resize event arrives.
    window.addEventListener("focusin", settle);
    window.addEventListener("focusout", settle);
    return () => {
      timers.forEach(window.clearTimeout);
      vv?.removeEventListener("resize", settle);
      vv?.removeEventListener("scroll", set);
      window.removeEventListener("resize", settle);
      window.removeEventListener("orientationchange", settle);
      window.removeEventListener("focusin", settle);
      window.removeEventListener("focusout", settle);
      body.overflow = prev.bodyOverflow;
      body.position = prev.bodyPosition;
      body.width = prev.bodyWidth;
      body.height = prev.bodyHeight;
      body.background = prev.bodyBg;
      html.overflow = prev.htmlOverflow;
      html.background = prev.htmlBg;
      root.removeProperty("--app-vh");
      root.removeProperty("--app-top");
    };
  }, [active]);
}
