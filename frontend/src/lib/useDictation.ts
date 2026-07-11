// Push-to-talk dictation for the chat composer. Click to start, click to stop;
// the transcript is handed back to the caller to drop into the input. Uses the
// browser Web Speech API when present (live interim text), otherwise records a
// clip and sends it to the local server STT. Silent no-op when neither exists.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  browserSttAvailable,
  recognizeOnce,
  transcribeBlob,
  probeServer,
} from "./voice";

export function useDictation(onText: (text: string, partial: boolean) => void) {
  const [available, setAvailable] = useState<boolean>(browserSttAvailable());
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (browserSttAvailable()) { setAvailable(true); return; }
    let alive = true;
    probeServer().then((h) => { if (alive) setAvailable(!!h?.stt.available); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const stopServer = useCallback(async () => {
    const rec = recRef.current;
    if (!rec) return;
    setBusy(true);
    await new Promise<void>((resolve) => {
      rec.onstop = () => resolve();
      rec.stop();
    });
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recRef.current = null;
    const blob = new Blob(chunksRef.current, { type: rec.mimeType });
    try {
      if (blob.size > 1000) {
        const text = await transcribeBlob(blob);
        if (text) onText(text, false);
      }
    } finally {
      setBusy(false);
    }
  }, [onText]);

  const start = useCallback(async () => {
    if (browserSttAvailable()) {
      const { promise, abort } = recognizeOnce((t) => onText(t, true));
      abortRef.current = abort;
      setRecording(true);
      promise
        .then((final) => { if (final) onText(final, false); })
        .catch(() => {})
        .finally(() => { setRecording(false); abortRef.current = null; });
      return;
    }
    // server path
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chunksRef.current = [];
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus" : "audio/webm";
      const rec = new MediaRecorder(stream, { mimeType: mime });
      rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
      recRef.current = rec;
      rec.start();
      setRecording(true);
    } catch {
      setRecording(false);
    }
  }, [onText]);

  const stop = useCallback(async () => {
    if (abortRef.current) { abortRef.current(); abortRef.current = null; setRecording(false); return; }
    if (recRef.current) { setRecording(false); await stopServer(); }
  }, [stopServer]);

  const toggle = useCallback(() => { recording ? stop() : start(); }, [recording, start, stop]);

  useEffect(() => () => {
    abortRef.current?.();
    streamRef.current?.getTracks().forEach((t) => t.stop());
  }, []);

  return { available, recording, busy, toggle };
}
