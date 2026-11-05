import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Notice, Skeleton } from "@workshop/ui";
import { scheduleApi } from "../../lib/chatSchedules";
import { sendText } from "./schedules";
import { scheduledKey } from "./useScheduled";

// What a schedule has sent, newest first, read from the chat itself. The
// latest few show; the rest open on Show all.

const FIRST = 5;

export default function ScheduleSends({ threadId, sid }: { threadId: string; sid: string }) {
  const [all, setAll] = useState(false);
  const q = useQuery({
    queryKey: [...scheduledKey(threadId), "sends", sid],
    queryFn: () => scheduleApi.sends(threadId, sid),
    refetchInterval: 20_000,
  });
  if (q.isLoading) return <Skeleton width="40%" />;
  if (q.isError) {
    return (
      <Notice tone="error" action={<Button size="sm" variant="ghost" onClick={() => void q.refetch()}>Retry</Button>}>
        Could not load what it sent.
      </Notice>
    );
  }
  const sends = q.data?.sends ?? [];
  if (!sends.length) return null;
  const shown = all ? sends : sends.slice(0, FIRST);
  return (
    <div className="chat-sched-sends">
      <ul className="chat-sched-sends__list">
        {shown.map((s, i) => (
          <li key={`${s.at}-${i}`} className={s.state === "waiting" ? "chat-sched-sends__waiting" : undefined}>
            {sendText(s)}
          </li>
        ))}
      </ul>
      {sends.length > FIRST && (
        <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all (${sends.length})`}
        </Button>
      )}
    </div>
  );
}
