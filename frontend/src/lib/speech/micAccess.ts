// Where to turn the microphone back on, said for the client in hand.
//
// Once a browser or an app has been told no, a page cannot ask again: the
// prompt is gone until the setting is changed by hand, and where that setting
// lives differs by client. So the message names the place rather than saying
// "check your settings".

import { native } from "@workshop/ui";

export type MicClient =
  | "ios-app" | "mac-app" | "ios-safari" | "safari" | "firefox" | "chromium" | "other";

export function micClient(platform: string | undefined, ua: string): MicClient {
  if (platform === "ios") return "ios-app";
  if (platform === "macos") return "mac-app";
  if (/Firefox\//.test(ua) || /FxiOS\//.test(ua)) return "firefox";
  if (/(iPhone|iPad|iPod)/.test(ua)) return "ios-safari";
  if (/Chrome\/|Chromium\/|Edg\//.test(ua)) return "chromium";
  if (/Safari\//.test(ua)) return "safari";
  return "other";
}

const HELP: Record<MicClient, string> = {
  "ios-app": "The microphone is off for Workshop. Turn it on in the Settings app under Workshop, then try again.",
  "mac-app": "The microphone is off for Workshop. Turn it on in System Settings, Privacy & Security, Microphone, then try again.",
  "ios-safari": "Safari is blocking the microphone here. Tap the page menu in the address bar, open Website Settings, allow Microphone, then try again.",
  safari: "Safari is blocking the microphone here. Open Safari, Settings for This Website, set Microphone to Allow, then try again.",
  firefox: "Firefox is blocking the microphone here. Click the crossed-out microphone in the address bar, clear the block, then try again.",
  chromium: "The browser is blocking the microphone here. Click the site controls left of the address, allow Microphone, then try again.",
  other: "The browser is blocking the microphone here. Allow it in this site's settings, then try again.",
};

export function micBlockedHelp(client: MicClient): string {
  return HELP[client];
}

/** The label for the button that opens the setting, where the client can. */
export function micSettingsLabel(client: MicClient): string {
  return client === "mac-app" ? "Open System Settings" : "Open Settings";
}

/** The shell's own microphone permission, when the app hosting the page
 *  offers it (shared/native-bridge.ts `mic`). Null in a browser. */
function nativeMic() {
  try {
    const n = native();
    return n?.has("mic") ? n.mic : null;
  } catch {
    return null;
  }
}

/** In an app that hosts the page, make sure the system allows the microphone,
 *  raising the system's own prompt when it has never asked. True in a browser,
 *  where the page's request carries its own prompt. */
export async function ensureSystemMic(): Promise<boolean> {
  const mic = nativeMic();
  if (!mic) return true;
  try {
    let state = await mic.status();
    if (state === "undetermined") state = await mic.request();
    return state === "granted";
  } catch {
    return true;
  }
}

/** Whether a button here can take the operator to the setting. */
export function canOpenMicSettings(): boolean {
  return nativeMic() !== null;
}

export async function openMicSettings(): Promise<void> {
  await nativeMic()?.openSettings();
}

export function currentMicClient(): MicClient {
  let platform: string | undefined;
  try {
    platform = native()?.platform;
  } catch {
    platform = undefined;
  }
  return micClient(platform, typeof navigator === "undefined" ? "" : navigator.userAgent);
}

export type MicPermission = "granted" | "prompt" | "denied" | "unknown";

/** What this client will do when the microphone is asked for. "unknown" where
 *  the browser cannot say without asking. */
export async function micPermission(): Promise<MicPermission> {
  const mic = nativeMic();
  if (mic) {
    try {
      const state = await mic.status();
      return state === "granted" ? "granted" : state === "undetermined" ? "prompt" : "denied";
    } catch {
      return "unknown";
    }
  }
  try {
    const status = await navigator.permissions.query({ name: "microphone" as PermissionName });
    return status.state;
  } catch {
    return "unknown";
  }
}

/** Call `onChange` whenever the answer may have changed: the browser's own
 *  change event where it has one, and on coming back to the page, which is
 *  when a setting changed elsewhere would show. Returns the unsubscribe. */
export function watchMicPermission(onChange: () => void): () => void {
  let status: PermissionStatus | null = null;
  let live = true;
  navigator.permissions?.query({ name: "microphone" as PermissionName })
    .then((s) => { if (live) { status = s; s.addEventListener("change", onChange); } })
    .catch(() => {});
  const onBack = () => { if (document.visibilityState === "visible") onChange(); };
  window.addEventListener("focus", onBack);
  document.addEventListener("visibilitychange", onBack);
  return () => {
    live = false;
    status?.removeEventListener("change", onChange);
    window.removeEventListener("focus", onBack);
    document.removeEventListener("visibilitychange", onBack);
  };
}

/** Ask for the microphone now, from a click, so the client shows its prompt,
 *  then let it go at once. Resolves to whether it was granted. */
export async function requestMic(): Promise<boolean> {
  const mic = nativeMic();
  if (mic) {
    // The system prompt first: the page's own request cannot raise it for the
    // app hosting the page.
    const state = await mic.request().catch(() => "denied" as const);
    if (state !== "granted") return false;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch {
    return false;
  }
}
