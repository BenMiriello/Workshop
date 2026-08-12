import { useEffect, useRef, useState } from "react";
import { ChevronDown, Mic } from "lucide-react";
import { Modal, SectionLabel, Select, Segmented, Button, StatusPill } from "@workshop/ui";
import { voiceApi } from "../lib/voiceApi";
import type { VoiceClient } from "../lib/voice/client";
import type { ModalityMode, ReplyPace, VoicePrefs } from "../lib/voice/modes";

// Keep a stored pref inside its slider's range so an out-of-range persisted value
// can never render a thumb off-track (clamp on READ, not just on change).
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

// What each perception tier means, said plainly. The sheet reports which one is
// RUNNING rather than which one is configured: with the ONNX weights absent it
// has to be able to say so instead of implying accuracy the box does not have.
const TIER_LABEL: Record<string, string> = {
  silero: "Silero",
  energy: "Level based",
  "smart-turn": "Smart Turn",
  heuristic: "Rule based",
};

// Voice settings. Eight knobs became three, because the rest were tuning
// mechanisms that no longer exist: endpoint_silence_ms, eager_endpoint and
// eager_silence_ms all tried to answer "have they finished speaking" with a
// stopwatch, which no value can do, and barge_sensitivity was presented as an
// interruption preference while actually being the master gate on whether you
// were heard at all. A knob whose correct value the system can measure is a bug
// wearing a control.
export default function VoiceSettings({
  open, onClose, prefs, updatePrefs, session,
}: {
  open: boolean;
  onClose: () => void;
  prefs: VoicePrefs;
  updatePrefs: (patch: Partial<VoicePrefs>) => void;
  session: VoiceClient;
}) {
  const [voices, setVoices] = useState<string[]>([]);
  const [diagOpen, setDiagOpen] = useState(false);
  const [diag, setDiag] = useState<ReturnType<VoiceClient["diagnostics"]> | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text?: string; reason?: string } | null>(null);
  const testSeq = useRef(0);

  useEffect(() => {
    if (!open) return;
    voiceApi.voices().then((r) => setVoices(r.voices || [])).catch(() => {});
    void session.probeCaps();
  }, [open, session]);

  useEffect(() => {
    if (!open || !diagOpen) return;
    const tick = () => setDiag(session.diagnostics());
    tick();
    const t = setInterval(tick, 500);
    return () => clearInterval(t);
  }, [open, diagOpen, session]);

  const snap = session.snapshot();
  const ttsOk = snap.engines.tts;

  const runMicTest = async () => {
    const seq = ++testSeq.current;
    setTesting(true);
    setTestResult(null);
    const r = await session.testMic();
    if (seq !== testSeq.current) return;   // sheet closed or retested meanwhile
    setTestResult(r);
    setTesting(false);
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Voice settings"
      size="lg"
      // A VISIBLE WAY OUT. Esc and a scrim tap both close this, and on a phone
      // neither is reachable: there is no Esc key, and a sheet this tall leaves
      // almost no scrim to tap. Every control here commits on change, so this
      // only dismisses.
      actions={<Button onClick={onClose}>Done</Button>}
    >
      <div className="ws-voice-settings">
        <div className="ws-voice-settings-group">Conversation</div>
        <label className="ws-voice-setting">
          <SectionLabel>How I send messages</SectionLabel>
          <Segmented<ModalityMode>
            value={prefs.input_mode}
            onChange={(v) => updatePrefs({ input_mode: v })}
            options={[
              { value: "voice", label: "Speak" },
              { value: "text", label: "Type" },
              { value: "both", label: "Either" },
            ]}
          />
          <span className="ws-voice-hintline">
            {prefs.input_mode === "text"
              ? "The mic stays off."
              : prefs.input_mode === "voice"
                ? "Hands-free: the mic is the only way in."
                : "Use whichever suits the moment."}
          </span>
        </label>
        <label className="ws-voice-setting">
          <SectionLabel>How replies come back</SectionLabel>
          <Segmented<ModalityMode>
            value={prefs.output_mode}
            onChange={(v) => updatePrefs({ output_mode: v })}
            options={[
              { value: "voice", label: "Spoken" },
              { value: "text", label: "Written" },
              { value: "both", label: "Both" },
            ]}
          />
          <span className="ws-voice-hintline">
            {prefs.output_mode === "text"
              ? "Captions only. Interrupting and resuming still work."
              : prefs.output_mode === "voice"
                ? "Spoken aloud, no transcript in the log."
                : "Spoken aloud and written down."}
          </span>
        </label>
        <label className="ws-voice-setting">
          <SectionLabel>How soon I reply</SectionLabel>
          <Segmented<ReplyPace>
            value={prefs.reply_pace}
            onChange={(v) => updatePrefs({ reply_pace: v })}
            options={[
              { value: "snappy", label: "Snappy" },
              { value: "natural", label: "Natural" },
              { value: "patient", label: "Patient" },
            ]}
          />
          <span className="ws-voice-hintline">
            {prefs.reply_pace === "snappy"
              ? "Answers fast. May cut in if you pause to think."
              : prefs.reply_pace === "patient"
                ? "Waits through long pauses. Good for dictation."
                : "Waits when you sound unfinished, answers when you sound done."}
          </span>
        </label>

        <div className="ws-voice-settings-group">Speech</div>
        <label className="ws-voice-setting">
          <SectionLabel>Voice</SectionLabel>
          <Select
            value={prefs.voice}
            onChange={(e) => updatePrefs({ voice: e.target.value })}
            disabled={!ttsOk || voices.length === 0}
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
            value={clamp(prefs.speed, 0.7, 1.4)}
            onChange={(e) => updatePrefs({ speed: Number(e.target.value) })}
          />
        </label>

        {/* Diagnostics. The point of this section is that "it isn't hearing me"
            stops being a guess: every layer between the microphone and the
            transcript reports its own state, and the test exercises all of them
            through the same path a real turn uses. */}
        <div className="ws-disclosure" data-open={diagOpen}>
          <button
            type="button"
            className="ws-disclosure__summary"
            aria-expanded={diagOpen}
            onClick={() => setDiagOpen((o) => !o)}
          >
            <ChevronDown size={14} strokeWidth={1.8} aria-hidden className="ws-disclosure__chevron" />
            Diagnostics
          </button>
          {diagOpen && (
            <div className="ws-disclosure__body">
              <dl className="ws-voice-diag">
                <dt>Connection</dt>
                <dd>
                  <StatusPill status={snap.connected ? "ok" : "warn"}>
                    {snap.connected ? "Live" : "Offline"}
                  </StatusPill>
                </dd>
                <dt>Microphone</dt>
                <dd>
                  <StatusPill status={diag?.trackLive ? "ok" : diag?.micRunning ? "warn" : "stopped"}>
                    {diag?.trackLive ? "Open" : diag?.micRunning ? "Stopped" : "Closed"}
                  </StatusPill>
                </dd>
                <dt>Echo cancellation</dt>
                <dd>
                  <StatusPill status={snap.echoCancelled ? "ok" : "warn"}>
                    {snap.echoCancelled ? "On" : "Unavailable"}
                  </StatusPill>
                </dd>
                <dt>Talking over it</dt>
                <dd>
                  {snap.echoCancelled
                    ? "Works. Just start talking."
                    : "Use the stop button. Without echo cancellation the mic hears the reply."}
                </dd>
                <dt>Device</dt>
                <dd>{diag?.deviceLabel || "not open"}</dd>
                <dt>Secure origin</dt>
                <dd>{diag?.secureContext ? "yes" : "no, voice input needs https"}</dd>
                <dt>Speech detection</dt>
                <dd>{TIER_LABEL[snap.tiers.vad] || snap.tiers.vad || "not connected"}</dd>
                <dt>Turn detection</dt>
                <dd>{TIER_LABEL[snap.tiers.turnEnd] || snap.tiers.turnEnd || "not connected"}</dd>
                <dt>Transcription</dt>
                <dd>{snap.engines.stt ? snap.engines.sttModel || "on" : "unavailable"}</dd>
              </dl>
              <div className="ws-voice-setting">
                <Button onClick={() => void runMicTest()} loading={testing}>
                  <Mic size={16} aria-hidden />
                  Test microphone
                </Button>
                <span className="ws-voice-hintline">
                  {testing
                    ? "Transcribing the last few seconds. Say something first."
                    : testResult
                      ? testResult.ok
                        ? testResult.text
                          ? `Heard: "${testResult.text}"`
                          : "Audio arrived, but nothing was transcribed. Check the level and that you spoke recently."
                        : testResult.reason
                      : "Transcribes the last few seconds through the real path."}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
