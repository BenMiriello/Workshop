import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { Modal, SectionLabel, Select, Toggle, Segmented } from "../ui";
import { voiceApi } from "../lib/voiceApi";
import type { EngineCaps } from "../lib/voice";
import type { VoicePrefs } from "../lib/voiceSession";

// Human label for a resolved engine tier, used in the "Auto (X)" hints.
const ENGINE_LABEL: Record<string, string> = {
  server: "Local",
  browser: "Browser",
  native: "Device",
};

// Keep a stored pref inside its slider's range so an out-of-range persisted value
// can never render a thumb off-track (clamp on READ, not just on change).
const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

// Voice settings sheet. Lives in the shared Modal (size="lg" so the control list
// fits on desktop and scrolls its body rather than overflowing the viewport).
// Four essentials are always visible; everything else lives in a collapsed
// "Advanced" disclosure. Every control here is a view preference — commits on change.
export default function VoiceSettings({
  open, onClose, prefs, updatePrefs, caps,
}: {
  open: boolean;
  onClose: () => void;
  prefs: VoicePrefs;
  updatePrefs: (patch: Partial<VoicePrefs>) => void;
  caps: EngineCaps | null;
}) {
  const [voices, setVoices] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    voiceApi.voices().then((r) => setVoices(r.voices || [])).catch(() => {});
  }, [open]);

  const ttsServerOk = !!caps?.server?.tts.available;
  const sttServerOk = !!caps?.server?.stt.available;
  const browserTtsOk = !!caps?.browserTts;
  const browserSttOk = !!caps?.browserStt;
  const serverTts = ttsServerOk;
  const autoTtsLabel = caps?.tts ? `Auto (${ENGINE_LABEL[caps.tts] ?? caps.tts})` : "Auto";
  const autoSttLabel = caps?.stt ? `Auto (${ENGINE_LABEL[caps.stt] ?? caps.stt})` : "Auto";
  const engineHint = caps && !caps.server ? "Voice server offline" : undefined;

  return (
    <Modal open={open} onClose={onClose} title="Voice settings" size="lg">
      <div className="ws-voice-settings">
        <div className="ws-voice-settings-group">Voice</div>
        <label className="ws-voice-setting ws-voice-setting--row">
          <SectionLabel>Speak replies</SectionLabel>
          <Toggle checked={prefs.speak_replies} onChange={(v) => updatePrefs({ speak_replies: v })} />
        </label>
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
                <span className="ws-voice-hintline">onset + interrupt threshold</span>
              </label>
              <label className="ws-voice-setting ws-voice-setting--row">
                <SectionLabel>Mic on</SectionLabel>
                <Toggle checked={prefs.mic_enabled} onChange={(v) => updatePrefs({ mic_enabled: v })} />
              </label>
              <label className="ws-voice-setting ws-voice-setting--row">
                <SectionLabel>Typing holds my turn</SectionLabel>
                <Toggle checked={prefs.typing_hold} onChange={(v) => updatePrefs({ typing_hold: v })} />
              </label>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
