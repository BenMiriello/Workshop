import { previewSplit, type ModelDisplay } from "./models";

// Where a long message gets cut, as a pure function.
//
// Extracted from ElidedContent so the two rules the operator actually cares
// about are executable: the preview keeps an EVEN amount above and below the
// cut, and the divider reports how many lines are hidden. Both were previously
// buried in JSX with no way to check them except by eye on a long message.

// ~chars per line on a phone; over-counts a little on desktop, which is the safe
// direction — it means we elide slightly sooner than strictly necessary.
export const CPL = 46;

export type ElidePlan =
  | { elide: false }
  | { elide: true; headSrc: string; tailSrc: string; hidden: number };

// A long paragraph with no newlines still wraps to many rows, so "how long is
// this" counts wrapped rows, not "\n"s.
export function visualLineCount(source: string): number {
  return source
    .split("\n")
    .reduce((n, l) => n + Math.max(1, Math.ceil(l.length / CPL)), 0);
}

export function elidePlan(source: string, cfg: ModelDisplay): ElidePlan {
  const lines = source.split("\n");
  const visualLines = visualLineCount(source);
  if (visualLines <= Math.max(cfg.elideOverLines, cfg.previewLines + 1)) {
    return { elide: false };
  }
  const { head, tail } = previewSplit(cfg.previewLines);
  // Prefer slicing on real line breaks (lists, paragraphs, code)...
  if (lines.length >= cfg.previewLines + 2) {
    return {
      elide: true,
      headSrc: lines.slice(0, head).join("\n"),
      tailSrc: lines.slice(-tail).join("\n"),
      hidden: lines.length - head - tail,
    };
  }
  // ...and fall back to characters when it is a few long wrapped lines, so the
  // preview still trims. There are no "\n"s to count here, so the hidden count
  // is the wrapped-row estimate.
  return {
    elide: true,
    headSrc: source.slice(0, head * CPL),
    tailSrc: source.slice(-tail * CPL),
    hidden: Math.max(1, visualLines - cfg.previewLines),
  };
}
