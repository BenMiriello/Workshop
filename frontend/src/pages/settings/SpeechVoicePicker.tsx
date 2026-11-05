// Settings, Speech: the voice read aloud uses on this device, and a sample.
//
// The list comes from the server, grouped by language in the order it sends.
// The first entry is the server's default, stored as no choice at all, so a
// device that never picked follows the server when its default changes. A
// stored voice the server no longer has shows as the default.

import { useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import { Button, Notice, ReadOnly, Row, Select, Skeleton } from "@workshop/ui";
import { speechVoicesQuery, type SpeechVoice } from "../../lib/speech/api";
import { speechPrefs, useSpeechPrefs } from "../../lib/speech/prefs";
import { reader } from "../../components/shell/speech/reader";

const SAMPLE = "This is how text will sound when it is read aloud.";
const SAMPLE_SOURCE = "settings-sample";

const voiceLabel = (v: SpeechVoice) => (v.gender ? `${v.name} (${v.gender})` : v.name);

function byLanguage(voices: SpeechVoice[]): [string, SpeechVoice[]][] {
  const groups = new Map<string, SpeechVoice[]>();
  for (const v of voices) groups.set(v.language, [...(groups.get(v.language) ?? []), v]);
  return [...groups];
}

function Sample() {
  const playing = useSyncExternalStore(
    reader.subscribe,
    () => reader.active && reader.getState().source === SAMPLE_SOURCE,
  );
  return playing
    ? <Button size="sm" onClick={() => reader.stop()}><Square aria-hidden /> Stop</Button>
    : <Button size="sm" onClick={() => reader.read(SAMPLE, SAMPLE_SOURCE)}><Play aria-hidden /> Play sample</Button>;
}

export function SpeechVoicePicker() {
  const prefs = useSpeechPrefs();
  const voices = useQuery(speechVoicesQuery);
  if (voices.isPending) return <Skeleton width="12rem" />;
  if (voices.isError) {
    return (
      <Notice
        tone="error"
        detail={voices.error instanceof Error ? voices.error.message : undefined}
        action={<Button size="sm" variant="ghost" onClick={() => void voices.refetch()}>Retry</Button>}
      >
        Could not load the voices.
      </Notice>
    );
  }
  const { voices: list, default: serverDefault } = voices.data;
  if (!list.length) return <ReadOnly value="No voices on this server" />;
  const fallback = list.find((v) => v.id === serverDefault);
  const value = prefs.voice && list.some((v) => v.id === prefs.voice) ? prefs.voice : "";
  return (
    <Row wrap>
      <Select
        value={value}
        onChange={(e) => speechPrefs.set({ voice: e.target.value || null })}
        aria-label="Voice"
      >
        <option value="">{fallback ? `Default: ${voiceLabel(fallback)}` : "Server default"}</option>
        {byLanguage(list).map(([language, group]) => (
          <optgroup key={language} label={language}>
            {group.map((v) => <option key={v.id} value={v.id}>{voiceLabel(v)}</option>)}
          </optgroup>
        ))}
      </Select>
      <Sample />
    </Row>
  );
}
