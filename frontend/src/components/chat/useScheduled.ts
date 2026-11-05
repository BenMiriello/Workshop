import { useQuery, useQueryClient } from "@tanstack/react-query";
import { scheduleApi, type Schedule } from "../../lib/chatSchedules";

// What is set to send into this chat, shared by the composer's dot, the
// Scheduled dialog and the transcript's bylines through one query.

export const NO_SCHEDULES: readonly Schedule[] = [];

export const scheduledKey = (threadId: string | null) => ["chat-scheduled", threadId] as const;

export function useScheduled(threadId: string | null) {
  return useQuery({
    queryKey: scheduledKey(threadId),
    queryFn: () => scheduleApi.list(threadId as string),
    enabled: !!threadId,
    staleTime: 10_000,
    refetchInterval: 20_000,
  });
}

/** Read again after a change made here. */
export function useRefreshScheduled(threadId: string | null) {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: scheduledKey(threadId) });
}
