// React binding for VoiceSession. Owns one session per contextKey, wires the
// reply path to the existing chat backend (data-mode /messages), loads + persists
// per-owner prefs, and exposes a live snapshot via useSyncExternalStore.
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";

import { chatApi } from "./chat";
import { voiceApi } from "./voiceApi";
import {
  VoiceSession,
  DEFAULT_PREFS,
  type VoicePrefs,
  type VoiceSnapshot,
} from "./voiceSession";

export function useVoiceSession(contextKey: string, model?: string) {
  const threadRef = useRef<string | null>(null);
  const prefsRef = useRef<VoicePrefs>({ ...DEFAULT_PREFS });
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // sendTurn: ensure a thread for this context, post the utterance, return the
  // assistant's reply text. Same path the typed chat uses.
  const sendTurn = useCallback(
    async (text: string): Promise<string> => {
      let id = threadRef.current;
      if (!id) {
        const t = await chatApi.createThread(contextKey);
        id = t.id;
        threadRef.current = id;
      }
      const { message } = await chatApi.sendMessage(id, text, model);
      return message.content || "";
    },
    [contextKey, model],
  );

  const persistSignal = useCallback((rate: number) => {
    voiceApi.putPrefs({ interrupt_rate: Number(rate.toFixed(3)) }).catch(() => {});
  }, []);

  const session = useMemo(
    () => new VoiceSession({ sendTurn, prefs: prefsRef.current, onPrefsSignal: persistSignal }),
    [sendTurn, persistSignal],
  );

  // Load persisted prefs once and push them into the live session.
  useEffect(() => {
    let alive = true;
    voiceApi
      .getPrefs()
      .then((p) => {
        if (!alive) return;
        const merged: VoicePrefs = {
          ...DEFAULT_PREFS,
          voice: p.voice ?? DEFAULT_PREFS.voice,
          speed: p.speed ?? DEFAULT_PREFS.speed,
          tts_provider: (p.tts_provider as VoicePrefs["tts_provider"]) ?? DEFAULT_PREFS.tts_provider,
          stt_provider: (p.stt_provider as VoicePrefs["stt_provider"]) ?? DEFAULT_PREFS.stt_provider,
          speak_replies: p.speak_replies ?? DEFAULT_PREFS.speak_replies,
          endpoint_silence_ms: p.endpoint_silence_ms ?? DEFAULT_PREFS.endpoint_silence_ms,
          barge_in: p.barge_in ?? DEFAULT_PREFS.barge_in,
          barge_sensitivity: p.barge_sensitivity ?? DEFAULT_PREFS.barge_sensitivity,
          verbosity: (p.verbosity as VoicePrefs["verbosity"]) ?? DEFAULT_PREFS.verbosity,
        };
        prefsRef.current = merged;
        session.setPrefs(merged);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [session]);

  // Tear the session down when the component using it unmounts.
  useEffect(() => () => session.stop(), [session]);

  const snapshot = useSyncExternalStore<VoiceSnapshot>(
    (cb) => session.subscribe(cb),
    () => session.snapshot(),
  );

  const updatePrefs = useCallback(
    (patch: Partial<VoicePrefs>) => {
      const next = { ...prefsRef.current, ...patch };
      prefsRef.current = next;
      session.setPrefs(next);
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => voiceApi.putPrefs(patch).catch(() => {}), 400);
    },
    [session],
  );

  return { session, snapshot, prefs: prefsRef.current, updatePrefs };
}
