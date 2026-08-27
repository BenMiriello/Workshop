import { useQuery } from "@tanstack/react-query";
import { fetchThreads, type ThreadSummary } from "../../lib/dashboard";
import { queryKeys } from "../../lib/queryKeys";

// ONE THREADS READ FOR THE PAGE, because two cards want it: "Pick up where you
// left off" needs the list, and "Running now" needs the ones with a turn in
// flight. Two independent polls of the same URL would be the request
// duplication the client-state audit measured in chat (three to four identical
// reads of one thread inside 150ms), reproduced deliberately.
//
// This is NOT a cache and it is not a step toward one: it is one hook, called
// once, at the page. Everything else about the four cards stays independent --
// each still owns its own read, and one endpoint failing still degrades one
// card.
//
// IT IS THE QUERY LAYER NOW, on the shared key. `GET /api/chat/threads` has
// several owners in this app and `["chat", "threads", <scope>]` is the one they
// are collapsing onto, so this hook and the chat toolbar's recent-chats menu
// already share a cache entry and an in-flight request. The scope slot is null
// because this read asks for every scope.

const POLL_MS = 15_000;

export type ThreadsRead = {
  threads: ThreadSummary[] | null;
  error: string | null;
};

export function useThreads(): ThreadsRead {
  const q = useQuery({
    queryKey: queryKeys.chat.threads(null),
    queryFn: fetchThreads,
    refetchInterval: POLL_MS,
    // This read owns its own cadence, so a staleness window over it would only
    // swallow the catch-up read on the way back from hidden, which is half of
    // what made pausing a poll safe to default to (packages/ui/src/usePoll.ts).
    staleTime: 0,
    // `usePoll` DOES NOT RETRY: a failed tick is simply followed by the next
    // tick, fifteen seconds later. The app's transient-retry default would put
    // extra requests in front of a cadence that already re-reads on its own.
    retry: false,
  });

  return {
    // A failed read keeps the last good list, exactly as the `useState` this
    // replaces did: the cards stay drawn and the failure is said beside them.
    threads: q.data ?? null,
    // Set on a failed read and cleared by the next SUCCESSFUL one, not by the
    // start of the next attempt. That is what `isError` already means.
    error: q.isError ? (q.error instanceof Error ? q.error.message : String(q.error)) : null,
  };
}
