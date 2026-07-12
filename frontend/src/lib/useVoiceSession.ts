// React binding for VoiceSession. Owns one session per contextKey, wires the
// streaming reply path to the resident voice worker (/api/voice/session +
// /turns + SSE), loads + persists per-owner prefs, and exposes a live snapshot
// via useSyncExternalStore.
import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from "react";

import { chatApi } from "./chat";
import { voiceApi } from "./voiceApi";
import {
  VoiceSession,
  DEFAULT_PREFS,
  type VoicePrefs,
  type VoiceSnapshot,
  type ReplyHandle,
  type ReplyHandlers,
} from "./voiceSession";

export function useVoiceSession(contextKey: string, model?: string) {
  const threadRef = useRef<string | null>(null);
  const prefsRef = useRef<VoicePrefs>({ ...DEFAULT_PREFS });
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Ensure the resident worker + thread exist; returns the thread id. Prewarmed
  // on mount so the process is up before the first utterance finishes.
  const ensureSession = useCallback(async (): Promise<string> => {
    if (threadRef.current) return threadRef.current;
    const r = await fetch("/api/voice/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context_key: contextKey }),
    });
    if (!r.ok) throw new Error(`session ${r.status}`);
    const id = (await r.json()).thread_id as string;
    threadRef.current = id;
    return id;
  }, [contextKey]);

  // startReply: begin a streaming turn. Deltas arrive over SSE; the returned
  // handle cancels generation (barge-in). Falls back to the blocking /messages
  // path if streaming can't start and no tokens have arrived yet.
  const startReply = useCallback(
    (text: string, handlers: ReplyHandlers): ReplyHandle => {
      const ac = new AbortController();
      let turnId: string | null = null;
      let full = "";
      let gotDelta = false;
      let settled = false;

      const cancelTurn = (id: string, reason: string, heard?: number, heardText?: string) => {
        fetch(`/api/voice/turns/${id}/cancel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reason, heard, heard_text: heardText }),
        }).catch(() => {});
      };

      const fallback = async () => {
        try {
          const id = await ensureSession();
          const { message } = await chatApi.sendMessage(id, text, model);
          if (!settled) { settled = true; handlers.onDone(message.content || ""); }
        } catch (e) {
          if (!settled) { settled = true; handlers.onError(String(e)); }
        }
      };

      const handleEvent = (name: string, data: string) => {
        if (settled) return;
        if (name === "text") {
          try {
            const d = JSON.parse(data);
            full += d.payload?.content || "";
            gotDelta = true;
            handlers.onDelta(full);
          } catch { /* ignore malformed frame */ }
        } else if (name === "done" || name === "end") {
          settled = true;
          handlers.onDone(full);
        } else if (name === "error") {
          let detail = "reply error";
          try { detail = JSON.parse(data).payload?.detail || detail; } catch { /* noop */ }
          settled = true;
          handlers.onError(detail);
        }
      };

      (async () => {
        try {
          const thread = await ensureSession();
          const tr = await fetch(`/api/voice/session/${thread}/turns`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ context_key: contextKey, text, model }),
          });
          if (!tr.ok) throw new Error(`turn ${tr.status}`);
          turnId = (await tr.json()).turn_id as string;
          const resp = await fetch(`/api/voice/turns/${turnId}/stream`, {
            headers: { Accept: "text/event-stream" },
            signal: ac.signal,
          });
          if (!resp.ok || !resp.body) throw new Error(`stream ${resp.status}`);
          const reader = resp.body.getReader();
          const dec = new TextDecoder();
          let buf = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            let idx: number;
            while ((idx = buf.indexOf("\n\n")) >= 0) {
              const frame = buf.slice(0, idx);
              buf = buf.slice(idx + 2);
              let evName = "message";
              let data = "";
              for (const line of frame.split("\n")) {
                if (line.startsWith("event:")) evName = line.slice(6).trim();
                else if (line.startsWith("data:")) data += line.slice(5).trim();
              }
              handleEvent(evName, data);
            }
            if (settled) { reader.cancel().catch(() => {}); break; }
          }
          if (!settled) { settled = true; handlers.onDone(full); }
        } catch (e) {
          if (ac.signal.aborted) return;         // cancelled by barge-in
          if (!gotDelta) { fallback(); return; }  // never started: blocking fallback
          if (!settled) { settled = true; handlers.onError(String(e)); }
        }
      })();

      return {
        cancel: (info) => {
          settled = true;
          ac.abort();
          if (turnId) cancelTurn(turnId, "barge", info?.heard, info?.heardText);
        },
      };
    },
    [contextKey, model, ensureSession],
  );

  const persistSignal = useCallback((rate: number) => {
    voiceApi.putPrefs({ interrupt_rate: Number(rate.toFixed(3)) }).catch(() => {});
  }, []);

  const session = useMemo(
    () => new VoiceSession({ startReply, prefs: prefsRef.current, onPrefsSignal: persistSignal }),
    [startReply, persistSignal],
  );

  // Prewarm the resident worker on mount (best-effort).
  useEffect(() => {
    ensureSession().catch(() => {});
  }, [ensureSession]);

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
