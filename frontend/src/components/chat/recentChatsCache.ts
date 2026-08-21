import type { ThreadSummary } from "../../lib/chat";

// The last cross-scope thread list RecentChatsMenu fetched, held for the life of
// the tab so re-opening the picker (and, more to the point, the menu remounting
// when a scope switch rebuilds the toolbar) paints the last-known list in the
// first render instead of the "Loading" row. It is a plain module singleton, not
// React state, so it survives those remounts; the menu still revalidates on every
// open, so a stale-by-a-conversation list is corrected as soon as it is shown.
//
// This is a display cache only. It is never the source of truth for which chat is
// active or what a chat contains — those come from useChatThread/useThreadLog by
// id, unchanged.

let cached: ThreadSummary[] | null = null;

export const recentChatsCache = {
  get(): ThreadSummary[] | null {
    return cached;
  },
  set(list: ThreadSummary[]): void {
    cached = list;
  },
};
