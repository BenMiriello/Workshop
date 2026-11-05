// Speech for every page and every framed app, mounted once by the shell.
//
// Dictation: the dictate shortcut, or the chip on the focused text field,
// starts listening, and words go where dictation started (dictation.ts has the
// rule): that field, or the clipboard when none was focused. The bar carries
// where words go, Pause and Stop across pages, and a ring marks the field they
// go into. Read aloud: select text and press the button under it, or the read
// shortcut, or a page's own Read aloud control, which reaches the same reader
// through the kit's read-aloud host, as a page's own spoken actions reach
// dictation through the kit's spoken-actions host. While reading, the sentence is marked on
// the page, followed, and a tap reads from another (readHighlight.ts,
// readFollow.ts, readTap.ts). Both shortcuts are set in Settings > Speech.
//
// Nothing here is per field or per page. Focus, keys and selection are
// listened for in the shell's document and in every same-origin frame
// (useSpeechWatch.ts), and the overlays are drawn in the shell's own body,
// above everything, positioned over whichever document the field or selection
// is in. A field opts out with `data-dictation="off"` on it or an ancestor.

import { useEffect, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { provideReadAloud, provideSpokenActions } from "@workshop/ui";
import { useMediaQuery } from "../../../lib/useMediaQuery";
import { composerFor } from "../../../lib/speech/chatTargets";
import { importHistory } from "../../../lib/speech/historyImport";
import { useSpeechPrefs } from "../../../lib/speech/prefs";
import { DestinationRing } from "./DestinationRing";
import { appActionsHost } from "./appActionRegistry";
import { DictationChip } from "./DictationChip";
import { DictationPill } from "./DictationPill";
import { ElsewhereBar } from "./ElsewhereBar";
import { dictation, type DictationState } from "./dictation";
import { LabelsLayer } from "./labels/LabelsLayer";
import { pageLabels } from "./labels/pageLabels";
import { NumberedDialog } from "./NumberedDialog";
import { watchNumbers } from "./numbered";
import { keepOn } from "./stopping";
import { useChipSpot } from "./useChipSpot";
import { useDockTop } from "./useDockTop";
import { LAYER, useSpeechWatch } from "./useSpeechWatch";
import { reader } from "./reader";
import { readerHost } from "./readerHost";
import { ReadAloudPlayer } from "./ReadAloudPlayer";
import { watchFollow } from "./readFollow";
import { watchReadHighlight } from "./readHighlight";
import { watchReadTap } from "./readTap";
import { SelectionRead } from "./SelectionRead";
import { addSelection } from "../beside/attach";
import "./speech.css";
import "./dictation.css";

/** The element words are going into now, when it is on a page. */
function destinationEl(s: DictationState): HTMLElement | null {
  const d = s.into;
  if (d?.kind === "field") return d.target?.el.isConnected ? d.target.el : null;
  if (d?.kind === "chat") return composerFor(d.chat);
  return null;
}

export function SpeechLayer() {
  const touch = useMediaQuery("(pointer: coarse)");
  const dict = useSyncExternalStore(dictation.subscribe, dictation.getState, dictation.getState);
  const prefs = useSpeechPrefs();
  const { field, selected, clearSelection } = useSpeechWatch();
  // The layer portals into the document body, which a server render has not got.
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    reader.probe();
    void importHistory();
    const resume = keepOn.resumeInto();
    if (resume) void dictation.start(null, resume);
    const unwatch = watchNumbers((on) => dictation.setNumbers(on));
    const unprovide = provideReadAloud(readerHost(reader));
    const offRead = [watchReadHighlight(), watchFollow(), watchReadTap(), provideSpokenActions(appActionsHost)];
    return () => { unwatch(); unprovide(); offRead.forEach((off) => off()); };
  }, []);

  // Labels are for speaking to the page; when dictation ends they go.
  useEffect(() => { if (!dictation.busy) pageLabels.hide(); }, [dict.phase]);

  // An error belongs to the field it happened in. Focusing another field
  // moves on from it, so that field gets its own idle chip.
  useEffect(() => {
    if (field && dict.phase === "error" && field !== dict.originEl) dictation.dismiss();
  }, [field, dict.phase, dict.originEl]);

  // While dictating, the chip sits on the field the words are going into, or
  // offers "Dictate here" on another field that is focused. An error stays
  // with the field it happened in. At rest it shows only when the mic button
  // is wanted in fields.
  const destEl = dictation.busy ? destinationEl(dict) : null;
  const here = dictation.busy && !!dict.here && !!field && field !== destEl;
  const showIdle = prefs.dictation && prefs.micButton === "fields";
  const anchor = here ? field
    : dictation.busy ? destEl
    : dict.phase === "error" ? dict.originEl
    : showIdle ? field : null;
  const spot = useChipSpot(anchor, touch);
  const dockTop = useDockTop(touch);

  const selectionBar = selected && (prefs.selectionButton || !selected.inChat) ? (
    <SelectionRead
      box={selected.box}
      docked={touch}
      onRead={prefs.selectionButton ? () => {
        reader.read(selected.text, "selection", selected.range);
        clearSelection();
      } : null}
      onAddToChat={selected.inChat ? null : () => {
        addSelection(selected.text, selected.doc);
        clearSelection();
      }}
    />
  ) : null;

  if (!mounted) return null;
  return createPortal(
    <div className={LAYER}>
      {destEl && <DestinationRing el={destEl} />}
      {anchor && spot && (
        <DictationChip
          state={dict}
          spot={spot}
          touch={touch}
          here={here}
          onPress={() => (here ? dictation.dictateHere() : dictation.toggle(anchor))}
          onRetry={() => dictation.retry()}
          onClose={() => dictation.dismiss()}
        />
      )}
      {selectionBar && !touch && selectionBar}
      <LabelsLayer />
      <NumberedDialog />
      <div className={"ws-speech-dock" + (dockTop !== null ? " ws-speech-dock--top" : "")} style={dockTop !== null ? { top: dockTop } : undefined}>
        {selectionBar && touch && selectionBar}
        <DictationPill state={dict} showError={!(anchor && spot)} />
        <ElsewhereBar />
        <ReadAloudPlayer />
      </div>
    </div>,
    document.body,
  );
}
