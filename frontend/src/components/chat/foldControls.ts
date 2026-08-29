// WHAT THE TWO FOLD BUTTONS SAY AND WHETHER THEY ACT, as one rule in one place.
//
// The rule is here rather than inline in ContextModal because the modal renders
// through a portal and cannot be exercised without a DOM harness, which this
// tree deliberately does not have (frontend/CLAUDE.md: extract the pure rule
// into its own module so it can be executed). It earned that: the first pass
// disabled Compact and left Clear reachable, so a second fold was still one tap
// away, and it labelled a running CLEAR "Compacting" because it took a boolean
// and could not tell the two folds apart.

export type FoldMode = "compact" | "clear";

export type FoldControl = {
  /** Inert. Rendered as `aria-disabled`, never the native `disabled`: a
   *  natively disabled button drops pointer events, so its reason never fires
   *  on a tap and does not fire at all in some browsers. */
  inert: boolean;
  label: string;
  /** Shown on hover and on tap. Undefined when the action is offered, because
   *  a control that works does not explain itself. */
  reason?: string;
};

const reasonFor = (running: FoldMode) =>
  `A ${running} is already running in this chat.`;

/** Both controls, from the one fact that either fold is running. `running` is
 *  the SERVER's answer merged with this tab's, so a fold started elsewhere
 *  makes these inert too. */
export function foldControls(running: FoldMode | null): {
  compact: FoldControl; clear: FoldControl;
} {
  if (running === null) {
    return {
      compact: { inert: false, label: "Compact now" },
      clear: { inert: false, label: "Clear context" },
    };
  }
  // BOTH inert, whichever is running: the server refuses a clear during a
  // compact exactly as it refuses a second compact, so offering it is offering
  // an error.
  return {
    compact: {
      inert: true,
      label: running === "compact" ? "Compacting" : "Compact now",
      reason: reasonFor(running),
    },
    clear: {
      inert: true,
      label: running === "clear" ? "Clearing" : "Clear context",
      reason: reasonFor(running),
    },
  };
}
