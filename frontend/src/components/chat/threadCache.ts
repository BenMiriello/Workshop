import type { ThreadSnapshot } from "./threadStore";

// A module-level, in-memory, per-tab cache of the last few threads this tab
// has actually finished loading — so switching BACK to one of them paints the
// transcript instantly instead of blank-then-loaded. See useThreadLog.ts for
// where this is read (on a thread switch) and written (as the active thread's
// state changes).
//
// SESSION-ONLY, ON PURPOSE. Nothing here reaches localStorage/IndexedDB: a
// stale transcript surviving a reload is a worse bug than a slow load, and a
// durable cache raises its own invalidation questions this phase is not
// answering. A reload always does today's cold, correct, tail-first load.
//
// WHAT IS DELIBERATELY NOT HERE:
//   - pending sends (useSendOutbox owns those; see ThreadSnapshot's comment)
//   - the working indicator's TRUTH (a cached activeTurn is a last-known guess,
//     painted for one frame; useThreadLog always follows a cache hit with a
//     cursor sync, and the server's answer is what the indicator actually
//     tracks from then on)
//   - anything about WHICH branch this snapshot belongs to being inferred —
//     the caller records it explicitly (see `CachedBranch`) so a branch that
//     changed while this tab was away can be told apart from the one cached.

/** The branch a cached snapshot was read as belonging to. Mirrors the shape
 *  `useThreadLog` already exposes as `ThreadLog.branch`; duplicated here
 *  (rather than imported) because it is a small, stable, structural type and
 *  importing it would pull a React hook module into a plain cache module. */
export type CachedBranch = { id: string | null; name: string; count: number; head: string | null };

type CachedThread = {
  snapshot: ThreadSnapshot;
  branch: CachedBranch;
};

/** A small LRU keyed by thread id.
 *
 *  Backed by a `Map`, whose keys iterate in insertion order — the same trick
 *  every LRU-over-Map uses: `get` and `set` both re-insert the key, so the
 *  first key in iteration order is always the least recently touched one, and
 *  eviction never needs its own bookkeeping structure. */
export class ThreadCache {
  private entries = new Map<string, CachedThread>();

  constructor(private readonly limit: number = 10) {
    if (limit < 1) throw new Error("ThreadCache limit must be at least 1");
  }

  get size(): number {
    return this.entries.size;
  }

  /** The cached entry for this thread, or `undefined` on a miss. Touches
   *  recency: a hit is now the MOST recently used entry, same as a fresh
   *  write would make it. */
  get(threadId: string): CachedThread | undefined {
    const entry = this.entries.get(threadId);
    if (!entry) return undefined;
    this.entries.delete(threadId);
    this.entries.set(threadId, entry);
    return entry;
  }

  /** Replace (or create) the entry for this thread and mark it most recently
   *  used, evicting the least recently used entry if this pushes the cache
   *  over its limit. */
  set(threadId: string, snapshot: ThreadSnapshot, branch: CachedBranch): void {
    this.entries.delete(threadId);
    this.entries.set(threadId, { snapshot, branch });
    while (this.entries.size > this.limit) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined) break;
      this.entries.delete(oldest);
    }
  }

  /** Drop one thread's entry, if any. Not currently called in production —
   *  kept for symmetry and available to a future caller that learns a thread
   *  was deleted; a stale entry for a thread nobody can navigate back to is
   *  otherwise harmless and just ages out via the LRU. */
  delete(threadId: string): void {
    this.entries.delete(threadId);
  }
}

/** THE cache. One per tab, living for the tab's session — see the module
 *  header for why that is a feature, not a gap. */
export const threadCache = new ThreadCache(10);
