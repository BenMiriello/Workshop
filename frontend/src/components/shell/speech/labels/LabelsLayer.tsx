import { useSyncExternalStore } from "react";
import { pageLabels } from "./pageLabels";
import { sayingOf } from "./targetFacts";
import "./labels.css";

// The badge's size, mirrored by `.ws-labels__badge`. It sits this far up and
// left of its control's corner, so it marks the control without covering it.
const BADGE_H = 18;
const BADGE_W = (n: number) => (n > 99 ? 31 : n > 9 ? 24 : 18);
const OFFSET = 6;
const EDGE = 2;
// The badge of the control the pointer is over also shows what it is said by,
// up to this wide (`.ws-labels__name`); its width is estimated to keep it on
// screen.
const NAME_MAX = 280;
const NAME_CHAR = 7;

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(v, hi));
}

/** The page's controls numbered for "click 4", drawn over everything and
 *  never in the way of a pointer. The one pointed at also shows its name, so
 *  it can be said by that. Nothing while labels are off. */
export function LabelsLayer() {
  const { on, targets, hover } = useSyncExternalStore(pageLabels.subscribe, pageLabels.get);
  if (!on) return null;
  const root = document.documentElement;
  return (
    <div className="ws-labels" aria-hidden="true">
      {targets.map((t) => {
        const name = t.n === hover ? sayingOf(t, targets) : "";
        const w = BADGE_W(t.n) + (name ? Math.min(NAME_MAX, name.length * NAME_CHAR + 8) : 0);
        return (
          <span
            key={t.n}
            className="ws-labels__badge"
            data-hover={t.n === hover || undefined}
            style={{
              left: clamp(t.box.left - OFFSET, EDGE, root.clientWidth - w - EDGE),
              top: clamp(t.box.top - OFFSET, EDGE, root.clientHeight - BADGE_H - EDGE),
            }}
          >
            {t.n}
            {name && <span className="ws-labels__name">{name}</span>}
          </span>
        );
      })}
    </div>
  );
}
