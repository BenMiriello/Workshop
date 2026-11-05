import { useQuery } from "@tanstack/react-query";
import { Clock } from "lucide-react";
import { Button } from "@workshop/ui";
import { chatApi, type SendRule } from "../../lib/chat";
import SendLaterMenu from "./SendLaterMenu";
import { ruleText } from "./sendRules";

// The line under a queued message that went out under a rule (Send later):
// what it is waiting for, Send now, and Change. A message queued only because a
// turn is running has no rule and shows nothing here.

export default function QueuedRule({ threadId, when, streaming, onSet }: {
  threadId: string | null;
  when: SendRule;
  streaming: boolean;
  /** A new rule, or null to send it now. */
  onSet: (when: SendRule | null) => void;
}) {
  const threads = useQuery({
    queryKey: ["chat-threads-running"],
    queryFn: () => chatApi.listThreads(""),
    enabled: when.kind === "after_thread",
    staleTime: 5_000,
  });
  const resources = useQuery({
    queryKey: ["chat-thread-resources", threadId],
    queryFn: () => chatApi.threadResources(threadId as string),
    enabled: !!threadId && when.kind === "resources_free",
    staleTime: 30_000,
  });
  const text = ruleText(when, {
    chatTitle: (id) => threads.data?.find((t) => t.id === id)?.title || undefined,
    resources: resources.data?.needs,
  });
  return (
    <div className="chat-queued-rule">
      <Clock size={13} strokeWidth={1.8} aria-hidden />
      <span>{text}</span>
      <Button size="sm" variant="ghost" onClick={() => onSet(null)}>Send now</Button>
      <SendLaterMenu
        threadId={threadId}
        streaming={streaming}
        onPick={onSet}
        trigger={<Button size="sm" variant="ghost">Change</Button>}
      />
    </div>
  );
}
