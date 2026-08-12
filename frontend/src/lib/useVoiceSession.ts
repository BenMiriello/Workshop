// React binding for the voice client.
//
// It owns one VoiceClient per contextKey, loads and persists per-owner prefs,
// and exposes the server's state through useSyncExternalStore. It no longer
// owns a transport, a fallback path or any turn logic: the conversation lives
// on the server (services/voice/machine.py) and arrives here as snapshots.
//
// The whole of `startReply` is gone. It used to assemble a turn out of a POST,
// an SSE stream and a blocking fallback, with a cancel and a commit endpoint
// each racing the others, and that arrangement is why the session needed an
// epoch counter to make stale continuations no-ops.

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { voiceApi } from "./voiceApi";
import { VoiceClient, type VoiceSnapshot } from "./voice/client";
import { DEFAULT_PREFS, normalizeModes, type VoicePrefs } from "./voice/modes";

export type { VoicePrefs } from "./voice/modes";
export type { VoiceSnapshot } from "./voice/client";

export function useVoiceSession(
  contextKey: string,
  model?: string,
  threadId?: string,
  onThreadBound?: (id: string) => void,
) {
  const [prefs, setPrefsState] = useState<VoicePrefs>({ ...DEFAULT_PREFS });
  const prefsRef = useRef<VoicePrefs>(prefs);
  const onBoundRef = useRef(onThreadBound);
  onBoundRef.current = onThreadBound;
  // The 400ms persist debounce accumulates the WHOLE set of fields changed in
  // the window and PUTs them together, so a rapid Voice-then-Speed change does
  // not drop the Voice save.
  const pendingPatch = useRef<Partial<VoicePrefs>>({});
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ONE client per scope, and NOT keyed on the thread.
  //
  // Keying it on `threadId` meant that binding a thread rebuilt the client, so
  // a brand-new voice chat restarted itself the moment the server told it which
  // chat it was. `setBinding` below keeps the live one in step instead.
  const session = useMemo(
    () => new VoiceClient({
      contextKey,
      model,
      threadId: threadId ?? null,
      prefs: prefsRef.current,
      onThreadBound: (id) => onBoundRef.current?.(id),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see setBinding
    [contextKey],
  );

  useEffect(() => {
    session.setBinding(threadId ?? null, model);
  }, [session, threadId, model]);

  const snapshot = useSyncExternalStore<VoiceSnapshot>(
    (cb) => session.subscribe(cb),
    () => session.snapshot(),
  );

  // Load persisted prefs once and push them into the live client.
  useEffect(() => {
    let alive = true;
    voiceApi.getPrefs().then((p) => {
      if (!alive) return;
      const merged: VoicePrefs = normalizeModes({
        ...DEFAULT_PREFS,
        voice: p.voice ?? DEFAULT_PREFS.voice,
        speed: p.speed ?? DEFAULT_PREFS.speed,
        input_mode: (p.input_mode as VoicePrefs["input_mode"]) ?? DEFAULT_PREFS.input_mode,
        output_mode: (p.output_mode as VoicePrefs["output_mode"]) ?? DEFAULT_PREFS.output_mode,
        mic_enabled: p.mic_enabled ?? DEFAULT_PREFS.mic_enabled,
        speak_replies: p.speak_replies ?? DEFAULT_PREFS.speak_replies,
        reply_pace: (p.reply_pace as VoicePrefs["reply_pace"]) ?? DEFAULT_PREFS.reply_pace,
      });
      prefsRef.current = merged;
      setPrefsState(merged);
      session.setPrefs(merged);
    }).catch(() => { /* defaults are usable; a failed read is not a failed session */ });
    return () => { alive = false; };
  }, [session]);

  useEffect(() => () => session.stop(), [session]);

  const updatePrefs = useCallback(
    (patch: Partial<VoicePrefs>) => {
      // THE PATCH IS PASSED, and this is the only call site.
      //
      // `mic_enabled` and `speak_replies` are derived from the modes, so a
      // caller writing only the boolean (the composer's mic button does exactly
      // that) had its value recomputed from a mode nobody changed and discarded
      // on the same line. The button did nothing in both directions. The
      // previous fix landed here and left a second, patchless call site inside
      // the session; there is no second call site now.
      const next = normalizeModes({ ...prefsRef.current, ...patch }, patch);
      prefsRef.current = next;
      setPrefsState(next);
      session.setPrefs(next);
      pendingPatch.current = { ...pendingPatch.current, ...patch };
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        const merged = pendingPatch.current;
        pendingPatch.current = {};
        if (Object.keys(merged).length) voiceApi.putPrefs(merged).catch(() => {});
      }, 400);
    },
    [session],
  );

  // Warm the resident worker ahead of the first spoken turn, so first-turn
  // latency is not paid on the microphone. The socket binds the thread itself;
  // this only spawns the process early.
  const prewarm = useCallback(() => {
    fetch("/api/voice/session", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-workshop-client": "web" },
      body: JSON.stringify({ context_key: contextKey, thread_id: threadId }),
    }).catch(() => {});
  }, [contextKey, threadId]);

  return { session, snapshot, prefs, updatePrefs, prewarm };
}
