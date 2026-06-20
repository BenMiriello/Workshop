// Microphone capture, incl. background audio. FREE — `UIBackgroundModes:["audio"]`
// is an Info.plist key, not a paid entitlement. Useful as an ad-hoc capture
// device for the birds pipeline. The clip is optionally uploaded on stop.

import { Audio } from "expo-av";

import type { AudioCaptureOptions, AudioCaptureResult } from "@shared/native-bridge";
import { apiUrl } from "./config";
import { authHeaders } from "./auth";

let recording: Audio.Recording | null = null;
let startedAt = 0;
let uploadPath: string | undefined;
let autoStopTimer: ReturnType<typeof setTimeout> | null = null;
let cachedResult: AudioCaptureResult | null = null;

async function upload(uri: string, path: string): Promise<boolean> {
  try {
    const form = new FormData();
    // RN FormData accepts the {uri,name,type} file shape.
    form.append("file", { uri, name: "capture.m4a", type: "audio/m4a" } as unknown as Blob);
    const r = await fetch(apiUrl(path), { method: "POST", body: form, headers: await authHeaders() });
    return r.ok;
  } catch {
    return false;
  }
}

async function finalize(): Promise<AudioCaptureResult> {
  if (!recording) {
    if (cachedResult) return cachedResult;
    throw new Error("no active capture");
  }
  if (autoStopTimer) {
    clearTimeout(autoStopTimer);
    autoStopTimer = null;
  }
  const rec = recording;
  recording = null;
  await rec.stopAndUnloadAsync();
  const uri = rec.getURI() ?? "";
  const durationSec = Math.round((Date.now() - startedAt) / 1000);
  let uploaded: boolean | undefined;
  if (uri && uploadPath) uploaded = await upload(uri, uploadPath);
  const result: AudioCaptureResult = { uri, durationSec, uploaded };
  cachedResult = result;
  return result;
}

export async function startCapture(opts: AudioCaptureOptions): Promise<void> {
  if (recording) throw new Error("capture already in progress");
  const perm = await Audio.requestPermissionsAsync();
  if (!perm.granted) throw new Error("microphone permission denied");

  await Audio.setAudioModeAsync({
    allowsRecordingIOS: true,
    staysActiveInBackground: true,
    playsInSilentModeIOS: true,
  });

  cachedResult = null;
  uploadPath = opts.uploadPath;
  const rec = new Audio.Recording();
  await rec.prepareToRecordAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
  await rec.startAsync();
  recording = rec;
  startedAt = Date.now();

  if (opts.maxSeconds > 0) {
    autoStopTimer = setTimeout(() => {
      void finalize().catch(() => {});
    }, opts.maxSeconds * 1000);
  }
}

export async function stopCapture(): Promise<AudioCaptureResult> {
  return finalize();
}
