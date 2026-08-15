import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import { cx } from "./cx";
import { useOverlayHost } from "./OverlayHost";

// A page's primary action, pinned to the bottom of the shell's content region.
//
// NOT STICKY, and that is the whole point. A sticky bar lives inside <main>,
// which is the scroller, so where it lands depends on the scrollport's exact
// box, on the scroller's own padding, and on the bar's containing block. Two
// attempts at this on 2026-08-14 both left the bar hovering above the bottom
// with the list scrolling through the strip underneath, and the second one
// added a page-level negative margin to compensate, which is the shape of a
// fix that is guessing.
//
// The shell already answers this. `OverlayHost` renders a host element that
// fills exactly the content region, below the top nav, and `Modal` has portalled
// into it since it was added. `position: absolute; bottom: 0` inside that host
// is the bottom of the region BY CONSTRUCTION: no scrollport, no containing
// block to escape, nothing to compensate for.
//
// Outside a provider (tests, the springer entry) there is no host, so it renders
// inline at the end of the page rather than vanishing.
export function PageActionBar({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const host = useOverlayHost();
  const bar = (
    <div className={cx("ws-pagebar", className)}>
      <div className="ws-pagebar__inner">{children}</div>
    </div>
  );
  return host ? createPortal(bar, host) : bar;
}
