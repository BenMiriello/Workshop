// How much the model spends on a turn, as the UI says it.
//
// ONE DEFINITION, the same rule models.ts and chatModes.ts follow: the label and
// its gloss live here, not inline in the trigger and again in each menu item.
//
// The LEVELS themselves are not defined here on purpose. They come from the
// backend per model (`Provider.model_caps[model].efforts.levels`) because the
// ladder is not one global list: Sonnet 4.6 supports `max` but not `xhigh`, and
// Haiku supports none of it. A frontend copy of the list would be wrong for some
// model the day a model is added. What lives here is only how to SAY a level.

// Ascending, and used only to order/compare levels the backend has already
// declared. Membership here is not a claim that any model supports it.
export const EFFORT_ORDER = ["low", "medium", "high", "xhigh", "max"];

const EFFORT_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

// Shown in the menu row beside the name, and on the trigger's tooltip. One line:
// a description long enough to wrap competes with the name for width and wins.
const EFFORT_HINTS: Record<string, string> = {
  low: "Fastest and cheapest. Fewer tool calls, little thinking.",
  medium: "Balanced. Solid work without the full spend.",
  high: "The model's default. Thorough reasoning.",
  xhigh: "For long agentic work: more exploration, more tool calls.",
  max: "No ceiling on thinking. Slowest and most expensive.",
};

// An unknown level renders as its own id rather than vanishing, so a level added
// by the backend is usable here before this table knows its name.
export function effortLabel(level: string): string {
  return EFFORT_LABELS[level] ?? level;
}

export function effortHint(level: string): string {
  return EFFORT_HINTS[level] ?? "";
}

/** Order a model's declared levels weakest-first. Anything this file has never
 *  heard of sorts to the end rather than being dropped. */
export function orderEfforts(levels: string[]): string[] {
  return [...levels].sort((a, b) => {
    const ia = EFFORT_ORDER.indexOf(a);
    const ib = EFFORT_ORDER.indexOf(b);
    return (ia < 0 ? EFFORT_ORDER.length : ia) - (ib < 0 ? EFFORT_ORDER.length : ib);
  });
}

/** The closest level a model actually offers, for when the model changes under a
 *  selection. Steps DOWN to the strongest supported level at or below the one
 *  asked for (xhigh on a model without it becomes high, not max) and only steps
 *  up when there is nothing below. Empty in, empty out. */
export function nearestEffort(level: string, levels: string[]): string {
  const ordered = orderEfforts(levels);
  if (!ordered.length) return "";
  if (ordered.includes(level)) return level;
  const want = EFFORT_ORDER.indexOf(level);
  if (want < 0) return "";
  const below = ordered.filter((l) => {
    const i = EFFORT_ORDER.indexOf(l);
    return i >= 0 && i <= want;
  });
  return below.length ? below[below.length - 1] : ordered[0];
}
