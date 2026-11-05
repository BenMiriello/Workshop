import { Link } from "react-router-dom";
import { Button, Notice, Prop, Props, StatusPill, type StatusKind } from "@workshop/ui";
import type { Schedule } from "../../lib/chatSchedules";
import ScheduleSends from "./ScheduleSends";
import { groupOf, nextText, ruleSentence, setByText, type Group } from "./schedules";
import { whenText } from "./sendRules";

// One schedule: where it stands and who set it, what it sends, then its rule
// in one sentence and what it has sent. Send now sits beside the next send it
// replaces.

const STATE: Record<Group, { kind: StatusKind; label: string }> = {
  active: { kind: "running", label: "Active" },
  paused: { kind: "stopped", label: "Paused" },
  ended: { kind: "ok", label: "Ended" },
};

export default function ScheduleView({ threadId, s, error, busy, onSendNow }: {
  threadId: string;
  s: Schedule;
  error?: string;
  busy: boolean;
  onSendNow: () => void;
}) {
  const group = groupOf(s);
  const state = STATE[group];
  const by = s.set_by.kind === "app" ? setByText(s) : setByText(s).toLowerCase();
  return (
    <div className="chat-sched-view">
      <div className="chat-sched-view__head">
        <StatusPill status={state.kind}>{state.label}</StatusPill>
        <span className="chat-sched-view__by">
          {"Set by "}
          {s.set_by.href ? <Link to={s.set_by.href}>{by}</Link> : by}
          {`, ${whenText(s.created_at)}`}
        </span>
      </div>
      <div className="chat-sched-message">
        {s.driver === "app" ? <span className="chat-sched-message__app">The app writes each message.</span> : s.text}
      </div>
      <Props>
        <Prop label="When">{ruleSentence(s)}</Prop>
        {group === "active" && (
          <Prop label="Next send">
            <span className="chat-sched-next">
              {nextText(s)}
              {s.driver === "workshop" && (
                <Button size="sm" variant="ghost" disabled={busy} onClick={onSendNow}>Send now</Button>
              )}
            </span>
          </Prop>
        )}
        {group !== "active" && s.why && <Prop label="Reason">{s.why}</Prop>}
        <Prop label="Sent">
          {s.max_sends ? `${s.sent} of ${s.max_sends}` : String(s.sent)}
          <ScheduleSends threadId={threadId} sid={s.id} />
        </Prop>
      </Props>
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}
