// "Speak replies" toggle for the typed chat composer. When on, each completed
// assistant message is read aloud with the best available voice (local Kokoro if
// present, else the browser voice). The choice persists across sessions.
import { useCallback, useEffect, useRef, useState } from "react";

import {
  detectCaps,
  makeSpeaker,
  splitSentences,
  cleanForSpeech,
  type Speaker,
  type TtsProvider,
} from "./voice";

const KEY = "ws-voice-speak-replies";

export function useReplySpeaker() {
  const [on, setOn] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY) === "1"; } catch { return false; }
  });
  const [tts, setTts] = useState<TtsProvider | null>(null);
  const speakerRef = useRef<Speaker | null>(null);
  const onRef = useRef(on);
  onRef.current = on;

  useEffect(() => {
    let alive = true;
    detectCaps().then((c) => { if (alive) setTts(c.tts); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const stop = useCallback(() => {
    speakerRef.current?.stop();
    speakerRef.current = null;
  }, []);

  // Speak a completed reply. No-op unless the toggle is on and a voice exists.
  const speak = useCallback((text: string) => {
    if (!onRef.current || !tts) return;
    const clean = cleanForSpeech(text);
    if (!clean) return;
    stop();
    const sp = makeSpeaker(tts, {});
    speakerRef.current = sp;
    sp.speak(splitSentences(clean), { onDone: () => { speakerRef.current = null; } });
  }, [tts, stop]);

  const toggle = useCallback(() => {
    setOn((prev) => {
      const next = !prev;
      try { localStorage.setItem(KEY, next ? "1" : "0"); } catch { /* noop */ }
      if (!next) stop();
      return next;
    });
  }, [stop]);

  useEffect(() => () => stop(), [stop]);

  return { available: tts !== null, on, toggle, speak, stop };
}
