// WHICH APP FRAMES THE SHELL KEEPS ALIVE, and when one goes.
//
// An app's screen is a document in a frame. When the frame belonged to the
// route, leaving the app destroyed the document and coming back loaded it from
// scratch: scroll, open panels, typed text, a playing video, a browser's live
// pages, all gone on every switch. The shell now owns the frames and hides the
// ones not on screen, so returning shows the same document.
//
// Two rules live here because both are easy to get wrong in the component:
//
// 1. THE LIST IS IN CREATION ORDER, NEVER RECENCY ORDER. React moves a keyed
//    child to reorder it, and moving an iframe in the DOM reloads it. Recency
//    is a counter on each entry, used only to pick what to evict.
// 2. A FRAME NAVIGATES ONLY WHEN THE SHELL ASKS FOR A DIFFERENT ADDRESS than it
//    asked for last time. Returning through the same link keeps wherever the
//    app had moved to inside its own document; a deep link elsewhere loads it.

export const KEEP_FRAMES = 4;

export type KeptFrame = {
  key: string;
  slug: string;
  title: string;
  /** The address the frame element carries. */
  src: string;
  usedAt: number;
};

export type FrameRequest = { key: string; slug: string; title: string; src: string };

export const frameKey = (slug: string, service: string) => `${slug}/${service}`;

/** `frames` with `req` shown: added at the end when new, its address changed
 *  only when the shell asked for a different one, and the least recently used
 *  others dropped past `limit`. */
export function keepFrame(
  frames: readonly KeptFrame[], req: FrameRequest, now: number, limit = KEEP_FRAMES,
): KeptFrame[] {
  const found = frames.find((f) => f.key === req.key);
  const next = found
    ? frames.map((f) => (f.key === req.key ? { ...f, title: req.title, src: req.src, usedAt: now } : f))
    : [...frames, { ...req, usedAt: now }];
  if (next.length <= limit) return next;
  const evict = new Set(
    next.filter((f) => f.key !== req.key)
      .sort((a, b) => a.usedAt - b.usedAt)
      .slice(0, next.length - limit)
      .map((f) => f.key),
  );
  return next.filter((f) => !evict.has(f.key));
}

export function dropFrame(frames: readonly KeptFrame[], key: string): readonly KeptFrame[] {
  return frames.some((f) => f.key === key) ? frames.filter((f) => f.key !== key) : frames;
}
