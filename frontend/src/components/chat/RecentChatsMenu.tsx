import { useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare } from "lucide-react";
import { Menu, MenuItem, MenuSeparator } from "@workshop/ui";
import { chatApi, type ThreadSummary } from "../../lib/chat";
import { scopeKind } from "./chatScope";
import ScopeIcon from "./ScopeIcon";
import { queryKeys } from "../../lib/queryKeys";

// Trim a full cross-scope list to the recent few this menu shows, newest first.
const recentSlice = (list: ThreadSummary[]): ThreadSummary[] =>
  [...list]
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, RECENT_LIMIT);

// The chat's name in the toolbar, and behind it the conversations you were in
// most recently.
//
// The toolbar used to say a conversation picker was DELIBERATELY not here,
// because it duplicated the /chats list. The operator reversed that on
// 2026-08-12: the name was already the one word on the bar identifying the
// conversation, so making it the way back to the others costs no room at all,
// and the list you actually want is the last few, not all of them. Renaming
// moved to the pencil beside it, which is where every app puts it.
//
// CROSS-SCOPE, not this scope's threads. That is why each row carries a scope
// icon: a list of the last few chats is only useful if the one you left in
// another project is in it. Ordering is `updated_at` descending, the same sort
// the /chats page applies, so "recent" means one thing in both places.
//
// FETCHED WHEN OPENED, not on mount. Every chat in the app would otherwise pay
// for a full cross-scope thread list it may never show. The trigger's own
// onClick loads it; Menu calls that before its own toggle, so one tap does both.
//
// THE CACHE IS THE QUERY CACHE NOW. This used to seed from a module singleton
// (`recentChatsCache`) whose only job was painting the last-known list on a
// remount instead of the "Loading" row, and the shared key `["chat", "threads",
// <scope>]` does that for nothing: the dashboard's own threads read writes the
// same entry, so opening the menu on a chat reached from the dashboard is
// already warm. `enabled: false` is what keeps "on open, not on mount" true --
// a disabled query still reads the cache and never fetches on its own.
//
// ONE THING THE SINGLETON DID THAT THIS DOES NOT: it lived for the life of the
// tab. The entry lives for `gcTime` (5 minutes) past the last component holding
// it, so leaving the chat for longer than that and coming back shows "Loading"
// for one request. Every remount the old comment named -- reopening the menu, a
// scope switch rebuilding the toolbar -- is inside that window and unaffected.

const RECENT_LIMIT = 8;

export default function RecentChatsMenu({
  title, activeId, onOpenThread, onSeeAll,
}: {
  /** The open chat's name, which is the trigger. */
  title: string;
  activeId: string | null;
  /** Switch to that conversation. The caller owns how (it is a route change,
   *  since the thread may live under a different scope than this chat). */
  onOpenThread: (t: ThreadSummary) => void;
  /** The overflow path: more chats exist than this menu shows. */
  onSeeAll: () => void;
}) {
  const q = useQuery({
    // The shared key. The scope slot is null because this list is deliberately
    // cross-scope, which is the same list the dashboard reads.
    queryKey: queryKeys.chat.threads(null),
    queryFn: () => chatApi.listThreads(),
    // On open, not on mount. The cached list is still read and painted.
    enabled: false,
    // The read this replaces did not retry, and a menu that has already drawn
    // its last-known rows must not sit on a spinner through a backoff.
    retry: false,
  });

  const { data, isFetching, isError, error, refetch } = q;
  // Revalidates on every open: the list is a handful of rows, and a "recent"
  // list an hour of conversation out of date is worse than one that costs a
  // request. The cached rows stay on screen while it runs (never cleared to
  // null), so revalidation refreshes in place rather than flashing "Loading". A
  // second open while one is in flight is ignored -- `cancelRefetch: false` is
  // that guard; the default would abandon the request in flight and start
  // another.
  const load = useCallback(() => { void refetch({ cancelRefetch: false }); }, [refetch]);

  const threads = useMemo(() => (data ? recentSlice(data) : null), [data]);
  const total = data?.length ?? 0;
  const loading = isFetching;
  // Set on a failed read and cleared at the START of the next attempt, which is
  // what the `setErr(null)` at the top of the old `load` did: reopening the menu
  // after a failure shows the loading row, not the stale failure.
  const err = !isFetching && isError ? String(error) : null;

  return (
    <Menu
      label="Recent chats"
      // The wrapper is what sits in the toolbar's flex row now, so it carries
      // the shrink the bare title button used to. `.ws-menu` is flex: 0 0 auto,
      // and without this the name would push the toolbar wide instead of
      // ellipsising, which is the whole reason the bar fits one row on a phone.
      className="chat-tb__titlemenu"
      trigger={
        <button className="chat-tb__title" onClick={load} title="Recent chats">
          {title}
        </button>
      }
    >
      {/* The states a list owes, at menu scale: a row each, none of them
          selectable, so the menu never opens onto nothing with no explanation. */}
      {loading && threads === null && (
        <MenuItem disabled onSelect={() => {}}>Loading</MenuItem>
      )}
      {err && (
        <MenuItem disabled onSelect={() => {}} title={`GET /api/chat/threads: ${err}`}>
          Couldn't load recent chats
        </MenuItem>
      )}
      {threads?.length === 0 && (
        <MenuItem disabled onSelect={() => {}}>No other chats yet</MenuItem>
      )}
      {threads?.map((t) => (
        <MenuItem
          key={t.id}
          icon={<ScopeIcon kind={scopeKind(t.context_key)} />}
          checked={t.id === activeId}
          onSelect={() => onOpenThread(t)}
        >
          {t.title || "Untitled chat"}
        </MenuItem>
      ))}
      {/* Overflow, stated rather than silently truncated: this menu shows the
          last few, and says so by handing off to the page that has them all. */}
      {total > RECENT_LIMIT && (
        <>
          <MenuSeparator />
          <MenuItem icon={<MessagesSquare size={14} aria-hidden />} onSelect={onSeeAll}>
            All {total} chats
          </MenuItem>
        </>
      )}
    </Menu>
  );
}
