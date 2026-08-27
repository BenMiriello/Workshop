// Drives native commute features from the existing /commute/status brief:
//   - a local "leave now" notification at the leave-by time (FREE)
//   - a local-update Live Activity countdown on the lock screen (FREE)
//
// No-op in a plain browser. Mounted once from the Commute app wrapper. This is
// the bridge between the backend's commute brain and iOS — it adds NO new
// backend endpoints; it reads the same status the web UI already shows.

import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import type { CommuteActivityState, ActivityStatus } from "@shared/native-bridge";
import { native } from "@workshop/ui";
import { commuteStatusQuery } from "./queries";

interface Leg {
  mode?: string;
  from?: string;
  to?: string;
  preferred_train?: string | null;
}

interface CommuteBrief {
  active?: boolean;
  direction?: "to-work" | "to-home";
  schedule_id?: string;
  leave_by?: string | null;
  leave_by_local?: string | null;
  target_arrive?: string | null;
  target_arrive_local?: string | null;
  late_risk?: boolean;
  current_leg?: Leg | null;
  next_leg?: Leg | null;
  next_schedule?: {
    schedule_id?: string;
    direction?: string;
    leave_by?: string;
    leave_by_local?: string;
    target_arrive_local?: string;
  } | null;
}

const POLL_MS = 60_000;

function legLabel(brief: CommuteBrief): string {
  const leg = brief.current_leg ?? brief.next_leg ?? null;
  if (!leg) return brief.direction === "to-home" ? "Heading home" : "Heading to work";
  const train = leg.preferred_train ? ` ${leg.preferred_train}` : "";
  return `${(leg.mode ?? "transit").replace(/^\w/, (c) => c.toUpperCase())}${train}`;
}

function legRoute(brief: CommuteBrief): string {
  const leg = brief.current_leg ?? brief.next_leg ?? null;
  if (!leg?.from || !leg?.to) return "";
  return `${leg.from} to ${leg.to}`;
}

function toActivityState(brief: CommuteBrief): CommuteActivityState | null {
  const countdownTo = brief.leave_by ?? brief.target_arrive ?? null;
  if (!countdownTo || !brief.direction) return null;
  const status: ActivityStatus = brief.late_risk ? "urgent" : "ok";
  return {
    direction: brief.direction,
    headline: legLabel(brief),
    subtitle: legRoute(brief),
    leaveByLocal: brief.leave_by_local ?? "",
    targetArriveLocal: brief.target_arrive_local ?? "",
    countdownTo,
    status,
  };
}

/**
 * WHAT THE PHONE SHOULD BE SHOWING, derived from a brief. Everything the side
 * effects depend on is in here and nothing else is, which is what makes "has
 * anything actually changed?" a question with an answer.
 */
type NativePlan = {
  /** The notification to have pending, or none. `key` identifies the fact it
   *  is about, so re-deriving the same plan re-schedules nothing. */
  notify: { key: string; fireAt: string; title: string; body: string } | null;
  /** The Live Activity to be showing, or none (which ends a running one). */
  activity: CommuteActivityState | null;
};

function nativePlan(brief: CommuteBrief): NativePlan {
  if (brief.active) {
    const notify = brief.leave_by && brief.schedule_id
      ? {
          key: `${brief.schedule_id}:${brief.leave_by}`,
          fireAt: brief.leave_by,
          title: "Time to leave",
          body: `Leave now${brief.target_arrive_local ? ` to arrive by ${brief.target_arrive_local}` : ""}.`,
        }
      : null;
    return { notify, activity: toActivityState(brief) };
  }
  const ns = brief.next_schedule;
  if (ns?.leave_by) {
    return {
      notify: {
        key: `${ns.schedule_id}:${ns.leave_by}`,
        fireAt: ns.leave_by,
        title: "Upcoming commute",
        body: `Leave by ${ns.leave_by_local ?? "soon"}${ns.target_arrive_local ? ` to arrive ${ns.target_arrive_local}` : ""}.`,
      },
      activity: null,
    };
  }
  return { notify: null, activity: null };
}

