// Drives native commute features from the existing /commute/status brief:
//   - a local "leave now" notification at the leave-by time (FREE)
//   - a local-update Live Activity countdown on the lock screen (FREE)
//
// No-op in a plain browser. Mounted once from the Commute app wrapper. This is
// the bridge between the backend's commute brain and iOS — it adds NO new
// backend endpoints; it reads the same status the web UI already shows.

import { useEffect, useRef } from "react";
import type { CommuteActivityState, ActivityStatus } from "@shared/native-bridge";
import { native } from "../../../native/bridge";

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

export function useCommuteNative(): void {
  const activityId = useRef<string | null>(null);
  const scheduledKey = useRef<string | null>(null);

  useEffect(() => {
    const n = native();
    if (!n) return; // browser: nothing to do

    let alive = true;

    const scheduleLeave = (key: string, fireAt: string, title: string, body: string) => {
      if (!n.has("localNotify") || scheduledKey.current === key) return;
      scheduledKey.current = key;
      void n.localNotify.schedule({ id: `commute-leave`, title, body, fireAt }).catch(() => {});
    };

    const syncActivity = async (state: CommuteActivityState | null) => {
      if (!n.has("liveActivity") || !(await n.liveActivity.isSupported())) return;
      try {
        if (state) {
          if (activityId.current) {
            await n.liveActivity.update(activityId.current, state);
          } else {
            const h = await n.liveActivity.start(state);
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

    const tick = async () => {
      try {
        const r = await fetch("/api/commute/status");
        if (!r.ok || !alive) return;
        const brief = (await r.json()) as CommuteBrief;

        if (brief.active) {
          if (brief.leave_by && brief.schedule_id) {
            scheduleLeave(
              `${brief.schedule_id}:${brief.leave_by}`,
              brief.leave_by,
              "Time to leave",
              `Leave now${brief.target_arrive_local ? ` to arrive by ${brief.target_arrive_local}` : ""}.`,
            );
          }
          await syncActivity(toActivityState(brief));
        } else if (brief.next_schedule?.leave_by) {
          const ns = brief.next_schedule;
          scheduleLeave(
            `${ns.schedule_id}:${ns.leave_by}`,
            ns.leave_by!,
            "Upcoming commute",
            `Leave by ${ns.leave_by_local ?? "soon"}${ns.target_arrive_local ? ` to arrive ${ns.target_arrive_local}` : ""}.`,
          );
          await syncActivity(null);
        } else {
          await syncActivity(null);
        }
      } catch {
        /* transient; next tick retries */
      }
    };

    void tick();
    const h = setInterval(() => void tick(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, []);
}
