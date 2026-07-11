// On-device speech for voice mode. STT via iOS SFSpeechRecognizer
// (expo-speech-recognition, on-device mode) and TTS via AVSpeechSynthesizer
// (expo-speech). FREE — no paid entitlement; both are standard iOS frameworks.
//
// The web app's VoiceEngine picks the "native" provider when this capability is
// present, so a spoken exchange in the shell never leaves the phone.

import * as Speech from "expo-speech";
import {
  ExpoSpeechRecognitionModule,
} from "expo-speech-recognition";

import type {
  SpeechAvailability,
  SpeechResult,
  SpeechSpeakOptions,
  SpeechTranscribeOptions,
} from "@shared/native-bridge";

export async function available(): Promise<SpeechAvailability> {
  let stt = false;
  try {
    const perm = await ExpoSpeechRecognitionModule.getPermissionsAsync();
    stt = perm.granted || perm.canAskAgain;
  } catch {
    stt = false;
  }
  return { stt, tts: true };
}

// Start recognition and resolve with the final transcript when the recognizer
// endpoints. On-device by default so audio stays on the phone.
export async function transcribe(opts?: SpeechTranscribeOptions): Promise<SpeechResult> {
  const perm = await ExpoSpeechRecognitionModule.requestPermissionsAsync();
  if (!perm.granted) throw new Error("speech permission denied");

  return new Promise<SpeechResult>((resolve, reject) => {
    let finalText = "";
    const subs: { remove: () => void }[] = [];
    const cleanup = () => subs.forEach((s) => s.remove());

    subs.push(
      ExpoSpeechRecognitionModule.addListener("result", (e: {
        results: { transcript: string }[];
        isFinal: boolean;
      }) => {
        const t = e.results?.[0]?.transcript ?? "";
        if (t) finalText = t;
      }),
    );
    subs.push(
      ExpoSpeechRecognitionModule.addListener("end", () => {
        cleanup();
        resolve({ text: finalText.trim() });
      }),
    );
    subs.push(
      ExpoSpeechRecognitionModule.addListener("error", (e: { error: string; message?: string }) => {
        cleanup();
        reject(new Error(e.message || e.error || "recognition error"));
      }),
    );

    try {
      ExpoSpeechRecognitionModule.start({
        lang: opts?.locale ?? "en-US",
        interimResults: false,
        continuous: false,
        requiresOnDeviceRecognition: opts?.onDevice ?? true,
      });
    } catch (e) {
      cleanup();
      reject(e instanceof Error ? e : new Error(String(e)));
    }
  });
}

export async function speak(text: string, opts?: SpeechSpeakOptions): Promise<void> {
  return new Promise<void>((resolve) => {
    Speech.speak(text, {
      language: "en-US",
      rate: opts?.rate ?? 1.0,
      pitch: opts?.pitch ?? 1.0,
      voice: opts?.voice,
      onDone: () => resolve(),
      onStopped: () => resolve(),
      onError: () => resolve(),
    });
  });
}

export async function stop(): Promise<void> {
  try { await Speech.stop(); } catch { /* noop */ }
  try { ExpoSpeechRecognitionModule.abort(); } catch { /* noop */ }
}
