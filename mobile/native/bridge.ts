// RN-side dispatcher. Receives BridgeRequests from the WebView (onMessage),
// routes them to the capability modules, and injects the BridgeResponse back
// into the page. Mirror of the injected client in inject.ts; both are generated
// from / validated against the shared contract.

import type WebView from "react-native-webview";
import {
  APP_SCOPED_CAPS,
  isBridgeHello,
  isBridgeRequest,
  RECV_FN,
  type BridgeEvent,
  type BridgeEventTopic,
  type BridgeResponse,
  type Capability,
} from "@shared/native-bridge";

import * as audio from "./audio";
import * as clipboard from "./clipboard";
import * as liveActivity from "./liveActivity";
import * as location from "./location";
import * as mic from "./mic";
import * as notify from "./notify";
import * as pages from "./pages";
import * as secureStore from "./secureStore";
import * as speech from "./speech";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Handler = (...args: any[]) => Promise<unknown> | unknown;

const ROUTES: Record<Capability, Record<string, Handler>> = {
  location: {
    getCurrent: location.getCurrent,
    startBackgroundTracking: location.startBackgroundTracking,
    stopBackgroundTracking: location.stopBackgroundTracking,
    trackingStatus: location.trackingStatus,
  },
  audio: {
    startCapture: audio.startCapture,
    stopCapture: audio.stopCapture,
  },
  speech: {
    available: speech.available,
    transcribe: speech.transcribe,
    speak: speech.speak,
    stop: speech.stop,
  },
  localNotify: {
    schedule: notify.schedule,
    cancel: notify.cancel,
    cancelAll: notify.cancelAll,
    permissionStatus: notify.permissionStatus,
    requestPermission: notify.requestPermission,
  },
  // Each handler takes the request's `app` first, like pages below.
  liveActivity: {
    isSupported: liveActivity.isSupported,
    start: liveActivity.start,
    update: liveActivity.update,
    end: liveActivity.end,
  },
  secureStore: {
    // Web may WRITE ONLY the device token — no read, no delete, no other key.
    // Exposing get() to page JS reintroduced the exact bearer-exfil path the
    // HttpOnly cookie closes; any same-origin XSS could read the token. Native
    // producers call native/secureStore directly and keep full access.
    // (security-audit-2026-07-18 H6)
    set: (key: string, value: string) => {
      if (key !== "ws-device-token") {
        throw new Error("secureStore: web may only write ws-device-token");
      }
      return secureStore.set(key, value);
    },
  },
  // Page views are separate WKWebViews without this bridge, so nothing loaded
  // in one can call back in here. Each handler takes the request's `app`
  // first; the module refuses one without it.
  pages: {
    isSupported: pages.isSupported,
    open: pages.open,
    close: pages.close,
    closeAll: pages.closeAll,
    setRect: pages.setRect,
    show: pages.show,
    hide: pages.hide,
    bringToFront: pages.bringToFront,
    adopt: pages.adopt,
    navigate: pages.navigate,
    back: pages.back,
    forward: pages.forward,
    reload: pages.reload,
    stop: pages.stop,
    run: pages.run,
    snapshot: pages.snapshot,
    setContentMode: pages.setContentMode,
    setZoom: pages.setZoom,
    setMuted: pages.setMuted,
    setCutouts: pages.setCutouts,
    "contentRules.set": pages.setContentRules,
    "contentRules.remove": pages.removeContentRules,
    "contentRules.list": pages.listContentRules,
    "contentRules.setLifted": pages.setLiftedContentRules,
    "userScripts.set": pages.setUserScript,
    "userScripts.remove": pages.removeUserScript,
    "navigationRules.set": pages.setNavigationRules,
    "navigationRules.remove": pages.removeNavigationRules,
    "commands.set": pages.setCommands,
    "cookies.export": pages.exportCookies,
    "cookies.import": pages.importCookies,
    "profiles.list": pages.listProfiles,
    "profiles.remove": pages.removeProfile,
  },
  mic: {
    status: mic.status,
    request: mic.request,
    openSettings: mic.openSettings,
  },
  // The app in front decides which page views are drawn and playing, and the
  // web UI's own layers are cut out of every page; the phone has no menu bar
  // to route page commands with.
  shell: {
    foreground: pages.setForeground,
    overlays: pages.setOverlays,
  },
  // The watch is looked at on coming to the front; see native/clipboard.ts.
  clipboard: {
    write: clipboard.write,
    "watch.get": clipboard.watchGet,
    "watch.set": clipboard.watchSet,
  },
};

export interface Dispatcher {
  onMessage(raw: string): Promise<void>;
  emit(topic: BridgeEventTopic, payload: unknown): void;
}

/**
 * `onDocument` runs each time the injected client installs into a new top
 * document (first load, reload, full navigation), so native state that belongs
 * to the previous document can be dropped.
 */
export function createDispatcher(
  getWebView: () => WebView | null,
  onDocument?: () => void,
): Dispatcher {
  function send(obj: BridgeResponse | BridgeEvent): void {
    const wv = getWebView();
    if (!wv) return;
    wv.injectJavaScript(`window.${RECV_FN} && window.${RECV_FN}(${JSON.stringify(obj)}); true;`);
  }

  async function onMessage(raw: string): Promise<void> {
    let msg: unknown;
    try {
      msg = JSON.parse(raw);
    } catch {
      return; // not ours
    }
    if (isBridgeHello(msg)) {
      onDocument?.();
      return;
    }
    if (!isBridgeRequest(msg)) return;

    const { id, cap, method, args, app } = msg;
    const handler = ROUTES[cap]?.[method];
    if (!handler) {
      send({ t: "res", id, ok: false, error: `unknown method ${cap}.${method}` });
      return;
    }
    try {
      const result = await handler(...(APP_SCOPED_CAPS.includes(cap) ? [app ?? "", ...args] : args));
      send({ t: "res", id, ok: true, result });
    } catch (e) {
      send({ t: "res", id, ok: false, error: e instanceof Error ? e.message : String(e) });
    }
  }

  function emit(topic: BridgeEventTopic, payload: unknown): void {
    send({ t: "evt", topic, payload });
  }

  return { onMessage, emit };
}
