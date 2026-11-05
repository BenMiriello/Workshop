import { useState } from "react";
import { ChevronLeft } from "lucide-react";
import { Button, ConfirmDialog, Modal } from "@workshop/ui";
import { scheduleApi, type Schedule } from "../../lib/chatSchedules";
import ScheduleForm from "./ScheduleForm";
import { ViewActions } from "./scheduleActs";
import ScheduledList from "./ScheduledList";
import ScheduleView from "./ScheduleView";
import { againFormOf, formBody, formOf, type ScheduleForm as Form } from "./scheduleFields";
import { nameOf, type Act, type Group } from "./schedules";
import { useRefreshScheduled, useScheduled } from "./useScheduled";

// SCHEDULED: everything set to send into this chat (ScheduledList), each
// schedule opening to a view of its own (ScheduleView) and its form
// (ScheduleForm). Back returns to where the view was opened from. Every button
// in a schedule's view acts on that schedule; the dialog closes from outside.

type Mode = "new" | "copy" | "edit" | "again";
type View =
  | { kind: "list" }
  | { kind: "one"; id: string }
  | { kind: "edit"; mode: Mode; id: string | null; back: View };

const FORM_TITLE: Record<Mode, string> = {
  new: "New schedule", copy: "New schedule", edit: "Edit schedule", again: "Run again",
};
const FORM_SAVE: Record<Mode, string> = { new: "Create", copy: "Create", edit: "Save", again: "Start" };

export default function SchedulesDialog({ threadId, onClose, newText, onCreated }: {
  threadId: string;
  onClose: () => void;
  /** A schedule was made from what was typed. */
  onCreated?: () => void;
  /** Opened from Repeat: a new schedule of what was typed. */
  newText?: string;
}) {
  const scheduled = useScheduled(threadId);
  const refresh = useRefreshScheduled(threadId);
  const [view, setView] = useState<View>(newText !== undefined
    ? { kind: "edit", mode: "new", id: null, back: { kind: "list" } } : { kind: "list" });
  const [form, setForm] = useState<Form>(() => formOf(null, newText ?? ""));
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Group>("active");
  const [confirm, setConfirm] = useState<null | { kind: "schedule" | "wake"; id: string }>(null);
  const items = scheduled.data?.schedules ?? [];
  const one = view.kind !== "list" && view.id ? items.find((s) => s.id === view.id) : undefined;

  const go = (next: View) => { setError(undefined); setView(next); };
  const act = async (run: () => Promise<unknown>, next?: View) => {
    setBusy(true); setError(undefined);
    try { await run(); await refresh(); if (next) setView(next); }
    catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const open = (mode: Mode, s: Schedule | null) => {
    setForm(s && mode !== "edit" ? againFormOf(s) : formOf(s));
    go({ kind: "edit", mode, id: mode === "edit" || mode === "again" ? s?.id ?? null : null, back: view });
  };
  const onAct = (s: Schedule, a: Act) => {
    if (a === "send") void act(() => scheduleApi.sendNow(threadId, s.id));
    else if (a === "again") void act(() => scheduleApi.sendAgain(threadId, s.id));
    else if (a === "toggle") {
      void act(() => scheduleApi.change(threadId, s.id, { status: s.status === "active" ? "paused" : "active" }));
    } else if (a === "end") void act(() => scheduleApi.change(threadId, s.id, { status: "done", why: "ended by you" }));
    else if (a === "edit") open("edit", s);
    else if (a === "duplicate") open("copy", s);
    else if (a === "rearm") open("again", s);
    else setConfirm({ kind: "schedule", id: s.id });
  };
  const save = () => {
    if (view.kind !== "edit") return;
    const got = formBody(form);
    if (!got.body) { setError(got.error); return; }
    const body = got.body;
    const { mode, id, back } = view;
    void act(async () => {
      const s = mode === "edit" && id
        ? await scheduleApi.change(threadId, id, one?.driver === "app"
          ? { start_at: body.start_at, until_at: body.until_at, max_sends: body.max_sends } : body)
        : mode === "again" && id ? await scheduleApi.rearm(threadId, id, body)
          : await scheduleApi.create(threadId, body);
      if (mode === "new") onCreated?.();
      setView(mode === "edit" && back.kind === "list" ? back : { kind: "one", id: s.id });
    });
  };
  const back = (): View => (view.kind === "edit" ? view.back : { kind: "list" });

  let title = "Scheduled";
  let content;
  let actions;
  if (view.kind === "edit") {
    title = FORM_TITLE[view.mode];
    content = <ScheduleForm value={form} onChange={setForm} error={error}
      fromApp={view.mode === "edit" && one?.driver === "app"} />;
    actions = (
      <>
        <Button variant="ghost" onClick={() => (view.mode !== "new" || newText === undefined ? go(back()) : onClose())}>
          Cancel
        </Button>
        <Button variant="primary" disabled={busy} onClick={save}>{FORM_SAVE[view.mode]}</Button>
      </>
    );
  } else if (view.kind === "one" && one) {
    title = nameOf(one);
    content = <ScheduleView threadId={threadId} s={one} error={error} busy={busy} onSendNow={() => onAct(one, "send")} />;
    actions = <ViewActions s={one} busy={busy} onAct={onAct} />;
  } else {
    content = <ScheduledList state={scheduled} tab={tab} onTab={setTab} busy={busy} error={error} onAct={onAct}
      onOpen={(id) => go({ kind: "one", id })} onShowWaiting={onClose}
      onCancelWake={(id) => setConfirm({ kind: "wake", id })} />;
    actions = (
      <>
        <Button variant="ghost" onClick={onClose}>Close</Button>
        <Button variant="primary" onClick={() => open("new", null)}>New schedule</Button>
      </>
    );
  }
  return (
    <>
      <Modal open tall size="lg" onClose={onClose} actions={actions} title={view.kind === "list" ? title : (
        <span className="chat-sched-title">
          <button type="button" className="chat-sched-back" aria-label="Back" onClick={() => go(back())}>
            <ChevronLeft size={16} aria-hidden />
          </button>
          <span className="chat-sched-title__text">{title}</span>
        </span>
      )}>
        {content}
      </Modal>
      <ConfirmDialog
        open={!!confirm}
        onCancel={() => setConfirm(null)}
        title={confirm?.kind === "wake" ? "Cancel this wake-up?" : "Delete this schedule?"}
        body={confirm?.kind === "wake" ? "The chat will not be woken at that time. This cannot be undone."
          : "It sends nothing more. This cannot be undone."}
        confirmLabel={confirm?.kind === "wake" ? "Cancel wake-up" : "Delete"}
        busy={busy}
        onConfirm={() => {
          const c = confirm;
          setConfirm(null);
          if (c) void act(() => (c.kind === "wake" ? scheduleApi.cancelWakeUp(threadId, c.id)
            : scheduleApi.remove(threadId, c.id)), { kind: "list" });
        }}
      />
    </>
  );
}
