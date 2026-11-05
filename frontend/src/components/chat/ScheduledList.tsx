import type { ReactNode } from "react";
import {
  AlarmClock, CalendarClock, ChevronRight, Clock, MessageSquareReply, MoreHorizontal, Repeat,
} from "lucide-react";
import { Button, Menu, Notice, Skeleton, StateBlock, Tabs } from "@workshop/ui";
import type { Schedule } from "../../lib/chatSchedules";
import { ActItems } from "./scheduleActs";
import { actsOf, inGroup, nameOf, rowMeta, upcomingOf, type Act, type Group } from "./schedules";
import { whenText } from "./sendRules";
import type { useScheduled } from "./useScheduled";

// What is set to send into this chat, one tab per state: Active (soonest
// first, the chat's wake-ups among them), Paused and Ended. Each row opens its
// schedule; its menu acts on it in place.

const KIND_ICON: Record<Schedule["repeat"], ReactNode> = {
  after_reply: <MessageSquareReply size={16} aria-hidden />,
  every: <Repeat size={16} aria-hidden />,
  once: <CalendarClock size={16} aria-hidden />,
};

const EMPTY: Record<Group, string> = {
  active: "Nothing is active",
  paused: "Nothing is paused",
  ended: "Nothing has ended",
};

export default function ScheduledList({ state, tab, onTab, onOpen, onAct, onCancelWake, onShowWaiting, error, busy }: {
  state: ReturnType<typeof useScheduled>;
  tab: Group;
  onTab: (tab: Group) => void;
  onOpen: (id: string) => void;
  onAct: (s: Schedule, act: Act) => void;
  onCancelWake: (id: string) => void;
  /** The messages set to send later are under the chat: close to see them. */
  onShowWaiting: () => void;
  error?: string;
  busy: boolean;
}) {
  if (state.isLoading) return <><Skeleton width="70%" /><Skeleton width="50%" /></>;
  if (state.isError) {
    return <StateBlock variant="error" title="Could not load what is scheduled"
      hint="GET /api/chat/threads/<id>/schedules failed."
      action={<Button size="sm" onClick={() => void state.refetch()}>Retry</Button>} />;
  }
  const { schedules = [], wakeups = [], waiting = 0 } = state.data ?? {};
  const upcoming = upcomingOf(schedules, wakeups);
  const paused = inGroup(schedules, "paused");
  const ended = inGroup(schedules, "ended");
  const counts: Record<Group, number> = {
    active: upcoming.length + (waiting ? 1 : 0), paused: paused.length, ended: ended.length,
  };
  const label = (g: Group, name: string) => (counts[g] ? `${name} (${counts[g]})` : name);
  const row = (s: Schedule) => <ScheduleRow key={s.id} s={s} busy={busy} onOpen={onOpen} onAct={onAct} />;
  return (
    <div className="chat-sched-list">
      <Tabs value={tab} onChange={onTab} items={[
        { value: "active", label: label("active", "Active") },
        { value: "paused", label: label("paused", "Paused") },
        { value: "ended", label: label("ended", "Ended") },
      ]} />
      {!counts[tab] ? (
        <StateBlock title={EMPTY[tab]}
          hint={tab === "active" ? "New schedule sends a message after each reply, every so often, or once." : undefined} />
      ) : (
        <div className="chat-sched-rows">
          {tab === "active" && upcoming.map((u) => (u.kind === "schedule" ? row(u.s) : (
            <div key={u.w.id} className="chat-sched-row">
              <div className="chat-sched-row__main">
                <span className="chat-sched-row__icon"><AlarmClock size={16} aria-hidden /></span>
                <RowText name={u.w.reason || "Wake up"} meta={`Wake-up set by this chat · ${whenText(u.w.due_at)}`} />
              </div>
              <Button size="sm" variant="ghost" onClick={() => onCancelWake(u.w.id)}>Cancel</Button>
            </div>
          )))}
          {tab === "active" && waiting > 0 && (
            <div className="chat-sched-row">
              <button type="button" className="chat-sched-row__main" onClick={onShowWaiting}>
                <span className="chat-sched-row__icon"><Clock size={16} aria-hidden /></span>
                <RowText meta="Shown under the chat"
                  name={waiting === 1 ? "1 message set to send later" : `${waiting} messages set to send later`} />
                <ChevronRight size={16} aria-hidden className="chat-sched-row__go" />
              </button>
            </div>
          )}
          {tab === "paused" && paused.map(row)}
          {tab === "ended" && ended.map(row)}
        </div>
      )}
      {error && <Notice tone="error">{error}</Notice>}
    </div>
  );
}

function RowText({ name, meta }: { name: string; meta: string }) {
  return (
    <span className="chat-sched-row__text">
      <span className="chat-sched-row__name">{name}</span>
      <span className="chat-sched-row__meta">{meta}</span>
    </span>
  );
}

function ScheduleRow({ s, busy, onOpen, onAct }: {
  s: Schedule;
  busy: boolean;
  onOpen: (id: string) => void;
  onAct: (s: Schedule, act: Act) => void;
}) {
  return (
    <div className="chat-sched-row">
      <button type="button" className="chat-sched-row__main" onClick={() => onOpen(s.id)}>
        <span className="chat-sched-row__icon">{KIND_ICON[s.repeat]}</span>
        <RowText name={nameOf(s)} meta={rowMeta(s)} />
      </button>
      <Menu label={`Actions for ${nameOf(s)}`} trigger={
        <button type="button" className="chat-sched-row__more" aria-label="More actions">
          <MoreHorizontal size={16} aria-hidden />
        </button>
      }>
        <ActItems s={s} acts={actsOf(s)} busy={busy} onAct={onAct} />
      </Menu>
    </div>
  );
}
