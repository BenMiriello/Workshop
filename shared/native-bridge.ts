// Canonical contract for the Workshop native bridge — the SINGLE source of
// truth shared by the web app (frontend/) and the Expo shell (mobile/).
//
// The mobile shell hosts the Workshop web app in a WebView and injects a
// `window.WorkshopNative` object before the page loads. Web code feature-detects
// that object and calls into native capabilities (background location, local
// notifications, microphone, Live Activities, secure storage); when it is
// absent (a plain browser) the web code falls back to web APIs.
//
// Wire model (see mobile/native/bridge.ts + mobile/native/inject.ts):
//   web -> native :  window.ReactNativeWebView.postMessage(JSON BridgeRequest)
//                    (plus one BridgeHello per new top document)
//   native -> web :  window.__WORKSHOP_NATIVE_RECV__(BridgeResponse | BridgeEvent)
//
// Because both packages alias `@shared/*` -> `../shared/*`, both import these
// types from `@shared/native-bridge`. Keep `CAP_METHODS` (runtime) and the
// `WorkshopNative` interface (compile-time) in sync — the injected stub builds
// its namespaces from `CAP_METHODS`, the type checker enforces the interface.
// A dotted method name ("cookies.export") becomes a nested namespace.
//
// The client lives on the top Workshop document only. An app framed by the
// Workshop SPA (same origin) reaches it as `window.parent.WorkshopNative`;
// calls, promises and `on()` callbacks work across that frame boundary.

import { LIVE_ACTIVITY_METHODS, type LiveActivityApi } from "./native-live-activity";
import { PAGES_METHODS, type PageRect, type PagesApi, type PagesEventTopic } from "./native-pages";

export * from "./native-live-activity";
export * from "./native-pages";

export const BRIDGE_VERSION = 1;

/** Name of the public global the shell installs on `window`. */
export const NATIVE_GLOBAL = "WorkshopNative";
/** Name of the global the shell calls to deliver responses/events into the web. */
export const RECV_FN = "__WORKSHOP_NATIVE_RECV__";
/** A shell that learns its `NativeEngine` only at run time sets this global
 *  before the client installs; the client moves it onto `engine`. */
export const ENGINE_GLOBAL = "__WORKSHOP_NATIVE_ENGINE__";

/** Runtime method manifest, per capability. Drives the injected stub. */
export const CAP_METHODS = {
  location: ["getCurrent", "startBackgroundTracking", "stopBackgroundTracking", "trackingStatus"],
  audio: ["startCapture", "stopCapture"],
  // On-device speech: iOS SFSpeechRecognizer (STT) + AVSpeechSynthesizer (TTS).
  // Powers voice mode's "native" provider tier — private, offline, no server.
  speech: ["available", "transcribe", "speak", "stop"],
  localNotify: ["schedule", "cancel", "cancelAll", "permissionStatus", "requestPermission"],
  // The system's microphone permission for this app: read it, ask for it with
  // the system's own prompt, and open the settings page where it is changed.
  // A web view cannot do any of the three for its host app.
  mic: ["status", "request", "openSettings"],
  // Contract in ./native-live-activity.ts.
  liveActivity: LIVE_ACTIVITY_METHODS,
  // Web-reachable surface is WRITE-ONLY (see the dispatcher, which also pins the
  // key to ws-device-token). get/remove are deliberately NOT exposed to page JS:
  // exposing get() let any script or same-origin XSS read the API bearer that the
  // HttpOnly session cookie is specifically designed to keep out of JS reach.
  // Native producers import native/secureStore directly and are unaffected.
  // (security-audit-2026-07-18 H6)
  secureStore: ["set"],
  // Native page views (iOS 17+); contract in ./native-pages.ts.
  pages: PAGES_METHODS,
  // What the web UI shows, for the shell's own routing; see `ShellApi`.
  shell: ["foreground", "overlays"],
  // The system clipboard; see `ClipboardApi`.
  clipboard: ["write", "watch.get", "watch.set"],
} as const;

export type Capability = keyof typeof CAP_METHODS;

/** Capabilities whose every call takes the calling app's id first: the
 *  dispatcher passes the request's `app`, and the module refuses an empty one
 *  where the call needs an owner. */
export const APP_SCOPED_CAPS: readonly Capability[] = ["pages", "liveActivity"];

/** What `forApp` accepts as an app id: a slug in Workshop's app routing. */
export const APP_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

/** The shell hosting the web UI: the Expo app on iPhone and iPad, or the
 *  AppKit app on a Mac (macos/). */
export type NativePlatform = "ios" | "macos";

/** The shell's web engine, for web code that builds a user agent or allows
 *  for an engine version. Each field is left out where the shell cannot tell
 *  it cheaply. */
export interface NativeEngine {
  /** WebKit's own build version, e.g. "20621.1.15.11.10". */
  webkitVersion?: string;
  /** The Safari release this WebKit is, e.g. "26.0": the installed Safari
   *  on a Mac, the OS release on iOS. */
  safariVersion?: string;
}

// ---------------------------------------------------------------------------
// Data shapes
// ---------------------------------------------------------------------------

