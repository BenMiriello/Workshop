import { isRunning, type TurnItem } from "./grouping";

// When a stretch of steps can skip a re-render.
//
// A live turn re-renders on every stream event, and a long one holds thousands
// of segments, so redrawing every stretch on every event saturated the main
// thread: events queued behind each other and an answered approval stayed on
// screen, still clickable, for seconds. `buildTurnBlocks` rebuilds its item
// arrays on every call, but the reducer keeps every segment it did not touch
// by identity, so comparing the items element-wise finds the one stretch an
// event actually changed.

type StepRunProps = {
  items: TurnItem[];
  cfg: unknown;
  nowMs: number;
  current?: boolean;
  onAnswerPermission?: unknown;
};

function sameItem(a: TurnItem, b: TurnItem): boolean {
  if (a.t === "group" && b.t === "group") {
    return a.steps.length === b.steps.length && a.steps.every((s, k) => s === b.steps[k]);
  }
  return a.t === "step" && b.t === "step" && a.seg === b.seg;
}

/** Whether anything in the stretch reads the clock: a step still running, or
 *  a delegation still working. Only those move on the 1Hz tick. */
export function runTicks(items: TurnItem[]): boolean {
  return items.some((item) => {
    if (item.t === "group") return item.steps.some(isRunning);
    const seg = item.seg;
    return seg.t === "agent" && (!seg.done || seg.steps.some(isRunning));
  });
}

/** The memo comparator for a stretch of steps. `arrived` is deliberately not
 *  compared: it only decides the flash on a step's first render, and a new step
 *  changes `items`, which re-renders the stretch with a current one. */
export function sameStepRun(a: StepRunProps, b: StepRunProps): boolean {
  if (a.cfg !== b.cfg || a.current !== b.current) return false;
  if (a.onAnswerPermission !== b.onAnswerPermission) return false;
  if (a.items.length !== b.items.length) return false;
  if (!a.items.every((item, k) => sameItem(item, b.items[k]))) return false;
  return a.nowMs === b.nowMs || !runTicks(b.items);
}
