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
import type { StatusKind } from "../status";
import type { PageRect, PagesApi } from "./pageTypes";

export type { PageMessageEvent, PageRunOptions, PageUserScript, PageWorld } from "./pageScriptTypes";
export type { PageDownloadEvent } from "./pageDownloadTypes";
export type {
  PageHopRule, PageNavigationBlockedEvent, PageNavigationRules, PageStripRule,
} from "./pageNavigationTypes";
export type * from "./pageTypes";

export type Capability =
  | "location" | "audio" | "speech" | "localNotify" | "mic" | "liveActivity" | "secureStore" | "pages"
  | "shell";

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

/** The kit's five status kinds. */
export type ActivityStatus = StatusKind;

/** What a Live Activity shows; the shell lays out the lock screen and the
 *  Dynamic Island from these fields. */
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

export type BridgeEventTopic =
  | "location:fix" | "audio:captured" | "notify:response" | "liveActivity:ended"
  | "pages:state" | "pages:error" | "pages:newWindow" | "pages:command" | "pages:message"
  | "pages:navigationBlocked" | "pages:download";

/** The shell's web engine; a field is left out where the shell cannot tell it. */
export interface NativeEngine {
  /** WebKit's own build version, e.g. "20621.1.15.11.10". */
  webkitVersion?: string;
  /** The Safari release this WebKit is, e.g. "26.0". */
  safariVersion?: string;
}

export interface WorkshopNative {
  readonly bridgeVersion: number;
  readonly platform: "ios" | "macos";
  readonly tier: "free" | "paid";
  /** Absent in shell builds from before it existed. */
  readonly engine?: NativeEngine;

  has(cap: Capability): boolean;
  on<T = unknown>(topic: BridgeEventTopic, cb: (payload: T) => void): () => void;
  /** This bridge acting for app `app`: requests carry it and `on` passes
   *  only its "pages:*" events. Page views and Live Activities are kept per
   *  app by it. Absent in shells from before it existed. */
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

  mic: MicApi;

  liveActivity: LiveActivityApi;

  secureStore: {
    set(key: "ws-device-token", value: string): Promise<void>;
  };

  pages: PagesApi;

  shell: ShellApi;
}

/** The system microphone permission of the app hosting the web UI. */
export interface MicApi {
  status(): Promise<PermissionState>;
  /** The system prompt while undetermined; resolves to the state after it. */
  request(): Promise<PermissionState>;
  /** Opens the settings page where this app's microphone is allowed. */
  openSettings(): Promise<void>;
}

/** What Workshop's web UI tells the shell; offered only by shells that take it. */
export interface ShellApi {
  /** The app whose frame the web UI shows, or null for none. */
  foreground(app: string | null): Promise<void>;
  /** Where the web UI's own layers sit over page views, in page view space:
   *  every app's pages are cut away there, and take no input there (none at
   *  all while `modal`). Absent in shells from before it. */
  overlays?(rects: PageRect[], modal: boolean): Promise<void>;
}
