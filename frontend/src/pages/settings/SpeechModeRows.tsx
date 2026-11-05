// The mode dictation starts in and the trigger word, at the top of Settings >
// Speech > Spoken commands, with a button that makes every command need the
// trigger word, then how many controls "show labels" numbers at most, and the
// menu on each command for when it is heard (shown only when it is not the
// usual). Saved as each is changed.

import { useEffect, useState } from "react";
import { Ellipsis } from "lucide-react";
import { Button, Input, Menu, MenuItem, Row as Line, Select, SettingsRow as Row } from "@workshop/ui";
import {
  LABELS_RANGE, saveSpeechLabels, saveSpeechMode, SPEECH_MODES, WHEN_LABELS, WHEN_SHORT, type CommandWhen, type SpeechCommands, type SpeechMode,
} from "../../lib/speech/commandsApi";

export function SpeechModeRows({ c, onFail, onAllTrigger }: {
  c: SpeechCommands;
  onFail: () => void;
  /** Every command that is on needs the trigger word from now on. */
  onAllTrigger: () => void;
}) {
  const allTrigger = [...c.builtins, ...c.custom].every((x) => x.when !== "alone");
  const [trigger, setTrigger] = useState(c.trigger);
  useEffect(() => setTrigger(c.trigger), [c.trigger]);
  const [most, setMost] = useState(String(c.labels.max));
  useEffect(() => setMost(String(c.labels.max)), [c.labels.max]);
  const saveMost = () => {
    const n = Math.round(Number(most));
    if (!most.trim() || !Number.isFinite(n)) return setMost(String(c.labels.max));
    const max = Math.min(Math.max(n, LABELS_RANGE.min), LABELS_RANGE.max);
    setMost(String(max));
    if (max !== c.labels.max) void saveSpeechLabels({ max }).catch(onFail);
  };
  const saveTrigger = () => {
    const t = trigger.trim();
    if (!t) setTrigger(c.trigger);
    else if (t !== c.trigger) void saveSpeechMode(c.mode, t).catch(onFail);
  };
  return (
    <>
      <Row label="mode">
        <Select
          value={c.mode}
          aria-label="Command mode"
          onChange={(e) => { void saveSpeechMode(e.target.value as SpeechMode).catch(onFail); }}
        >
          {SPEECH_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </Select>
      </Row>
      <Row label="trigger word">
        <Line wrap>
          <Input
            value={trigger}
            aria-label="Trigger word"
            data-dictation="off"
            onChange={(e) => setTrigger(e.target.value)}
            onBlur={saveTrigger}
            onKeyDown={(e) => { if (e.key === "Enter") saveTrigger(); }}
          />
          <Button size="sm" variant="ghost" disabled={allTrigger} onClick={onAllTrigger}>Need it for every command</Button>
        </Line>
      </Row>
      <Row label="most labels" hint={`${LABELS_RANGE.min} to ${LABELS_RANGE.max}`}>
        <Input
          type="number"
          min={LABELS_RANGE.min}
          max={LABELS_RANGE.max}
          value={most}
          aria-label="Most labels"
          data-dictation="off"
          onChange={(e) => setMost(e.target.value)}
          onBlur={saveMost}
          onKeyDown={(e) => { if (e.key === "Enter") saveMost(); }}
        />
      </Row>
    </>
  );
}

export function WhenMenu({ when, label, onChange }: {
  when: CommandWhen;
  label: string;
  onChange: (when: CommandWhen) => void;
}) {
  return (
    <Menu
      label={`When ${label} is heard`}
      trigger={
        <Button size="sm" variant="ghost" aria-label={`When ${label} is heard`}>
          <Ellipsis aria-hidden />
          {when !== "alone" && WHEN_SHORT[when]}
        </Button>
      }
    >
      {(Object.keys(WHEN_LABELS) as CommandWhen[]).map((w) => (
        <MenuItem key={w} checked={w === when} onSelect={() => onChange(w)}>{WHEN_LABELS[w]}</MenuItem>
      ))}
    </Menu>
  );
}
