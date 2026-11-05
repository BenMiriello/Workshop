import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type UIEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { MessagesSquare, Pencil, Search } from "lucide-react";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@workshop/ui";
import { chatApi, type ThreadSummary } from "../../lib/chat";
import { queryKeys } from "../../lib/queryKeys";
import {
  PAGE_SIZE, firstOfGroups, groupByDate, newestFirst, searchGroups, type ChatGroup, type SearchRow,
} from "../../pages/chatList";
import { scopeKind } from "./chatScope";
import ScopeIcon from "./ScopeIcon";
import { RunningDot } from "./ChatRow";
import { useChatSearch } from "./useChatSearch";
import { useRoomBelow } from "./useRoomBelow";

// The chat's name in the toolbar, and behind it every other conversation.
//
// The name was already the one word on the bar identifying the conversation, so
// making it the way back to the others costs no room (operator, 2026-08-12).
// Renaming is the pencil on the current chat's row.
//
// CROSS-SCOPE, not this scope's threads, which is why each row carries a scope
// icon. Newest first under the same date headings as the Chats page, and the
// same search (`useChatSearch`): title matches, then message matches.
//
// THE LIST LOADS AS IT SCROLLS. It mounts a page (`PAGE_SIZE`, the Chats page's
// own) and mounts the next one as the scroll nears the bottom, the same as
// pressing that page's Show more. The search sits pinned above the rows and
// All chats pinned below them; the rows scroll between.
//
// FETCHED WHEN OPENED, not on mount: `enabled: false` still reads and paints
// the shared cache entry (the dashboard writes the same key), and the trigger's
// onClick revalidates it. Menu calls that before its own toggle, so one tap
// does both.

// How close to the bottom of the rows the next page is mounted.
const LOAD_AHEAD_PX = 160;

type Row = SearchRow<ThreadSummary>;