export interface LocationFix {
  lat: number;
  lng: number;
  accuracy_m?: number;
  altitude_m?: number;
  heading_deg?: number;
  speed_mps?: number;
  /** ISO 8601 timestamp from the device clock. */
  client_ts?: string;
}

export type TrackingMode = "active" | "significant";

export interface BackgroundTrackingOptions {
  /** Minimum movement (metres) before a new fix is reported. */
  distanceM?: number;
  /**
   * "active"      — continuous standard updates (a trip in progress; higher battery).
   * "significant" — significant-change / low-power (default; mirrors OwnTracks).
   */
  mode?: TrackingMode;
}

export interface TrackingStatus {
  tracking: boolean;
  mode?: TrackingMode;
  /** Last fix this session, if any. */
  last?: LocationFix;
  permission: PermissionState;
  /** Fixes persisted to the offline backlog, awaiting re-send (0 when caught up). */
  queued?: number;
}

export type PermissionState = "granted" | "denied" | "undetermined" | "restricted";

export interface LocalNotification {
  /** Stable id so the same logical alert can be rescheduled/cancelled. */
  id: string;
  title: string;
  body: string;
  /** ISO 8601 fire time. Past times fire immediately. */
  fireAt: string;
  data?: Record<string, unknown>;
}

export interface AudioCaptureOptions {
  maxSeconds: number;
  /** If set, the clip is POSTed here (relative to the API base) on stop. */
  uploadPath?: string;
}

export interface AudioCaptureResult {
  /** Local file URI of the recording. */
  uri: string;
  durationSec: number;
  uploaded?: boolean;
}

export interface SpeechAvailability {
  /** On-device speech recognition usable (permission + locale installed). */
  stt: boolean;
  /** Speech synthesis usable. */
  tts: boolean;
}

export interface SpeechTranscribeOptions {
  /** BCP-47 locale, e.g. "en-US". */
  locale?: string;
  /** Prefer on-device recognition (no audio leaves the phone). Default true. */
  onDevice?: boolean;
}

export interface SpeechResult {
  text: string;
}

export interface SpeechSpeakOptions {
  /** Named platform voice id, if the caller picked one. */
  voice?: string;
  /** 0.5..2.0, 1.0 = normal. */
  rate?: number;
  pitch?: number;
}

// ---------------------------------------------------------------------------
// Public API surface (compile-time mirror of CAP_METHODS)
// ---------------------------------------------------------------------------

export interface WorkshopNative {
  readonly bridgeVersion: number;
  readonly platform: NativePlatform;
  /** "paid" only when the Apple Developer Program entitlements are present. */
  readonly tier: "free" | "paid";
  /** Absent in shell builds from before it existed. */
  readonly engine?: NativeEngine;

  /** True when `cap` is available in this build/tier. */
  has(cap: Capability): boolean;

  /** Subscribe to pushed events (e.g. background location fixes). Returns an unsubscribe fn. */
  on<T = unknown>(topic: BridgeEventTopic, cb: (payload: T) => void): () => void;

  /**
   * This bridge acting for app `app` (an app slug): every request carries it,
   * and `on` passes only that app's "pages:*" events. Page views and Live
   * Activities are kept per app by it. The same object for the same app.
   * Absent in shell builds from before it existed.
   */
  forApp?(app: string): WorkshopNative;

  location: {
    getCurrent(): Promise<LocationFix>;
    startBackgroundTracking(opts?: BackgroundTrackingOptions): Promise<void>;
    stopBackgroundTracking(): Promise<void>;
    trackingStatus(): Promise<TrackingStatus>;
  };

  audio: {
    startCapture(opts: AudioCaptureOptions): Promise<void>;
    stopCapture(): Promise<AudioCaptureResult>;
  };

  speech: {
    available(): Promise<SpeechAvailability>;
    /** Start on-device recognition; resolves with the final transcript when the
     *  recognizer endpoints (the user stops speaking). */
    transcribe(opts?: SpeechTranscribeOptions): Promise<SpeechResult>;
    /** Speak one utterance; resolves when it finishes (so callers can chain). */
    speak(text: string, opts?: SpeechSpeakOptions): Promise<void>;
    /** Stop any in-flight synthesis and abort recognition. */
    stop(): Promise<void>;
  };

  localNotify: {
    schedule(n: LocalNotification): Promise<void>;
    cancel(id: string): Promise<void>;
    cancelAll(): Promise<void>;
    permissionStatus(): Promise<PermissionState>;
    /** Prompt for notification permission (once); resolves to the resulting state. */
    requestPermission(): Promise<PermissionState>;
  };

  mic: MicApi;

  liveActivity: LiveActivityApi;

  secureStore: {
    /** Web may only persist the device token; the Keychain is not readable from
     *  page JS (security-audit-2026-07-18 H6). */
    set(key: "ws-device-token", value: string): Promise<void>;
  };

  pages: PagesApi;

  shell: ShellApi;

  clipboard: ClipboardApi;
}

/** Which copies on the computer the shell reports: those made in Workshop's
 *  own web UI (the page reports them itself), or anything copied in any app. */
