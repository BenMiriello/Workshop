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
//   native -> web :  window.__WORKSHOP_NATIVE_RECV__(BridgeResponse | BridgeEvent)
//
// Because both packages alias `@shared/*` -> `../shared/*`, both import these
// types from `@shared/native-bridge`. Keep `CAP_METHODS` (runtime) and the
// `WorkshopNative` interface (compile-time) in sync — the injected stub builds
// its namespaces from `CAP_METHODS`, the type checker enforces the interface.

export const BRIDGE_VERSION = 1;

/** Name of the public global the shell installs on `window`. */
export const NATIVE_GLOBAL = "WorkshopNative";
/** Name of the global the shell calls to deliver responses/events into the web. */
export const RECV_FN = "__WORKSHOP_NATIVE_RECV__";

/** Runtime method manifest, per capability. Drives the injected stub. */
export const CAP_METHODS = {
  location: ["getCurrent", "startBackgroundTracking", "stopBackgroundTracking", "trackingStatus"],
  audio: ["startCapture", "stopCapture"],
  localNotify: ["schedule", "cancel", "cancelAll", "permissionStatus"],
  liveActivity: ["isSupported", "start", "update", "end"],
  secureStore: ["get", "set", "remove"],
} as const;

export type Capability = keyof typeof CAP_METHODS;

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
   * "active"      — continuous standard updates (commute in progress; higher battery).
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

/** Status kinds mirror the web design system's five status kinds. */
export type ActivityStatus = "ok" | "info" | "caution" | "urgent";

/**
 * Content for the commute Live Activity (lock screen + Dynamic Island).
 * The widget renders a live countdown to `countdownTo` natively, so it keeps
 * advancing without further updates — we only push a new state when the
 * underlying plan changes.
 */
export interface CommuteActivityState {
  direction: "to-work" | "to-home";
  /** Primary line, e.g. "Caltrain 506". */
  headline: string;
  /** Secondary line, e.g. "Mountain View -> San Francisco". */
  subtitle?: string;
  leaveByLocal?: string;
  targetArriveLocal?: string;
  /** ISO 8601 instant the native timer counts down to. */
  countdownTo: string;
  status: ActivityStatus;
}

export interface LiveActivityHandle {
  /** Opaque id used to update/end this activity. */
  id: string;
}

// ---------------------------------------------------------------------------
// Public API surface (compile-time mirror of CAP_METHODS)
// ---------------------------------------------------------------------------

export interface WorkshopNative {
  readonly bridgeVersion: number;
  readonly platform: "ios";
  /** "paid" only when the Apple Developer Program entitlements are present. */
  readonly tier: "free" | "paid";

  /** True when `cap` is available in this build/tier. */
  has(cap: Capability): boolean;

  /** Subscribe to pushed events (e.g. background location fixes). Returns an unsubscribe fn. */
  on<T = unknown>(topic: BridgeEventTopic, cb: (payload: T) => void): () => void;

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

  localNotify: {
    schedule(n: LocalNotification): Promise<void>;
    cancel(id: string): Promise<void>;
    cancelAll(): Promise<void>;
    permissionStatus(): Promise<PermissionState>;
  };

  liveActivity: {
    isSupported(): Promise<boolean>;
    start(state: CommuteActivityState): Promise<LiveActivityHandle>;
    update(id: string, state: CommuteActivityState): Promise<void>;
    end(id: string): Promise<void>;
  };

  secureStore: {
    get(key: string): Promise<string | null>;
    set(key: string, value: string): Promise<void>;
    remove(key: string): Promise<void>;
  };
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
  | "liveActivity:ended";

export interface BridgeEvent {
  t: "evt";
  topic: BridgeEventTopic;
  payload: unknown;
}

export type BridgeMessage = BridgeRequest | BridgeResponse | BridgeEvent;

/** Type guard used by the RN dispatcher. */
export function isBridgeRequest(m: unknown): m is BridgeRequest {
  return !!m && typeof m === "object" && (m as { t?: string }).t === "req";
}
