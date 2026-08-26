import { useCallback, useState } from "react";
import { usePoll } from "@workshop/ui";
import { fetchThreads, type ThreadSummary } from "../../lib/dashboard";

// ONE THREADS READ FOR THE PAGE, because two cards want it: "Pick up where you
// left off" needs the list, and "Running now" needs the ones with a turn in
// flight. Two independent polls of the same URL would be the request
// duplication the client-state audit measured in chat (three to four identical
// reads of one thread inside 150ms), reproduced deliberately.
//
// This is NOT a cache and it is not a step toward one: it is one hook, called
// once, at the page. Everything else about the four cards stays independent --
// each still owns its own read, and one endpoint failing still degrades one
// card. When the query layer chat now uses reaches this page, this hook is what
// it replaces.

const POLL_MS = 15_000;

export type ThreadsRead = {
  threads: ThreadSummary[] | null;
  error: string | null;
};

export function useThreads(): ThreadsRead {
  const [threads, setThreads] = useState<ThreadSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setThreads(await fetchThreads());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);
  usePoll(load, POLL_MS);

  return { threads, error };
}
