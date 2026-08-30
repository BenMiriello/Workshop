// WHAT THE TWO CONTEXT BUTTONS SAY AND WHETHER THEY ACT, as one rule in one
// place.
//
// The rule is here rather than inline in ContextModal because the modal renders
// through a portal and cannot be exercised without a DOM harness, which this
// tree deliberately does not have (frontend/CLAUDE.md: extract the pure rule
// into its own module so it can be executed). It earned that: the first pass
// disabled Compact and left Clear reachable, so a second cut was still one tap
// away.
//
// ONLY A CLEAR CAN BE RUNNING HERE. It used to be either, because compaction
// was a server-side fold that held the thread the same way a clear does.
// Compaction is now the provider's own /compact, run as an ordinary turn, and a
// turn needs no hold: a second press queues behind it and says so.

export type FoldMode = "clear";

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

const REASON = "A clear is already running in this chat.";

/** Both controls, from the one fact that a clear is running. `running` is the
 *  SERVER's answer merged with this tab's, so a clear started elsewhere makes
 *  these inert too. */
export function foldControls(running: FoldMode | null): {
  compact: FoldControl; clear: FoldControl;
} {
  if (running === null) {
    return {
      compact: { inert: false, label: "Compact now" },
      clear: { inert: false, label: "Clear context" },
    };
  }
  // BOTH inert. A clear moves the cut out from under the conversation, so
  // starting a compaction against it is offering an error.
  return {
    compact: { inert: true, label: "Compact now", reason: REASON },
    clear: { inert: true, label: "Clearing", reason: REASON },
  };
}
