// The latest copy made in the Workshop, for pasting it back in the Workshop
// when copies stay off the system clipboard. Leaving the Workshop (another app
// in front, the page hidden) ends it: whatever was copied out there is what a
// paste should give then, so the system clipboard's own paste goes through.

let latest: { text: string; at: number } | null = null;
let leftAt = 0;

export const heldCopy = {
  took(text: string, now = Date.now()): void {
    latest = { text, at: now };
  },
  left(now = Date.now()): void {
    leftAt = now;
  },
  /** The text a paste in the Workshop gives now, or null for the system's. */
  current(): string | null {
    return latest && latest.at >= leftAt ? latest.text : null;
  },
  reset(): void {
    latest = null;
    leftAt = 0;
  },
};
