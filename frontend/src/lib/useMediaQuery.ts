import { useEffect, useState } from "react";

// Does the viewport match a media query, as state React can branch on.
//
// FOR LAYOUTS THAT ARE DIFFERENT TREES, not for layouts that are the same tree
// at a different size. CSS is still the right answer for the second kind and
// most of this app uses it: `.ws-listrow` is one grid that becomes two lines
// under 720px with no second implementation to keep in sync.
//
// The tickets board is the first case. A board and a phone list are not one
// component at two widths — the board is a horizontal scroller of lanes and the
// list is a vertical scroll of grouped rows — so rendering both and hiding one
// in CSS would mount two copies of every row, double every key handler, and put
// the hidden one in the tab order.
//
// SSR-safe and matches on the first paint rather than after it: the initial
// state reads matchMedia during render, so a phone never draws the desktop tree
// for a frame and then swaps.
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" && !!window.matchMedia?.(query).matches);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);

  return matches;
}

// The one width at which the tickets surface changes shape: below it the board
// and the table both become the grouped list, and the filters move into a
// sheet. Named once so the page, the switcher and the sheet cannot disagree
// about where the line is.
export const WIDE = "(min-width: 900px)";

export const useIsWide = () => useMediaQuery(WIDE);
