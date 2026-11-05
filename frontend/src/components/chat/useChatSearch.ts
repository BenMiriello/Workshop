import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { chatApi, type ThreadSearchHit } from "../../lib/chat";
import { queryKeys } from "../../lib/queryKeys";

// The chat search both chat lists share: the name menu in the chat's top bar
// and the Chats page. Typing settles for a moment before it asks the server,
// and the last answer stays on screen while the next one is on its way, so the
// list narrows in place instead of flashing empty on every key.

const SETTLE_MS = 200;

export function useChatSearch(query: string, scope: string | null): {
  /** Whether there is a query at all, which is what switches a list over. */
  active: boolean;
  hits: ThreadSearchHit[] | null;
  error: string | null;
} {
  const typed = query.trim();
  const [q, setQ] = useState(typed);
  useEffect(() => {
    const t = setTimeout(() => setQ(typed), SETTLE_MS);
    return () => clearTimeout(t);
  }, [typed]);

  const res = useQuery({
    queryKey: queryKeys.chat.threadSearch(scope, q),
    queryFn: () => chatApi.searchThreads(q, scope ?? undefined),
    enabled: q.length > 0,
    placeholderData: keepPreviousData,
    retry: false,
  });

  return {
    active: typed.length > 0,
    hits: q.length > 0 ? res.data ?? null : null,
    error: res.isError ? String(res.error) : null,
  };
}
