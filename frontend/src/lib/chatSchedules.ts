import { HttpError, shortHttpError } from "@workshop/ui";
import type { components } from "../api/schema.gen";

// What is set to send into a chat (backend routes/chat_schedules): its
// schedules, the messages waiting on a rule, the chat's own wake-ups.

export type Schedule = components["schemas"]["Schedule"];
export type Scheduled = components["schemas"]["Scheduled"];
export type WakeUp = components["schemas"]["WakeUp"];
export type ScheduleIn = components["schemas"]["ScheduleIn"];
export type SchedulePatch = components["schemas"]["SchedulePatch"];
export type RearmIn = components["schemas"]["RearmIn"];
export type ScheduleSend = components["schemas"]["Send"];

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    throw new HttpError(r.status, shortHttpError(r.status, r.statusText, await r.text().catch(() => "")));
  }
  return r.json() as Promise<T>;
}

const base = (threadId: string) => `/api/chat/threads/${encodeURIComponent(threadId)}`;
const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

export const scheduleApi = {
  list: (threadId: string) => fetch(`${base(threadId)}/schedules`).then(j<Scheduled>),
  create: (threadId: string, body: ScheduleIn) =>
    fetch(`${base(threadId)}/schedules`, json("POST", body)).then(j<Schedule>),
  change: (threadId: string, id: string, body: SchedulePatch) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}`, json("PATCH", body))
      .then(j<Schedule>),
  remove: (threadId: string, id: string) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then(j<Schedule>),
  sendNow: (threadId: string, id: string) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}/send`, json("POST", {}))
      .then(j<Schedule>),
  /** An ended schedule's message once more; it stays ended. */
  sendAgain: (threadId: string, id: string) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}/again`, { method: "POST" })
      .then(j<Schedule>),
  /** Start an ended schedule over with this rule. */
  rearm: (threadId: string, id: string, body: RearmIn) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}/rearm`, json("POST", body))
      .then(j<Schedule>),
  sends: (threadId: string, id: string) =>
    fetch(`${base(threadId)}/schedules/${encodeURIComponent(id)}/sends`)
      .then(j<components["schemas"]["Sends"]>),
  cancelWakeUp: (threadId: string, id: string) =>
    fetch(`${base(threadId)}/wakeups/${encodeURIComponent(id)}`, { method: "DELETE" })
      .then(j<WakeUp>),
};
