// Web-side access to Live Activities (the `liveActivity` capability of the
// Workshop mobile shell; contract in shared/native-live-activity.ts).
//
// An activity belongs to the app that started it: only that app may update
// or end it, and a tap on it opens that app. So the API here is the one
// acting for the app this frame serves (./appBridge.ts), and there is none
// outside an app's served pages.

import { forFrameApp, reachBridge } from "./appBridge";
import type { LiveActivityApi } from "./types";

/**
 * Live Activities for this frame's app, from this window or a same-origin
 * ancestor. Undefined in a plain browser, in a shell without Live Activities
 * or from before they were per app, and outside an app's served pages. Check
 * `isSupported()` before `start`: the system setting can turn them off.
 */
export function liveActivities(): LiveActivityApi | undefined {
  const found = reachBridge("liveActivity");
  return found ? forFrameApp(found.bridge)?.liveActivity : undefined;
}
