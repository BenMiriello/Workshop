// The Mac app's paste panel (shared/native-bridge.ts PANEL_CHANNEL): which
// overlay app it shows, and how its page talks to the panel.

import { PANEL_CHANNEL, type PanelMessage } from "@shared/native-bridge";
import type { OverlayApp } from "./store";

/** The event an app subscribes to when it keeps copies. */
const KEEPS_COPIES = "copied";

/** The app the panel shows: the one that keeps copies, else the first. */
export function panelApp(apps: readonly OverlayApp[]): OverlayApp | null {
  return apps.find((a) => a.events.includes(KEEPS_COPIES)) ?? apps[0] ?? null;
}

type Handler = { postMessage(m: PanelMessage): void };

/** The panel's message handler; null outside the Mac app's panel. */
function handler(): Handler | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { webkit?: { messageHandlers?: Record<string, Handler | undefined> } };
  return w.webkit?.messageHandlers?.[PANEL_CHANNEL] ?? null;
}

export const nativePanel = {
  available(): boolean {
    return handler() !== null;
  },
  /** False outside the panel, where there is no app to paste into. */
  send(m: PanelMessage): boolean {
    const h = handler();
    if (!h) return false;
    h.postMessage(m);
    return true;
  },
};
