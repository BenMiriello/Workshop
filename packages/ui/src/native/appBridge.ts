// Finding the native bridge from an app's frame, and the bridge acting for
// that app.
//
// The shell installs its bridge on the top Workshop document only. An app the
// Workshop SPA frames on the same origin reaches it through `window.parent`.
// The shell keeps what an app makes (page views, Live Activities) apart by the
// app's id, so app-facing helpers hand back the bridge from
// `WorkshopNative.forApp`, called with the app this frame serves: its slug in
// Workshop's app routing.

import { native } from "./bridge";
import type { Capability, WorkshopNative } from "./types";

const APP_PATH = /^(?:\/api)?\/base-apps\/run\/([^/]+)\/proxy\//;

/** The app a document at `pathname` belongs to: the slug in Workshop's app
 *  routing (`/base-apps/run/<slug>/proxy/<service>/...`, under `/api` from
 *  the app's own screen). Undefined anywhere else. */
export function frameApp(pathname: string): string | undefined {
  const match = APP_PATH.exec(pathname);
  if (!match) return undefined;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return undefined;
  }
}

export interface ReachedBridge {
  bridge: WorkshopNative;
  /** Found on an ancestor window rather than this one. */
  framed: boolean;
}

/** The bridge offering `cap`, from this window or a same-origin ancestor;
 *  undefined in a plain browser or a shell without `cap`. */
export function reachBridge(cap: Capability): ReachedBridge | undefined {
  const own = native();
  if (own) return own.has(cap) ? { bridge: own, framed: false } : undefined;
  if (typeof window === "undefined") return undefined;
  try {
    let win: Window = window;
    while (win.parent !== win) {
      win = win.parent;
      const found = win.WorkshopNative;
      if (found) return found.has(cap) ? { bridge: found, framed: true } : undefined;
    }
  } catch {
    // A cross-origin ancestor: no bridge is reachable from here.
  }
  return undefined;
}

/** `bridge` acting for this document's app; undefined outside an app's served
 *  pages, or in a shell from before `forApp`. */
export function forFrameApp(bridge: WorkshopNative): WorkshopNative | undefined {
  const app = frameApp(window.location.pathname);
  return app && bridge.forApp ? bridge.forApp(app) : undefined;
}
