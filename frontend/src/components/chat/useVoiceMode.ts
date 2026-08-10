import { useCallback, useEffect, useRef, useState } from "react";
import { useVoiceSession } from "../../lib/useVoiceSession";
import { unlockAudio } from "../../lib/voice";

// Voice as a MODE of this chat, not a separate screen: the same thread, the same
// message log. The engine (mic / STT / TTS / barge-in over a warm worker) drives
// input and spoken replies; both interfaces write the SAME thread.
//
// The lifecycle rules, each of which was a bug first:
//
//  - AUDIO IS UNLOCKED IN THE CLICK, not in the effect that starts the session.
//    The effect is not a user gesture, so priming there does not satisfy the
//    autoplay policy and a later held-draft release — which fires outside any
//    gesture — is still blocked. The toggle IS the gesture.
//  - THE ENGINE IS DRIVEN BY `voiceOn`, NOT BY THE TOGGLE. If the session object
//    is rebuilt mid-conversation (changing the model rebuilds the reply path),
//    keying off the toggle leaves voice running against the old session and
//    silently going deaf. Keying off the flag re-starts it on the new one.
//  - MIC LEVEL BYPASSES REACT. The session pushes ~60fps; it is written straight
//    onto the orb element as a CSS variable so the halo animates without
//    re-rendering the chat on every frame.
//  - "FATAL" MEANS ONE THING: no usable input lane left (voice-only input and
//    the mic is gone). Anything the composer can still carry — a muted mic, a
//    denied permission while typing works, an insecure origin — is a standing
//    condition for the status strip, NOT a reason to tear voice down. Muting
//    used to land here and kill the session.

export function useVoiceMode({
  contextKey, model, activeId, onAdoptThread, onFatal,
}: {
  contextKey: string;
  model: string;
  activeId: string | null;
  // The engine created a thread for a brand-new chat: adopt it as this chat's,
  // so the transcript stays unified and never forks into a parallel thread.
  onAdoptThread: (id: string) => void;
  onFatal: (message: string) => void;
}) {
  const [voiceOn, setVoiceOn] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const orbRef = useRef<HTMLButtonElement>(null);

  const adoptRef = useRef(onAdoptThread);
  adoptRef.current = onAdoptThread;
  const fatalRef = useRef(onFatal);
  fatalRef.current = onFatal;

  const {
    session: vsession, snapshot: vsnap, prefs: vprefs, updatePrefs: vupdatePrefs,
    prewarm: vprewarm, endSession: vendSession,
  } = useVoiceSession(
    contextKey,
    model || undefined,
    activeId ?? undefined,
    useCallback((id: string) => adoptRef.current(id), []),
  );

  const toggleVoice = useCallback(() => {
    unlockAudio();
    setVoiceOn((on) => !on);
  }, []);

  useEffect(() => {
    if (!voiceOn) return;
    vprewarm();
    vsession.start();
    // A closing tab does not run React cleanup, and voice holds a real process
    // on the other end. `pagehide` fires for a close, a navigation and a mobile
    // background alike, which is the whole set of ways this can end without an
    // unmount.
    const release = () => { vsession.stop(); vendSession(); };
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
      release();
    };
  }, [voiceOn, vsession, vprewarm, vendSession]);

  // Probe capabilities when voice turns on or the settings sheet opens, so the
  // provider controls reflect the truth before a session ever starts. Caps live
  // on the session: probeCaps fills them while idle, start() owns them once
  // running, and both reach the UI through the snapshot.
  useEffect(() => {
    if (voiceOn || settingsOpen) void vsession.probeCaps();
  }, [voiceOn, settingsOpen, vsession]);

  // Esc interrupts the assistant mid-speech. The settings sheet, when open, owns
  // Esc to close itself (handled by Modal).
  useEffect(() => {
    if (!voiceOn) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !settingsOpen) vsession.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [voiceOn, settingsOpen, vsession]);

  useEffect(() => {
    vsession.setOnLevel((v) => {
      const el = orbRef.current;
      if (el) el.style.setProperty("--vlevel", String(0.15 + v * 0.85));
    });
    return () => vsession.setOnLevel(null);
  }, [vsession]);

  useEffect(() => {
    if (voiceOn && vsnap.fatal) {
      fatalRef.current(vsnap.error || "Voice unavailable.");
      setVoiceOn(false);
    }
  }, [voiceOn, vsnap.fatal, vsnap.error]);

  return {
    voiceOn, toggleVoice,
    settingsOpen, setSettingsOpen,
    orbRef, vsession, vsnap, vprefs, vupdatePrefs,
  };
}
