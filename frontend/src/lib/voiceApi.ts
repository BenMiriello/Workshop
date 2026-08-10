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

  // Release the resident worker for a thread. The endpoint has existed since
  // voice shipped and had NO caller anywhere in the app, so turning voice off
  // left a claude process resident until the reaper collected it ten minutes
  // later. `keepalive` so it still goes out when this is the last thing a
  // closing tab does.
  endSession: (threadId: string) =>
    fetch(`/api/voice/session/${threadId}`, { method: "DELETE", keepalive: true })
      .then(() => undefined, () => undefined),
};
