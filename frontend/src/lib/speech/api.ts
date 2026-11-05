// The speech service: dictation in, read-aloud out.
//
// One request per piece. Transcribe takes a whole recording as a WAV body and
// answers with its text; speak takes one sentence and answers with a WAV.

import type { components } from "../../api/schema.gen";
import { httpFail, jsonOrFail } from "../http";
import { queryClient } from "../queryClient";
import { queryKeys } from "../queryKeys";

export type SpeechHealth = components["schemas"]["SpeechStatus"];

const STATUS_FRESH_MS = 60_000;
const VOICES_FRESH_MS = 10 * 60_000;

/** The one query for the service's readiness, shared by every reader. */
export const speechStatusQuery = {
  queryKey: queryKeys.speech.status(),
  queryFn: (): Promise<SpeechHealth> => fetch("/api/speech/status").then((r) => jsonOrFail<SpeechHealth>(r)),
  staleTime: STATUS_FRESH_MS,
};

export type SpeechVoice = components["schemas"]["SpeechVoice"];
export type SpeechVoices = components["schemas"]["VoiceList"];

/** The voices read aloud can use, labeled, and the server's default. The list
 *  only changes when the server's models do, so it is kept a while. */
export const speechVoicesQuery = {
  queryKey: queryKeys.speech.voices(),
  queryFn: (): Promise<SpeechVoices> => fetch("/api/speech/voices").then((r) => jsonOrFail<SpeechVoices>(r)),
  staleTime: VOICES_FRESH_MS,
};

export const speechApi = {
  /** The service's readiness, cached for a minute. Null when the service
   *  cannot be reached at all, which callers treat as "not available". */
  health(): Promise<SpeechHealth | null> {
    return queryClient
      .fetchQuery(speechStatusQuery)
      .catch(() => null);
  },

  /** Load the models ahead of the first request. Best effort, never awaited
   *  by anything the operator is waiting on. */
  warm(): void {
    fetch("/api/speech/warm", { method: "POST" }).catch(() => {});
  },

  async transcribe(wav: ArrayBuffer, signal?: AbortSignal): Promise<string> {
    const r = await fetch("/api/speech/transcribe", {
      method: "POST",
      headers: { "Content-Type": "audio/wav" },
      body: wav,
      signal,
    });
    const body = await jsonOrFail<components["schemas"]["Transcript"]>(r);
    return body.text.trim();
  },

  /** One piece as WAV, in `voice` (an id from the voice list) or the
   *  server's default when it is absent. */
  async speak(text: string, voice?: string, signal?: AbortSignal): Promise<Blob> {
    const r = await fetch("/api/speech/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(voice ? { text, voice } : { text }),
      signal,
    });
    if (!r.ok) throw await httpFail(r);
    return r.blob();
  },
};