export default function RecentChatsMenu({
  title, activeId, scope = null, onOpenThread, onSeeAll, onEditTitle,
}: {
  /** The open chat's name, which is the trigger. */
  title: string;
  activeId: string | null;
  /** One context key to list, or null for the cross-scope list. An embedded
   *  chat lists only its own scope's conversations. */
  scope?: string | null;
  /** Switch to that conversation. The caller owns how (it is a route change,
   *  since the thread may live under a different scope than this chat). */
  onOpenThread: (t: ThreadSummary) => void;
  /** The Chats page. */
  onSeeAll: () => void;
  /** Opens the rename flow for the current chat (pencil on the active row). */
  onEditTitle: () => void;
}) {
  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: queryKeys.chat.threads(scope),
    queryFn: () => chatApi.listThreads(scope ?? undefined),
    enabled: false,
    // A menu that has already drawn its last-known rows must not sit on a
    // spinner through a backoff.
    retry: false,
  });
  const [query, setQuery] = useState("");
  const [shown, setShown] = useState(PAGE_SIZE);
  const { ref: triggerRef, room, measure } = useRoomBelow<HTMLButtonElement>();
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const search = useChatSearch(query, scope);

  // Revalidates on every open. `cancelRefetch: false` ignores a second open
  // while one read is in flight instead of abandoning it for another.
  const load = useCallback(() => {
    setQuery("");
    setShown(PAGE_SIZE);
    measure();
    void refetch({ cancelRefetch: false });
    // A pointer that can hover has a keyboard to type with; a phone would pop
    // its keyboard over the list it just opened. After Menu focuses its list.
    if (window.matchMedia("(pointer: fine)").matches) {
      requestAnimationFrame(() => searchRef.current?.focus());
    }
  }, [refetch, measure]);

  useEffect(() => {
    setShown(PAGE_SIZE);
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [search.active, search.hits]);

  const sorted = useMemo(() => (data ? newestFirst(data) : null), [data]);
  const total = data?.length ?? 0;
  const all: ChatGroup<Row>[] | null = useMemo(() => {
    if (search.active) return search.hits && sorted ? searchGroups(sorted, search.hits) : null;
    if (!sorted) return null;
    return groupByDate(sorted).map((g) => ({
      ...g, items: g.items.map((thread) => ({ thread, snippet: null })),
    }));
  }, [search.active, search.hits, sorted]);
  const count = all?.reduce((n, g) => n + g.items.length, 0) ?? 0;
  const groups = useMemo(() => (all ? firstOfGroups(all, shown) : null), [all, shown]);
  const runningOrdinal = useMemo(() => {
    const order = new Map<string, number>();
    groups?.forEach((g) => g.items.forEach(({ thread: t }) => {
      if (t.running) order.set(t.id, order.size);
    }));
    return order;
  }, [groups]);

  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < LOAD_AHEAD_PX) {
      setShown((n) => (n < count ? n + PAGE_SIZE : n));
    }
  };

  const err = !isFetching && isError ? String(error) : null;
  const note = (text: string, hint?: string) => (
    <MenuItem disabled onSelect={() => {}} title={hint}>{text}</MenuItem>
  );

  const row = ({ thread: t, snippet }: Row) => {
    const isActive = t.id === activeId;
    const item = (
      <MenuItem
        key={t.id}
        icon={<ScopeIcon kind={scopeKind(t.context_key)} />}
        checked={isActive}
        onSelect={() => onOpenThread(t)}
      >
        <span className="rcm-row">
          {t.running && <RunningDot running ordinal={runningOrdinal.get(t.id) ?? 0} />}
          <span className="rcm-row__text">
            <span className="rcm-row__name">{t.title || "Untitled chat"}</span>
            {snippet && <span className="rcm-row__snip">{snippet}</span>}
          </span>
        </span>
      </MenuItem>
    );
    if (!isActive) return item;
    return (
      <div key={t.id} className="rcm-title-row">
        {item}
        <button
          className="rcm-title-row__pencil"
          role="menuitem"
          onClick={onEditTitle}
          title="Rename chat"
          aria-label="Rename chat"
        >
          <Pencil size={12} aria-hidden />
        </button>
      </div>
    );
  };

  return (
    <Menu
      label="Chats"
      // The wrapper is the toolbar's flex item, so it carries the shrink that
      // lets the name ellipsise instead of pushing the bar wide.
      className="chat-tb__titlemenu"
      trigger={
        <button ref={triggerRef} className="chat-tb__title" onClick={load} title="Chats">
          {title}
        </button>
      }
    >
      <div
        className="rcm-body"
        style={room != null ? ({ "--rcm-room": `${room}px` } as CSSProperties) : undefined}
      >
        <label className="ws-searchfield rcm-search">
          <Search aria-hidden />
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search chats"
            aria-label="Search chats"
            spellCheck={false}
          />
        </label>
        <div className="rcm-scroll" ref={scrollRef} onScroll={onScroll}>
          {/* The states a list owes, at menu scale: a row each, none of them
              selectable, so the menu never opens onto nothing unexplained. */}
          {!search.active && isFetching && sorted === null && note("Loading")}
          {err && note("Couldn't load chats", `GET /api/chat/threads: ${err}`)}
          {search.error && note("Couldn't search chats", `GET /api/chat/threads/search: ${search.error}`)}
          {search.active && groups === null && !search.error && note("Searching")}
          {groups?.length === 0 && note(search.active ? "No chats match" : "No other chats yet")}
          {groups?.map((g) => (
            <div key={g.key}>
              <MenuLabel>{g.label}</MenuLabel>
              {g.items.map(row)}
            </div>
          ))}
        </div>
        {total > 0 && (
          <>
            <MenuSeparator />
            <MenuItem icon={<MessagesSquare size={14} aria-hidden />} onSelect={onSeeAll}>
              All {total} chats
            </MenuItem>
          </>
        )}
      </div>
    </Menu>
  );
}
