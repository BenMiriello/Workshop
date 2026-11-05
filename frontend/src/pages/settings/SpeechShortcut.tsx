// Settings, Speech: one shortcut chord, shown as keys, with Change and Reset.
//
// Change listens for the next chord pressed while its button has focus. Esc
// stops listening; a key without Ctrl, Alt or Cmd is refused, because typing
// would set it off; the other speech action's chord is refused, because one
// of the two would never fire. A chord the browser or the system commonly
// takes is kept but warned about. The whole control is marked
// `data-dictation="off"` so the speech layer does not act on the chord being
// recorded.

import { Fragment, useEffect, useRef, useState } from "react";
import { Button, Notice, Row } from "@workshop/ui";
import { chordConflict } from "../../lib/speech/chordConflicts";
import { speechPrefs, useSpeechPrefs } from "../../lib/speech/prefs";
import {
  chordFromEvent, chordKeys, DEFAULT_CHORDS, isMacLike, sameChord, type Chord, type SpeechChords,
} from "../../lib/speech/shortcuts";

type Which = keyof SpeechChords;

const OTHER_NAME: Record<Which, string> = { dictate: "read aloud", read: "dictation" };

function Keys({ chord, mac }: { chord: Chord; mac: boolean }) {
  return (
    <span>
      {chordKeys(chord, mac).map((k, i) => (
        <Fragment key={k}>
          {i > 0 && "+"}
          <kbd className="ws-kbd">{k}</kbd>
        </Fragment>
      ))}
    </span>
  );
}

export function SpeechShortcut({ which }: { which: Which }) {
  const prefs = useSpeechPrefs();
  const [listening, setListening] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const mac = isMacLike();
  const chord = prefs.shortcuts[which];
  const other: Which = which === "dictate" ? "read" : "dictate";
  const conflict = chordConflict(chord, mac);

  useEffect(() => {
    if (listening) wrap.current?.querySelector<HTMLButtonElement>("[data-recorder]")?.focus();
  }, [listening]);

  const set = (next: Chord) => speechPrefs.set({ shortcuts: { ...speechPrefs.get().shortcuts, [which]: next } });

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!listening) return;
    e.preventDefault();
    e.stopPropagation();
    const bare = !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey;
    if (e.key === "Escape" && bare) { setListening(false); setNote(null); return; }
    const got = chordFromEvent(e.nativeEvent);
    if (!got) return;
    if ("refused" in got) {
      setNote(`Hold ${mac ? "Control, Option or Cmd" : "Ctrl, Alt or Win"} with the key.`);
      return;
    }
    if (sameChord(got.chord, prefs.shortcuts[other])) {
      setNote(`That is already the ${OTHER_NAME[which]} shortcut.`);
      return;
    }
    set(got.chord);
    setListening(false);
    setNote(null);
  };

  const stopListening = () => { setListening(false); setNote(null); };

  return (
    <div ref={wrap} data-dictation="off">
      <Row wrap>
        {listening
          ? <span className="ws-muted" aria-live="polite">Press the new shortcut. Esc cancels.</span>
          : <Keys chord={chord} mac={mac} />}
        <Button
          size="sm"
          data-recorder=""
          aria-pressed={listening}
          onClick={() => (listening ? stopListening() : setListening(true))}
          onKeyDown={onKeyDown}
          onBlur={stopListening}
        >
          {listening ? "Cancel" : "Change"}
        </Button>
        {!listening && !sameChord(chord, DEFAULT_CHORDS[which]) && (
          <Button size="sm" variant="ghost" onClick={() => set(DEFAULT_CHORDS[which])}>Reset</Button>
        )}
      </Row>
      {listening && note && <Notice tone="warn">{note}</Notice>}
      {!listening && conflict && <Notice tone="warn">{conflict} It may not reach this page.</Notice>}
    </div>
  );
}