export function useCommuteNative(): void {
  const activityId = useRef<string | null>(null);
  const scheduledKey = useRef<string | null>(null);
  // Installed before page scripts run (see native/useNative.ts), stable for the
  // document's lifetime. Null in a plain browser, which is what gates the read.
  const n = native();

  // THE SAME CACHE ENTRY THE STATUS TAB READS. This used to be its own fetch on
  // its own 60s timer with its own copy of the brief, so a phone with the app
  // open made two requests for one payload every minute and the two copies
  // could disagree. `whileHidden` is the old `whenHidden: "continue"`, and it
  // is the one thing about this reader that is genuinely different: the
  // notification and the Live Activity exist FOR the minutes when nobody is
  // looking at the screen, so this reader keeps ticking while the app is
  // hidden. A reader that only paints pixels does not.
  const q = useQuery(commuteStatusQuery<CommuteBrief>({
    enabled: !!n,
    refetchMs: POLL_MS,
    whileHidden: true,
  }));
  const brief = q.data;

  // ONCE PER REAL CHANGE — NOT ONCE PER RENDER, NOT ONCE PER FETCH.
  //
  // This payload drives effects that cannot be taken back: it schedules a phone
  // notification and starts, updates or ends a lock-screen Live Activity. Now
  // that it shares a cache entry with the page, this component re-renders
  // whenever ANY reader refetches, including the Status tab's 30s tick, so
  // running the effects from a plain render effect would fire them at somebody
  // else's cadence.
  //
  // THE PAYLOAD'S OWN IDENTITY IS NOT THE SIGNAL, and this is the trap worth
  // naming: Query's structural sharing keeps the previous object when a refetch
  // is deeply equal, but `/commute/status` stamps every response with `ts`
  // (`scoring.evaluate`), so no two responses are ever equal and `q.data` is a
  // new object every single time. Depending on it — or on `dataUpdatedAt`,
  // which moves for the same reason — would fire the effects twice a minute
  // forever.
  //
  // So the dependency is the VALUE of the plan: what the phone should be
  // showing, serialised. A brief whose projected arrival crept by a minute
  // produces the same plan and does nothing. `planRef` carries the object the
  // effect acts on; `planKey` decides when it acts, and the two cannot
  // disagree because the ref is written in the same render that computed the
  // key.
  const plan = useMemo(() => (brief ? nativePlan(brief) : null), [brief]);
  const planKey = plan ? JSON.stringify(plan) : "";
  const planRef = useRef<NativePlan | null>(plan);
  planRef.current = plan;

  // Serialises the runs. Each is chained onto the one before it, so two changes
  // landing close together cannot both find `activityId` empty and both call
  // `start()`, which is how one commute ends up with two Live Activities on the
  // lock screen.
  const queue = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    const current = planRef.current;
    if (!n || !current) return;

    const apply = async () => {
      // `scheduledKey` is not belt-and-braces: an activity-only change (the
      // train got later, the leave-by did not) re-runs this effect, and the
      // pending notification must not be re-scheduled for it.
      if (current.notify && n.has("localNotify") && scheduledKey.current !== current.notify.key) {
        scheduledKey.current = current.notify.key;
        void n.localNotify.schedule({
          id: `commute-leave`,
          title: current.notify.title,
          body: current.notify.body,
          fireAt: current.notify.fireAt,
        }).catch(() => {});
      }

      if (!n.has("liveActivity") || !(await n.liveActivity.isSupported())) return;
      try {
        if (current.activity) {
          if (activityId.current) {
            await n.liveActivity.update(activityId.current, current.activity);
          } else {
            const h = await n.liveActivity.start(current.activity);
            activityId.current = h.id;
          }
        } else if (activityId.current) {
          await n.liveActivity.end(activityId.current);
          activityId.current = null;
        }
      } catch {
        /* Live Activity not built into this dev build yet — ignore. */
      }
    };

    queue.current = queue.current.then(apply).catch(() => {
      /* transient; the next changed plan retries */
    });
  }, [n, planKey]);
}
