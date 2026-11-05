// Settings, Speech: dictation into text fields and reading text aloud.
//
// Everything here commits on change (design rule 7: a view preference, no
// write to config). The choices are kept per device, beside the microphone
// permission they depend on.

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { Cpu, Mic, Volume2 } from "lucide-react";
import {
  Button, ReadOnly, Segmented, Select, SettingsRow as Row, SettingsSection as Section, Stack, Toggle,
  type SegmentedOption,
} from "@workshop/ui";
import { speechStatusQuery } from "../../lib/speech/api";
import {
  canOpenMicSettings, currentMicClient, micBlockedHelp, micPermission, micSettingsLabel, openMicSettings,
  requestMic, watchMicPermission, type MicPermission,
} from "../../lib/speech/micAccess";
import { speechPrefs, useSpeechPrefs, type MicButton } from "../../lib/speech/prefs";
import { reader, SPEEDS } from "../../components/shell/speech/reader";
import { SpeechCommands } from "./SpeechCommands";
import { SpeechRecordings } from "./SpeechRecordings";
import { SpeechShortcut } from "./SpeechShortcut";
import { SpeechVoicePicker } from "./SpeechVoicePicker";

const MIC_BUTTON_OPTIONS: SegmentedOption<MicButton>[] = [
  { value: "fields", label: "In text fields" },
  { value: "shortcut", label: "Shortcut only" },
];

// Checked on its own and kept current, so the row is a status, not a test to
// run. The one button asks for real: shown only while the client still can
// prompt. Once refused, the client will not ask again from a page, so the row
// says where the setting is and updates when it changes.
function MicAccess() {
  const [state, setState] = useState<MicPermission | null>(null);
  const [refused, setRefused] = useState(false);
  const check = useCallback(() => {
    void micPermission().then((next) => {
      setState(next);
      if (next === "granted") setRefused(false);
    });
  }, []);
  useEffect(() => {
    check();
    return watchMicPermission(check);
  }, [check]);

  const ask = async () => {
    const ok = await requestMic();
    setRefused(!ok);
    check();
  };

  if (state === null) return <ReadOnly value="Checking" />;
  if (state === "granted" && !refused) return <ReadOnly value="Allowed" />;
  if (state === "denied" || refused) {
    const client = currentMicClient();
    return (
      <Stack gap="sm">
        <span className="ws-muted">{micBlockedHelp(client)}</span>
        {canOpenMicSettings() && (
          <div>
            <Button size="sm" onClick={() => void openMicSettings()}>{micSettingsLabel(client)}</Button>
          </div>
        )}
      </Stack>
    );
  }
  return <Button size="sm" onClick={ask}><Mic aria-hidden /> Allow microphone</Button>;
}

function engineWords(device: string, mode: string): string {
  if (device === "remote") return "A speech server";
  if (device === "gpu") return "The GPU, while it is idle";
  return mode === "auto" ? "The processor, and the GPU when it is idle" : "The processor";
}

function Engine() {
  const status = useQuery(speechStatusQuery);
  if (status.isPending) return <ReadOnly value="Checking" />;
  if (status.isError) return <ReadOnly value="Not reachable" />;
  const s = status.data;
  if (!s.stt.available) return <ReadOnly value={s.stt.error || "Not installed"} />;
  return <ReadOnly value={engineWords(s.device, s.mode)} />;
}

export default function SpeechSection() {
  const prefs = useSpeechPrefs();
  const speed = useSyncExternalStore(reader.subscribe, () => reader.getState().speed);
  return (
    <>
      <Section icon={Mic} title="Dictation">
        <Row label="dictation">
          <Toggle checked={prefs.dictation} onChange={(dictation) => speechPrefs.set({ dictation })} />
        </Row>
        {prefs.dictation && (
          <>
            <Row label="mic button">
              <Segmented
                options={MIC_BUTTON_OPTIONS}
                value={prefs.micButton}
                onChange={(micButton) => speechPrefs.set({ micButton })}
                ariaLabel="Mic button"
              />
            </Row>
            <Row label="shortcut">
              <SpeechShortcut which="dictate" />
            </Row>
            <Row label="stop">
              <ReadOnly value="Esc stops dictation" />
            </Row>
            <Row label="keep dictation on" hint="No stop after a long silence, and it starts again after a reload.">
              <Toggle checked={prefs.keepOn} onChange={(keepOn) => speechPrefs.set({ keepOn })} />
            </Row>
            <Row label="microphone">
              <MicAccess />
            </Row>
            <Row label="dictations">
              <SpeechRecordings />
            </Row>
          </>
        )}
      </Section>
      <Section icon={Volume2} title="Read aloud">
        <Row label="button on selected text">
          <Toggle
            checked={prefs.selectionButton}
            onChange={(selectionButton) => speechPrefs.set({ selectionButton })}
          />
        </Row>
        <Row label="highlight what is read">
          <Toggle
            checked={prefs.readHighlight}
            onChange={(readHighlight) => speechPrefs.set({ readHighlight })}
          />
        </Row>
        <Row label="shortcut">
          <SpeechShortcut which="read" />
        </Row>
        <Row label="voice">
          <SpeechVoicePicker />
        </Row>
        <Row label="speed">
          <Select value={String(speed)} onChange={(e) => reader.setSpeed(Number(e.target.value))} aria-label="Reading speed">
            {SPEEDS.map((s) => <option key={s} value={String(s)}>{s === 1 ? "1x (normal)" : `${s}x`}</option>)}
          </Select>
        </Row>
      </Section>
      <Section icon={Cpu} title="Engine">
        <Row label="runs on">
          <Engine />
        </Row>
      </Section>
      {prefs.dictation && <SpeechCommands />}
    </>
  );
}
