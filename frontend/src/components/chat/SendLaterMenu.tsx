import { useState, type ReactElement } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Clock, Cpu, Gauge, Hand, Link2, ListEnd, Repeat } from "lucide-react";
import {
  Button, Field, Input, Menu, MenuItem, MenuSeparator, Modal, Select, Skeleton, StateBlock,
} from "@workshop/ui";
import { chatApi, type SendRule } from "../../lib/chat";
import { atRule, localInputValue, resourceText, whenText } from "./sendRules";
import SchedulesDialog from "./SchedulesDialog";
import { useScheduled } from "./useScheduled";

// SEND LATER: the rules a message can go out under instead of now (backend
// services/send_rules). A plain Send is unchanged; every choice here queues the
// message, so it never joins the running turn and never starts one early.
//
// An option is offered only where it means something: "after this reply" while
// a turn runs, the usage reset when its time is known, "when the GPU is free"
// when this chat's model runs on this machine.
//
// Below them, the standing ones: Repeat makes a schedule of what is typed, and
// Scheduled opens everything set to send into this chat (SchedulesDialog).

type Dialog = null | "at" | "chat" | "repeat" | "scheduled";

export default function SendLaterMenu({ threadId, streaming, onPick, trigger, className, text, onScheduled }: {
  threadId: string | null;
  streaming: boolean;
  onPick: (when: SendRule) => void;
  /** What is typed, for Repeat. The composer's menu only: changing one queued
   *  message's rule has no standing entries. */
  text?: string;
  /** Repeat made a schedule of what was typed: clear it. */
  onScheduled?: () => void;
  trigger: ReactElement<Record<string, unknown>>;
  className?: string;
}) {
  const [dialog, setDialog] = useState<Dialog>(null);
  const resources = useQuery({
    queryKey: ["chat-thread-resources", threadId],
    queryFn: () => chatApi.threadResources(threadId as string),
    enabled: !!threadId,
    staleTime: 30_000,
  });
  const limits = useQuery({
    queryKey: ["usage-limits"],
    queryFn: () => chatApi.usageLimits(),
    staleTime: 30_000,
  });
  const scheduled = useScheduled(onScheduled ? threadId : null);
  const count = scheduled.data?.count ?? 0;
  const needs = resources.data?.needs ?? [];
  const reset = needs.length ? null : limits.data?.next_reset ?? null;

  return (
    <>
      <Menu label="Send later" side="top" trigger={trigger} className={className}>
        {streaming && (
          <MenuItem icon={<ListEnd size={14} aria-hidden />} onSelect={() => onPick({ kind: "after_turn" })}>
            After this reply
          </MenuItem>
        )}
        <MenuItem icon={<Clock size={14} aria-hidden />} onSelect={() => setDialog("at")}>
          At a time
        </MenuItem>
        {reset && (
          <MenuItem icon={<Gauge size={14} aria-hidden />} onSelect={() => onPick({ kind: "usage_reset" })}>
            {`When the usage limit resets (${whenText(reset)})`}
          </MenuItem>
        )}
        {needs.length > 0 && (
          <MenuItem icon={<Cpu size={14} aria-hidden />} onSelect={() => onPick({ kind: "resources_free" })}>
            {`When the ${resourceText(needs)} is free`}
          </MenuItem>
        )}
        <MenuItem icon={<Link2 size={14} aria-hidden />} onSelect={() => setDialog("chat")}>
          After another chat finishes
        </MenuItem>
        <MenuItem icon={<Hand size={14} aria-hidden />} onSelect={() => onPick({ kind: "hold" })}>
          Hold until I send it
        </MenuItem>
        {threadId && onScheduled && <MenuSeparator />}
        {threadId && onScheduled && (
          <MenuItem icon={<Repeat size={14} aria-hidden />} onSelect={() => setDialog("repeat")}>
            Repeat...
          </MenuItem>
        )}
        {threadId && onScheduled && (
          <MenuItem icon={<CalendarClock size={14} aria-hidden />} onSelect={() => setDialog("scheduled")}>
            {count ? `Scheduled (${count})` : "Scheduled"}
          </MenuItem>
        )}
      </Menu>
      {threadId && (dialog === "repeat" || dialog === "scheduled") && (
        <SchedulesDialog threadId={threadId} newText={dialog === "repeat" ? text ?? "" : undefined}
          onCreated={dialog === "repeat" ? onScheduled : undefined} onClose={() => setDialog(null)} />
      )}
      {dialog === "at" && (
        <AtTimeDialog onClose={() => setDialog(null)} onPick={(w) => { setDialog(null); onPick(w); }} />
      )}
      {dialog === "chat" && (
        <AfterChatDialog threadId={threadId} onClose={() => setDialog(null)}
          onPick={(w) => { setDialog(null); onPick(w); }} />
      )}
    </>
  );
}

function AtTimeDialog({ onClose, onPick }: { onClose: () => void; onPick: (w: SendRule) => void }) {
  const [value, setValue] = useState(() => localInputValue(new Date(Date.now() + 3600_000)));
  const rule = atRule(value);
  return (
    <Modal
      open
      onClose={onClose}
      title="Send at a time"
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!rule} onClick={() => rule && onPick(rule)}>Schedule</Button>
        </>
      }
    >
      <Field label="Time" htmlFor="send-at" error={value && !rule ? "Pick a time in the future." : undefined}>
        <Input id="send-at" type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} />
      </Field>
    </Modal>
  );
}

function AfterChatDialog({ threadId, onClose, onPick }: {
  threadId: string | null; onClose: () => void; onPick: (w: SendRule) => void;
}) {
  const threads = useQuery({
    queryKey: ["chat-threads-running"],
    queryFn: () => chatApi.listThreads(""),
    staleTime: 5_000,
  });
  const running = (threads.data ?? []).filter((t) => t.running && t.id !== threadId);
  const [picked, setPicked] = useState("");
  const chosen = picked || running[0]?.id || "";
  let body;
  if (threads.isLoading) body = <Skeleton width="60%" />;
  else if (threads.isError) {
    body = (
      <StateBlock variant="error" title="Could not load chats"
        hint="GET /api/chat/threads failed."
        action={<Button size="sm" onClick={() => void threads.refetch()}>Retry</Button>} />
    );
  } else if (!running.length) body = <StateBlock title="No other chat is running" />;
  else {
    body = (
      <Field label="Chat" htmlFor="send-after-chat">
        <Select id="send-after-chat" value={chosen} onChange={(e) => setPicked(e.target.value)}>
          {running.map((t) => <option key={t.id} value={t.id}>{t.title || "Untitled chat"}</option>)}
        </Select>
      </Field>
    );
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Send after another chat"
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!chosen}
            onClick={() => onPick({ kind: "after_thread", thread_id: chosen })}>Queue</Button>
        </>
      }
    >
      {body}
    </Modal>
  );
}
