import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft, Mic, Square, Play, X, Settings2, Loader, MessageSquare, AudioLines,
} from "lucide-react";
import { Button, StatusPill, SectionLabel, Select, Toggle, Segmented, StateBlock } from "../ui";
import { useVoiceSession } from "../lib/useVoiceSession";
import { voiceApi } from "../lib/voiceApi";
import { detectCaps, type EngineCaps } from "../lib/voice";
import type { VoiceState } from "../lib/voiceSession";

// Human label for a resolved engine tier, used in the "Auto (X)" hints.
const ENGINE_LABEL: Record<string, string> = {
  server: "Local",
  browser: "Browser",
  native: "Device",
};

// The full-screen voice conversation surface. A spoken exchange treated as a
// conversation: an orb that reacts to your voice, a live caption of what is being
// said (with the heard-vs-still-coming distinction made visible), and a queue of
// threads the assistant started but hasn't finished — offered, never forced.

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "Tap to start",
  connecting: "Starting…",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

const STATE_KIND: Record<VoiceState, "ok" | "running" | "warn" | "stopped"> = {
  idle: "stopped",
  connecting: "warn",
  listening: "running",
  thinking: "warn",
  speaking: "ok",
};

export default function VoiceMode() {
  const { contextKey: raw = "" } = useParams();
  const contextKey = decodeURIComponent(raw);
  const navigate = useNavigate();
  const { session, snapshot, prefs, updatePrefs } = useVoiceSession(contextKey);
  const [showSettings, setShowSettings] = useState(false);
  const [voices, setVoices] = useState<string[]>([]);
  const [mountCaps, setMountCaps] = useState<EngineCaps | null>(null);
  const startedRef = useRef(false);

  useEffect(() => {
    voiceApi.voices().then((r) => setVoices(r.voices || [])).catch(() => {});
  }, []);

  // Probe engine capabilities on mount (and again when the settings sheet opens)
  // so provider controls reflect the truth before the session ever starts —
  // otherwise caps are null until start() and everything looks disabled.
  useEffect(() => {
    detectCaps().then(setMountCaps).catch(() => {});
  }, [showSettings]);

  // Esc interrupts the assistant while it is speaking (closing the settings sheet
  // takes precedence when it is open).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (showSettings) { setShowSettings(false); return; }
      session.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [session, showSettings]);

  // Lock the page to the visual viewport (same trick as FocusedChat).
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  const { state, level, partial, lastUser, sentences, queue, caps } = snapshot;
  // Prefer live session caps once started; fall back to the mount-time probe.
  const cap = caps ?? mountCaps;
  const ttsServerOk = !!cap?.server?.tts.available;
  const sttServerOk = !!cap?.server?.stt.available;
  const browserTtsOk = !!cap?.browserTts;
  const browserSttOk = !!cap?.browserStt;
  const serverTts = ttsServerOk;
  const autoTtsLabel = cap?.tts ? `Auto (${ENGINE_LABEL[cap.tts] ?? cap.tts})` : "Auto";
  const autoSttLabel = cap?.stt ? `Auto (${ENGINE_LABEL[cap.stt] ?? cap.stt})` : "Auto";
  const engineHint = cap && !cap.server ? "Voice server offline" : undefined;
  const active = state !== "idle" && state !== "connecting";

  const primaryAction = () => {
    if (state === "idle") { startedRef.current = true; session.start(); return; }
    if (state === "speaking") { session.interrupt(); return; }
    if (state === "listening") { session.forceEndUtterance(); return; }
  };

  const orbStyle = { ["--vlevel" as string]: String(0.15 + level * 0.85) } as React.CSSProperties;

  return (
    <div className="ws-voice">
      <div className="ws-voice-top">
        <Button variant="ghost" size="sm" onClick={() => { session.stop(); navigate(-1); }}>
          <ArrowLeft size={16} aria-hidden /> Back
        </Button>
        <div className="ws-voice-top-mid">
          <AudioLines size={14} aria-hidden />
          <span className="ws-voice-ctx">{contextKey}</span>
        </div>
        <div className="ws-voice-top-right">
          <StatusPill status={STATE_KIND[state]}>{STATE_LABEL[state]}</StatusPill>
          <Button variant="ghost" size="sm" onClick={() => setShowSettings((s) => !s)} aria-label="Voice settings">
            <Settings2 size={16} aria-hidden />
          </Button>
        </div>
      </div>

      {showSettings && (
        <div className="ws-voice-settings">
          <label className="ws-voice-setting">
            <SectionLabel>Voice</SectionLabel>
            <Select
              value={prefs.voice}
              onChange={(e) => updatePrefs({ voice: e.target.value })}
              disabled={!serverTts || voices.length === 0}
            >
              {(voices.length ? voices : [prefs.voice]).map((v) => (
                <option key={v} value={v}>{v}</option>
              ))}
            </Select>
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Speed {prefs.speed.toFixed(2)}x</SectionLabel>
            <input
              className="ws-range"
              type="range" min={0.7} max={1.4} step={0.05}
              value={prefs.speed}
              onChange={(e) => updatePrefs({ speed: Number(e.target.value) })}
            />
          </label>
          <label className="ws-voice-setting ws-voice-setting--row">
            <SectionLabel>Speak replies</SectionLabel>
            <Toggle checked={prefs.speak_replies} onChange={(v) => updatePrefs({ speak_replies: v })} />
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Speech engine</SectionLabel>
            <Segmented<typeof prefs.tts_provider>
              value={prefs.tts_provider === "off" ? "auto" : prefs.tts_provider}
              onChange={(v) => updatePrefs({ tts_provider: v })}
              hint={engineHint}
              options={[
                { value: "auto", label: autoTtsLabel },
                { value: "server", label: "Local", disabled: !ttsServerOk },
                { value: "browser", label: "Browser", disabled: !browserTtsOk },
              ]}
            />
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Hearing engine</SectionLabel>
            <Segmented<typeof prefs.stt_provider>
              value={prefs.stt_provider}
              onChange={(v) => updatePrefs({ stt_provider: v })}
              hint={engineHint}
              options={[
                { value: "auto", label: autoSttLabel },
                { value: "server", label: "Local", disabled: !sttServerOk },
                { value: "browser", label: "Browser", disabled: !browserSttOk },
              ]}
            />
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Pause before reply {(prefs.endpoint_silence_ms / 1000).toFixed(2)}s</SectionLabel>
            <input
              className="ws-range"
              type="range" min={300} max={2000} step={50}
              value={prefs.endpoint_silence_ms}
              onChange={(e) => updatePrefs({ endpoint_silence_ms: Number(e.target.value) })}
            />
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Reply length</SectionLabel>
            <Segmented<typeof prefs.verbosity>
              value={prefs.verbosity}
              onChange={(v) => updatePrefs({ verbosity: v })}
              options={[
                { value: "terse", label: "Terse" },
                { value: "normal", label: "Normal" },
              ]}
            />
          </label>
          <label className="ws-voice-setting ws-voice-setting--row">
            <SectionLabel>Let me interrupt</SectionLabel>
            <Toggle checked={prefs.barge_in} onChange={(v) => updatePrefs({ barge_in: v })} />
          </label>
          <label className="ws-voice-setting">
            <SectionLabel>Interrupt sensitivity</SectionLabel>
            <input
              className="ws-range"
              type="range" min={0} max={1} step={0.05}
              value={prefs.barge_sensitivity}
              onChange={(e) => updatePrefs({ barge_sensitivity: Number(e.target.value) })}
            />
          </label>
        </div>
      )}

      {snapshot.error && (
        <div className="ws-voice-err">
          <StateBlock variant="error" title="Voice error" hint={snapshot.error} />
        </div>
      )}

      <div className="ws-voice-stage">
        <button
          className={`ws-voice-orb is-${state}`}
          style={orbStyle}
          onClick={primaryAction}
          aria-label={state === "speaking" ? "Interrupt" : state === "listening" ? "Send now" : "Start"}
        >
          <span className="ws-voice-orb-core">
            {state === "thinking" || state === "connecting" ? (
              <Loader size={30} aria-hidden className="ws-spin" />
            ) : state === "speaking" ? (
              <Square size={26} aria-hidden />
            ) : (
              <Mic size={30} aria-hidden />
            )}
          </span>
        </button>
        <div className="ws-voice-hint">
          {state === "idle" && "Tap the orb and talk"}
          {state === "listening" && (partial || "Listening… tap to send")}
          {state === "thinking" && "Working on it…"}
          {state === "speaking" && "Talk over me any time to interrupt"}
          {state === "connecting" && "Warming up…"}
        </div>
      </div>

      {active && (lastUser || sentences.length > 0) && (
        <div className="ws-voice-transcript">
          {lastUser && (
            <div className="ws-voice-line ws-voice-line--you">
              <span className="ws-voice-who">You</span>
              <span>{lastUser}</span>
            </div>
          )}
          {sentences.length > 0 && (
            <div className="ws-voice-line ws-voice-line--clawd">
              <span className="ws-voice-who">Clawd</span>
              <span>
                {sentences.map((s, i) => (
                  <span
                    key={i}
                    className={
                      s.status === "heard" ? "ws-sent-heard"
                      : s.status === "speaking" ? "ws-sent-now"
                      : "ws-sent-pending"
                    }
                  >
                    {s.text}{" "}
                  </span>
                ))}
              </span>
            </div>
          )}
        </div>
      )}

      {queue.length > 0 && (
        <div className="ws-voice-queue">
          <SectionLabel>
            <MessageSquare size={13} aria-hidden /> Threads I haven't finished
          </SectionLabel>
          <div className="ws-voice-threads">
            {queue.map((t) => (
              <div key={t.id} className="ws-voice-thread">
                <span className="ws-voice-thread-topic">{t.topic}</span>
                <div className="ws-voice-thread-actions">
                  <Button variant="ghost" size="sm" onClick={() => session.offerThread(t.id)} aria-label="Continue this thread">
                    <Play size={14} aria-hidden />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => session.dismissThread(t.id)} aria-label="Drop this thread">
                    <X size={14} aria-hidden />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {active && (
        <div className="ws-voice-footer">
          <Button variant="ghost" size="sm" onClick={() => session.stop()}>
            End session
          </Button>
          <span className="ws-voice-provider">
            {snapshot.lastTurnMs !== null && `last turn: ${(snapshot.lastTurnMs / 1000).toFixed(1)}s to speech · `}
            {snapshot.sttProvider ? `hear: ${snapshot.sttProvider}` : "no mic"} · {snapshot.ttsProvider ? `speak: ${snapshot.ttsProvider}` : "muted"}
          </span>
        </div>
      )}
    </div>
  );
}