export type ClipboardWatchMode = "workshop" | "everything";

/** The system clipboard of the device hosting the web UI. */
export interface ClipboardApi {
  /** Puts `text` on the system clipboard. Needs no user gesture, unlike the
   *  page's own clipboard API. Resolves true once it is there. */
  write(text: string): Promise<boolean>;
  /** While "everything", the Mac app itself reports each copy made in
   *  another app as the `copied` event, with or without a page open, opens at
   *  login, and offers its paste panel (`PANEL_CHANNEL`); copies made while
   *  Workshop is in front, and items an app marks concealed, transient or
   *  generated, are not reported. The iPhone app, which cannot see copies
   *  from the background, reports the latest copy made elsewhere each time it
   *  comes to the front. Kept by the shell across launches; "workshop" until
   *  set. */
  watch: {
    get(): Promise<ClipboardWatchMode>;
    set(mode: ClipboardWatchMode): Promise<ClipboardWatchMode>;
  };
}

/** The Mac's paste panel: a shortcut pressed in any app opens a floating
 *  panel over it showing `/embed/overlay`, which hosts the overlay app that
 *  keeps copies. That page talks to the panel on its own message handler
 *  (`window.webkit.messageHandlers[PANEL_CHANNEL]`), present in the panel's
 *  web view only, never on `WorkshopNative`. */
export const PANEL_CHANNEL = "workshopPanel";
/** Dispatched on the panel page's window, detail `PanelOpenDetail`, each time
 *  the panel is shown again; the first showing loads the page with `?into=`. */
export const PANEL_OPEN_EVENT = "workshop:panel-open";
/** Dispatched on the panel page's window when the panel is put away. */
export const PANEL_CLOSE_EVENT = "workshop:panel-close";

/** The name of the app the panel was opened over, "" when unknown. */
export interface PanelOpenDetail {
  into: string;
}

/** Page -> panel. `insert` puts the text on the clipboard, closes the panel
 *  and pastes it into the app it was opened over. */
export type PanelMessage = { t: "insert"; text: string } | { t: "close" };

/** The system microphone permission of the app hosting the web UI. */
export interface MicApi {
  status(): Promise<PermissionState>;
  /** Shows the system prompt when the answer is still undetermined; resolves
   *  to the state after it. Never prompts once the user has answered. */
  request(): Promise<PermissionState>;
  /** Opens the system settings page where this app's microphone is allowed. */
  openSettings(): Promise<void>;
}

/** What Workshop's web UI tells the shell about itself. */
export interface ShellApi {
  /**
   * The app whose frame the web UI shows now, by slug, or null when it shows
   * none. Only that app's page views are drawn and may play: every other
   * app's are hidden with their sound and video held, and are back as their
   * app left them when its frame is shown again. A shell with a menu bar (the
   * Mac) also gives that app its page commands while none of its page views
   * is visible ("pages:command" with a null `id`, see `PageCommandEvent`).
   * Until the web UI first says, every app's pages are as their apps set them.
   * Offered (`has("shell")`) only by shells that take it.
   */
  foreground(app: string | null): Promise<void>;
  /**
   * Where the web UI's own layers (bars, notices, menus, dialogs, whatever
   * it draws above an app's frame) sit, in the CSS pixels of page view
   * rects, kept apart from the cutouts each app sets, so no app has to know
   * what the shell drew. Clicks and touches inside the rects go to the web
   * UI; while `modal` holds (a menu or dialog is open), all of them do. An
   * empty list gives every page its input back. Page views are drawn under
   * the web UI, so these layers show over pages on their own; a shell from
   * before that cuts every page away at these rects. Absent in shells from
   * before it.
   */
  overlays?(rects: PageRect[], modal: boolean): Promise<void>;
}

// ---------------------------------------------------------------------------
// Wire envelopes
// ---------------------------------------------------------------------------

export interface BridgeRequest {
  t: "req";
  id: number;
  cap: Capability;
  method: string;
  args: unknown[];
  v: number;
  /** The calling app, from `WorkshopNative.forApp`. */
  app?: string;
}

export interface BridgeResponse {
  t: "res";
  id: number;
  ok: boolean;
  result?: unknown;
  error?: string;
}

export type BridgeEventTopic =
  | "location:fix"
  | "audio:captured"
  | "notify:response"
  | "liveActivity:ended"
  | PagesEventTopic;

export interface BridgeEvent {
  t: "evt";
  topic: BridgeEventTopic;
  payload: unknown;
}

/** Posted by the injected client each time it installs into a new top document. */
export interface BridgeHello {
  t: "hello";
  v: number;
}

export type BridgeMessage = BridgeRequest | BridgeResponse | BridgeEvent | BridgeHello;

/** Type guard used by the RN dispatcher. */
export function isBridgeRequest(m: unknown): m is BridgeRequest {
  return !!m && typeof m === "object" && (m as { t?: string }).t === "req";
}

export function isBridgeHello(m: unknown): m is BridgeHello {
  return !!m && typeof m === "object" && (m as { t?: string }).t === "hello";
}
