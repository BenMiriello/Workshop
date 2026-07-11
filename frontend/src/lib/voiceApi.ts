// Thin client for the per-owner voice prefs + voice catalog.
import type { VoicePrefs } from "./voiceSession";

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

export const voiceApi = {
  getPrefs: () => fetch("/api/voice/prefs").then(j<VoicePrefs & Record<string, unknown>>),
  putPrefs: (patch: Partial<VoicePrefs> & Record<string, unknown>) =>
    fetch("/api/voice/prefs", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }).then(j<VoicePrefs & Record<string, unknown>>),
  voices: () => fetch("/api/voice/voices").then(j<{ available: boolean; voices: string[] }>),
  health: () =>
    fetch("/api/voice/health").then(
      j<{ stt: { available: boolean }; tts: { available: boolean }; device?: string }>,
    ),
};
