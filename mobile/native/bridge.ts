// RN-side dispatcher. Receives BridgeRequests from the WebView (onMessage),
// routes them to the capability modules, and injects the BridgeResponse back
// into the page. Mirror of the injected client in inject.ts; both are generated
// from / validated against the shared contract.

import type WebView from "react-native-webview";
import {
  isBridgeRequest,
  RECV_FN,
  type BridgeEvent,
  type BridgeEventTopic,
  type BridgeResponse,
  type Capability,
} from "@shared/native-bridge";

import * as audio from "./audio";
import * as liveActivity from "./liveActivity";
import * as location from "./location";
import * as notify from "./notify";
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
  liveActivity: {
    isSupported: liveActivity.isSupported,
    start: liveActivity.start,
    update: liveActivity.update,
    end: liveActivity.end,
  },
  secureStore: {
    get: secureStore.get,
    set: secureStore.set,
    remove: secureStore.remove,
  },
};

export interface Dispatcher {
  onMessage(raw: string): Promise<void>;
  emit(topic: BridgeEventTopic, payload: unknown): void;
}

export function createDispatcher(getWebView: () => WebView | null): Dispatcher {
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
    if (!isBridgeRequest(msg)) return;

    const { id, cap, method, args } = msg;
    const handler = ROUTES[cap]?.[method];
    if (!handler) {
      send({ t: "res", id, ok: false, error: `unknown method ${cap}.${method}` });
      return;
    }
    try {
      const result = await handler(...args);
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
