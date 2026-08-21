// Local mirror of `shared/native-bridge.ts`, Workshop's own native-bridge
// contract (the source of truth the web app and the Expo shell both compile
// against).
//
// WHY A COPY, NOT AN IMPORT. `shared/` is a monorepo-only directory with no
// package boundary of its own; the frontend and the mobile app reach it
// through matching tsconfig/vite path aliases that only exist inside this
// repo. Importing it directly from the kit's SOURCE type-checks fine (an
// `import type` costs nothing at runtime), but it broke the kit's declaration
// BUILD: `tsc --emitDeclarationOnly` treats every file it opens to resolve a
// type, not just the kit's own entry points, as part of the compilation, so
// it tried to also emit `shared/native-bridge.d.ts`, and without an explicit
// `rootDir` that pulled the whole output layout out from under
// `dist/index.d.ts` (files landed at `dist/packages/ui/src/*.d.ts` instead of
// `dist/*.d.ts`).
//
// Structural typing means this only has to match the SHAPE of the real
// `window.WorkshopNative`, so a local copy is safe as long as it stays in
// sync; it mirrors the full public surface (everything but the wire
// envelopes `BridgeRequest`/`BridgeResponse`/`BridgeMessage`, which nothing
// outside the dispatcher itself touches) rather than only what this
// package's own wrappers call, because platform code that reads `native()`'s
// return value through this package's re-exported `WorkshopNative` type
// needs the whole interface, not just the slice `native/bridge.ts` uses.
export type Capability =
  | "location" | "audio" | "speech" | "localNotify" | "liveActivity" | "secureStore";

export interface LocationFix {
  lat: number;
  lng: number;
  accuracy_m?: number;
  altitude_m?: number;
  heading_deg?: number;
  speed_mps?: number;
  client_ts?: string;
}

export type TrackingMode = "active" | "significant";

export interface BackgroundTrackingOptions {
  distanceM?: number;
  mode?: TrackingMode;
}

export type PermissionState = "granted" | "denied" | "undetermined" | "restricted";

export interface TrackingStatus {
  tracking: boolean;
  mode?: TrackingMode;
  last?: LocationFix;
  permission: PermissionState;
  queued?: number;
}

export interface LocalNotification {
  id: string;
  title: string;
  body: string;
  fireAt: string;
  data?: Record<string, unknown>;
}

export interface AudioCaptureOptions {
  maxSeconds: number;
  uploadPath?: string;
}

export interface AudioCaptureResult {
  uri: string;
  durationSec: number;
  uploaded?: boolean;
}

export interface SpeechAvailability {
  stt: boolean;
  tts: boolean;
}

export interface SpeechTranscribeOptions {
  locale?: string;
  onDevice?: boolean;
}

export interface SpeechResult {
  text: string;
}

export interface SpeechSpeakOptions {
  voice?: string;
  rate?: number;
  pitch?: number;
}

/** Status kinds mirror the web design system's five status kinds. */
export type ActivityStatus = "ok" | "info" | "caution" | "urgent";

export interface CommuteActivityState {
  direction: "to-work" | "to-home";
  headline: string;
  subtitle?: string;
  leaveByLocal?: string;
  targetArriveLocal?: string;
  countdownTo: string;
  status: ActivityStatus;
}

export interface LiveActivityHandle {
  id: string;
}

export type BridgeEventTopic =
  | "location:fix" | "audio:captured" | "notify:response" | "liveActivity:ended";

export interface WorkshopNative {
  readonly bridgeVersion: number;
  readonly platform: "ios";
  readonly tier: "free" | "paid";

  has(cap: Capability): boolean;
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

  speech: {
    available(): Promise<SpeechAvailability>;
    transcribe(opts?: SpeechTranscribeOptions): Promise<SpeechResult>;
    speak(text: string, opts?: SpeechSpeakOptions): Promise<void>;
    stop(): Promise<void>;
  };

  localNotify: {
    schedule(n: LocalNotification): Promise<void>;
    cancel(id: string): Promise<void>;
    cancelAll(): Promise<void>;
    permissionStatus(): Promise<PermissionState>;
    requestPermission(): Promise<PermissionState>;
  };

  liveActivity: {
    isSupported(): Promise<boolean>;
    start(state: CommuteActivityState): Promise<LiveActivityHandle>;
    update(id: string, state: CommuteActivityState): Promise<void>;
    end(id: string): Promise<void>;
  };

  secureStore: {
    set(key: "ws-device-token", value: string): Promise<void>;
  };
}
