import { useEffect, useRef, useState } from "react";
import { ChevronDown, Mic } from "lucide-react";
import { Modal, SectionLabel, Select, Toggle, Segmented, Button, ProgressBar, StatusPill } from "../ui";
import { voiceApi } from "../lib/voiceApi";
import type { EngineCaps } from "../lib/voice";
import type {
  VoicePrefs, VoiceSession, VoiceDiagnostics, MicTestResult, ModalityMode,
} from "../lib/voiceSession";

// Human label for a resolved engine tier, used in the "Auto (X)" hints.
const ENGINE_LABEL: Record<string, string> = {
  server: "Local",
  browser: "Browser",
  native: "Device",
};

// Keep a stored pref inside its slider's range so an out-of-range persisted value
// can never render a thumb off-track (clamp on READ, not just on change).
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

const ms = (v: number | null) => (v == null ? "—" : `${v} ms`);

// Voice settings sheet. Lives in the shared Modal (size="lg" so the control list
// fits on desktop and scrolls its body rather than overflowing the viewport).
// The modality matrix and four essentials are always visible; engine knobs live
// in "Advanced" and measurements in "Diagnostics". Every control here is a view
// preference — commits on change.
export default function VoiceSettings({
  open, onClose, prefs, updatePrefs, caps, session,
}: {
  open: boolean;
  onClose: () => void;
  prefs: VoicePrefs;
  updatePrefs: (patch: Partial<VoicePrefs>) => void;
  caps: EngineCaps | null;
  session: VoiceSession;
}) {
  const [voices, setVoices] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [diagOpen, setDiagOpen] = useState(false);
  const [diag, setDiag] = useState<VoiceDiagnostics | null>(null);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<MicTestResult | null>(null);
  const testSeq = useRef(0);

  useEffect(() => {
    if (!open) return;
    voiceApi.voices().then((r) => setVoices(r.voices || [])).catch(() => {});
  }, [open]);

  // Poll diagnostics only while the section is actually visible. The raw level
  // moves at frame rate and deliberately never enters the session snapshot (it
  // would re-render the whole chat every frame); 10 Hz is plenty for a meter.
  useEffect(() => {
    if (!open || !diagOpen) return;
    const tick = () => setDiag(session.diagnostics());
    tick();
    const t = setInterval(tick, 100);
    return () => clearInterval(t);
  }, [open, diagOpen, session]);

  const ttsServerOk = !!caps?.server?.tts.available;
  const sttServerOk = !!caps?.server?.stt.available;
  const browserTtsOk = !!caps?.browserTts;
  const browserSttOk = !!caps?.browserStt;
  const serverTts = ttsServerOk;
  const autoTtsLabel = caps?.tts ? `Auto (${ENGINE_LABEL[caps.tts] ?? caps.tts})` : "Auto";
  const autoSttLabel = caps?.stt ? `Auto (${ENGINE_LABEL[caps.stt] ?? caps.stt})` : "Auto";
  const engineHint = caps && !caps.server ? "Voice server offline" : undefined;

  const runMicTest = async () => {
    const seq = ++testSeq.current;
    setTesting(true);
    setTestResult(null);
    const r = await session.testMic();
    if (seq !== testSeq.current) return;   // sheet closed / retested meanwhile
    setTestResult(r);
    setTesting(false);
  };

  return (
    <Modal open={open} onClose={onClose} title="Voice settings" size="lg">
      <div className="ws-voice-settings">
        {/* The modality matrix. One concept for what used to be four booleans
            spread across three resolution sites — and the only place that says,
            in one line each, how this conversation is carried. Any of the nine
            combinations is legal. */}
        <div className="ws-voice-settings-group">Conversation</div>
        <label className="ws-voice-setting">
          <SectionLabel>How I talk to you</SectionLabel>
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
              ? "The mic stays off. Nothing here is an error — it's the setting."
              : prefs.input_mode === "voice"
                ? "Hands-free: the mic is the only way in."
                : "Talk or type, whichever is to hand."}
          </span>
        </label>
        <label className="ws-voice-setting">
          <SectionLabel>How I answer</SectionLabel>
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
              ? "Captions only. Interrupting and resuming still work — text you've read counts as heard."
              : prefs.output_mode === "voice"
                ? "Spoken aloud, no transcript in the log."
                : "Spoken aloud and written down."}
          </span>
        </label>

        <div className="ws-voice-settings-group">Voice</div>
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
            value={clamp(prefs.speed, 0.7, 1.4)}
            onChange={(e) => updatePrefs({ speed: Number(e.target.value) })}
          />
        </label>
        <label className="ws-voice-setting">
          <SectionLabel>Pause before reply {(prefs.endpoint_silence_ms / 1000).toFixed(2)}s</SectionLabel>
          <input
            className="ws-range"
            type="range" min={300} max={2000} step={50}
            value={clamp(prefs.endpoint_silence_ms, 300, 2000)}
            onChange={(e) => updatePrefs({ endpoint_silence_ms: Number(e.target.value) })}
          />
          <span className="ws-voice-hintline">How long a silence before I take my turn.</span>
        </label>

        <div className="ws-disclosure" data-open={advancedOpen}>
          <button
            type="button"
            className="ws-disclosure__summary"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((o) => !o)}
          >
            <ChevronDown size={14} strokeWidth={1.8} aria-hidden className="ws-disclosure__chevron" />
            Advanced
          </button>
          {advancedOpen && (
            <div className="ws-disclosure__body">
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
              <label className="ws-voice-setting ws-voice-setting--row">
                <SectionLabel>Fast responses</SectionLabel>
                <Toggle checked={prefs.eager_endpoint} onChange={(v) => updatePrefs({ eager_endpoint: v })} />
              </label>
              <label className="ws-voice-setting ws-voice-setting--row">
                <SectionLabel>Talk over me to interrupt</SectionLabel>
                <Toggle checked={prefs.barge_in} onChange={(v) => updatePrefs({ barge_in: v })} />
              </label>
              <label className="ws-voice-setting">
                <SectionLabel>Mic sensitivity</SectionLabel>
                <input
                  className="ws-range"
                  type="range" min={0} max={1} step={0.05}
                  value={clamp(prefs.barge_sensitivity, 0, 1)}
                  onChange={(e) => updatePrefs({ barge_sensitivity: Number(e.target.value) })}
                />
                <span className="ws-voice-hintline">
                  Floor for the onset threshold. The measured noise floor raises it when the room is loud.
                </span>
              </label>
              <label className="ws-voice-setting ws-voice-setting--row">
                <SectionLabel>Typing holds my turn</SectionLabel>
                <Toggle checked={prefs.typing_hold} onChange={(v) => updatePrefs({ typing_hold: v })} />
              </label>
            </div>
          )}
        </div>

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
              <div className="ws-voice-setting">
                <SectionLabel>Input level</SectionLabel>
                <ProgressBar
                  value={diag ? diag.rawLevel : 0}
                  max={0.3}
                  status={diag && diag.rawLevel > diag.threshold ? "running" : "ok"}
                />
                <span className="ws-voice-hintline">
                  {diag
                    ? diag.calibrated
                      ? `floor ${diag.noiseFloor.toFixed(3)} · speech above ${diag.threshold.toFixed(3)} · now ${diag.rawLevel.toFixed(3)}`
                      : "calibrating the room's noise floor…"
                    : "—"}
                </span>
              </div>
              <dl className="ws-voice-diag">
                <dt>Capture</dt>
                <dd>
                  <StatusPill status={diag?.micSuspended ? "stopped" : diag?.micRunning ? "ok" : "warn"}>
                    {diag?.micSuspended ? "Off by setting" : diag?.micRunning ? "Open" : "Closed"}
                  </StatusPill>
                </dd>
                <dt>Device</dt>
                <dd>{diag?.deviceLabel || "—"}</dd>
                <dt>Secure origin</dt>
                <dd>{diag?.secureContext ? "yes" : "no — voice input needs https or localhost"}</dd>
                <dt>Hearing</dt>
                <dd>
                  {caps?.stt ? ENGINE_LABEL[caps.stt] ?? caps.stt : "—"}
                  {caps?.server?.stt.model ? ` · ${caps.server.stt.model}` : ""}
                </dd>
                <dt>Speech</dt>
                <dd>{caps?.tts ? ENGINE_LABEL[caps.tts] ?? caps.tts : "—"}</dd>
                <dt>Last transcription</dt>
                <dd>{ms(diag?.lastSttMs ?? null)}</dd>
                <dt>Last reply latency</dt>
                <dd>{ms(diag?.lastTurnMs ?? null)}</dd>
              </dl>
              <div className="ws-voice-setting">
                <Button onClick={() => void runMicTest()} loading={testing}>
                  <Mic size={16} aria-hidden />
                  Test microphone
                </Button>
                <span className="ws-voice-hintline">
                  {testing
                    ? "Recording 3 seconds — say something."
                    : testResult
                      ? testResult.ok
                        ? testResult.text
                          ? `Heard: "${testResult.text}" (${testResult.bytes} bytes, ${testResult.ms} ms)`
                          : `Recorded ${testResult.bytes} bytes but the transcript came back empty — the mic is open and the engine answered, so this is a level or a language problem.`
                        : testResult.reason
                      : "Records 3 seconds and transcribes it through the real path."}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
