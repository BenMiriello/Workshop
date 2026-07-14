import { useEffect, useState } from "react";
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

// Voice settings sheet. Lives in the shared Modal (size="lg" so the tall control
// list fits on desktop and scrolls its body rather than overflowing the viewport).
// Every control here is a view preference — it commits on change.
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
            value={prefs.speed}
            onChange={(e) => updatePrefs({ speed: Number(e.target.value) })}
          />
        </label>
        <div className="ws-voice-settings-group">Engines</div>
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
        <div className="ws-voice-settings-group">Turn-taking</div>
        <label className="ws-voice-setting">
          <SectionLabel>Pause before reply {(prefs.endpoint_silence_ms / 1000).toFixed(2)}s</SectionLabel>
          <input
            className="ws-range"
            type="range" min={300} max={2000} step={50}
            value={prefs.endpoint_silence_ms}
            onChange={(e) => updatePrefs({ endpoint_silence_ms: Number(e.target.value) })}
          />
          <span className="ws-voice-hintline">How long a silence before I take my turn.</span>
        </label>
        <label className="ws-voice-setting ws-voice-setting--row">
          <SectionLabel>Think during pauses</SectionLabel>
          <Toggle checked={prefs.eager_endpoint} onChange={(v) => updatePrefs({ eager_endpoint: v })} />
        </label>
        {prefs.eager_endpoint && (
          <label className="ws-voice-setting">
            <SectionLabel>Eager pause {(prefs.eager_silence_ms / 1000).toFixed(2)}s</SectionLabel>
            <input
              className="ws-range"
              type="range" min={200} max={800} step={50}
              value={prefs.eager_silence_ms}
              onChange={(e) => updatePrefs({ eager_silence_ms: Math.min(Number(e.target.value), prefs.endpoint_silence_ms - 200) })}
            />
          </label>
        )}
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
          <SectionLabel>Let me talk over you</SectionLabel>
          <Toggle checked={prefs.barge_in} onChange={(v) => updatePrefs({ barge_in: v })} />
        </label>
        {prefs.barge_in && (
          <label className="ws-voice-setting">
            <SectionLabel>How easily I cut in</SectionLabel>
            <input
              className="ws-range"
              type="range" min={0} max={1} step={0.05}
              value={prefs.barge_sensitivity}
              onChange={(e) => updatePrefs({ barge_sensitivity: Number(e.target.value) })}
            />
            <div className="ws-voice-rangeends"><span>Have to speak up</span><span>Hair trigger</span></div>
            <span className="ws-voice-hintline">How loud you must talk over me to stop me mid-sentence.</span>
          </label>
        )}
        <div className="ws-voice-settings-group">Typing &amp; input</div>
        <label className="ws-voice-setting ws-voice-setting--row">
          <SectionLabel>Mic on</SectionLabel>
          <Toggle checked={prefs.mic_enabled} onChange={(v) => updatePrefs({ mic_enabled: v })} />
        </label>
        <label className="ws-voice-setting ws-voice-setting--row">
          <SectionLabel>Enter interrupts speech</SectionLabel>
          <Toggle checked={prefs.enter_interrupts} onChange={(v) => updatePrefs({ enter_interrupts: v })} />
        </label>
        <label className="ws-voice-setting ws-voice-setting--row">
          <SectionLabel>Typing holds my turn</SectionLabel>
          <Toggle checked={prefs.typing_hold} onChange={(v) => updatePrefs({ typing_hold: v })} />
        </label>
        {prefs.typing_hold && (
          <label className="ws-voice-setting">
            <SectionLabel>Hold window {(prefs.typing_hold_ms / 1000).toFixed(1)}s</SectionLabel>
            <input
              className="ws-range"
              type="range" min={1000} max={10000} step={500}
              value={prefs.typing_hold_ms}
              onChange={(e) => updatePrefs({ typing_hold_ms: Number(e.target.value) })}
            />
          </label>
        )}
      </div>
    </Modal>
  );
}
