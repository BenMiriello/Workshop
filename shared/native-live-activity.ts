// Contract for Live Activities (lock screen and Dynamic Island), part of the
// Workshop native bridge (re-exported by `shared/native-bridge.ts`, which owns
// the wire model).
//
// One generic activity serves every app: the shell lays out the lock screen,
// the Dynamic Island's compact, expanded and minimal views from the fields of
// `LiveActivityState`, and nothing in it knows what an app shows. An activity
// belongs to the app that started it (`app` on the wire, set by
// `WorkshopNative.forApp`, which `liveActivities()` in the kit calls with the
// slug of the app its frame serves): only that app may update or end it, and
// a tap on it opens that app. A start with no app is refused. As with page
// views, the id is the caller's claim, so this keeps apps apart by accident,
// not on purpose.
//
// Native side: mobile/modules/workshop-live-activity (the attributes type and
// the app-side module), mobile/targets/WorkshopWidgets (the layout) and
// mobile/native/liveActivity.ts (the ownership check).

export const LIVE_ACTIVITY_METHODS = ["isSupported", "start", "update", "end"] as const;

/** The kit's five status kinds (packages/ui/src/status.ts). */
export type ActivityStatus = "ok" | "running" | "stopped" | "warn" | "danger";

export const ACTIVITY_STATUSES: readonly ActivityStatus[] = [
  "ok", "running", "stopped", "warn", "danger",
];

/**
 * What a Live Activity shows. The countdown and the progress are drawn
 * natively, so a countdown keeps running without further updates; push a new
 * state only when what it says changes.
 */
export interface LiveActivityState {
  headline: string;
  subtitle?: string;
  /** A third, smaller line. */
  detail?: string;
  /** ISO 8601 instant the native timer counts down to. */
  countdownTo?: string;
  /** 0 to 1; values outside are clamped. */
  progress?: number;
  status: ActivityStatus;
  /** An SF Symbol name, e.g. "timer". */
  symbol: string;
}

export interface LiveActivityHandle {
  /** Opaque id used to update or end this activity. */
  id: string;
}

export interface LiveActivityApi {
  isSupported(): Promise<boolean>;
  /** Needs the bridge from `forApp`: the activity is that app's. */
  start(state: LiveActivityState): Promise<LiveActivityHandle>;
  /** Rejects for an activity that is gone or another app's. */
  update(id: string, state: LiveActivityState): Promise<void>;
  end(id: string): Promise<void>;
}
