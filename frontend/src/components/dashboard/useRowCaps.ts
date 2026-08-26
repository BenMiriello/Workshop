import { useMediaQuery } from "../../lib/useMediaQuery";
import { rowCaps } from "../../lib/dashboard";

// HOW MANY ROWS FIT, at this width. The arithmetic is in `rowCaps`; this is the
// two lines of React around it.
//
// 900px is the frontend's own WIDE breakpoint (workshop.preview.json calls it
// that too), and it is the same number the dashboard's stylesheet switches the
// grid on, so the row count and the card height can never disagree about which
// layout is in play.
export function useRowCaps(): { needs: number; lines: number } {
  return rowCaps(useMediaQuery("(min-width: 901px)"));
}
