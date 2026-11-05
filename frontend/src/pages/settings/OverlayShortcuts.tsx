// Settings > Apps > Open over any page: each running app that can open over
// the page, with the shortcut that opens it on this device. Change records the
// next chord pressed (Esc cancels); Reset goes back to the one the app
// declares. The control is marked `data-dictation="off"` so the speech layer
// leaves the chord being recorded alone.

import { Fragment, useState } from "react";
import { Button, Notice, Row, SectionLabel, Stack, Table, TableScroll } from "@workshop/ui";
import { chordConflict } from "../../lib/speech/chordConflicts";
import { speechPrefs } from "../../lib/speech/prefs";
import {
  chordFromEvent, chordKeys, isMacLike, sameChord, type Chord,
} from "../../lib/speech/shortcuts";
import { chordFor, overlayShortcuts, useOverlayShortcuts } from "../../lib/overlayApps/prefs";
import { useOverlayApps, type OverlayApp } from "../../lib/overlayApps/store";

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

function ShortcutCell({ app, mac }: { app: OverlayApp; mac: boolean }) {
  const overrides = useOverlayShortcuts();
  const [listening, setListening] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const chord = chordFor(app.slug, app.shortcut, overrides, mac);
  const conflict = chord && chordConflict(chord, mac);

  const stop = () => { setListening(false); setNote(null); };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!listening) return;
    e.preventDefault();
    e.stopPropagation();
    const bare = !e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey;
    if (e.key === "Escape" && bare) { stop(); return; }
    const got = chordFromEvent(e.nativeEvent);
    if (!got) return;
    if ("refused" in got) {
      setNote(`Hold ${mac ? "Control, Option or Cmd" : "Ctrl, Alt or Win"} with the key.`);
      return;
    }
    const speech = speechPrefs.get().shortcuts;
    if (sameChord(got.chord, speech.dictate) || sameChord(got.chord, speech.read)) {
      setNote("That is a speech shortcut.");
      return;
    }
    overlayShortcuts.set(app.slug, got.chord);
    stop();
  };

  return (
    <div data-dictation="off">
      <Row wrap>
        {listening
          ? <span className="ws-muted" aria-live="polite">Press the new shortcut. Esc cancels.</span>
          : chord ? <Keys chord={chord} mac={mac} /> : <span className="ws-muted">None</span>}
        <Button size="sm" aria-pressed={listening} onKeyDown={onKeyDown} onBlur={stop}
          onClick={() => (listening ? stop() : setListening(true))}>
          {listening ? "Cancel" : "Change"}
        </Button>
        {!listening && overrides[app.slug] && (
          <Button size="sm" variant="ghost" onClick={() => overlayShortcuts.reset(app.slug)}>Reset</Button>
        )}
      </Row>
      {listening && note && <Notice tone="warn">{note}</Notice>}
      {!listening && conflict && <Notice tone="warn">{conflict} It may not reach this page.</Notice>}
    </div>
  );
}

/** Nothing at all when no running app opens over the page: the catalog above
 *  is where an app is turned on. */
export function OverlayShortcuts() {
  const { apps } = useOverlayApps();
  const mac = isMacLike();
  if (apps.length === 0) return null;
  return (
    <Stack>
      <SectionLabel>Open over any page</SectionLabel>
      <TableScroll>
        <Table>
          <thead>
            <tr><th>App</th><th>Shortcut on this device</th></tr>
          </thead>
          <tbody>
            {apps.map((a) => (
              <tr key={a.slug}>
                <td>{a.title}</td>
                <td><ShortcutCell app={a} mac={mac} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      </TableScroll>
    </Stack>
  );
}
