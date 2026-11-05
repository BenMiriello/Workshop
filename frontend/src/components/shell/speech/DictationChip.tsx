import type { CSSProperties } from "react";
import { AlertCircle, Loader2, Mic, Square, X } from "lucide-react";
import { Button } from "@workshop/ui";
import { CHIP_SIZE, CHIP_SIZE_TOUCH, type Spot } from "../../../lib/speech/placement";
import type { DictationState } from "./dictation";
import {
  canOpenMicSettings, currentMicClient, micSettingsLabel, openMicSettings,
} from "../../../lib/speech/micAccess";
import { keepFocus } from "./press";
import { speechPrefs } from "../../../lib/speech/prefs";
import { chordLabel, isMacLike } from "../../../lib/speech/shortcuts";

const LABEL: Record<DictationState["phase"], string> = {
  idle: "Dictate",
  starting: "Starting dictation",
  listening: "Stop dictation (Esc)",
  finishing: "Finishing",
  error: "Dictation failed",
};

/** `here`: dictating somewhere else; pressing this sends the words to this
 *  field from now on. */
export function DictationChip({ state, spot, touch, here, onPress, onRetry, onClose }: {
  state: DictationState;
  spot: Spot;
  touch: boolean;
  here: boolean;
  onPress: () => void;
  onRetry: () => void;
  onClose: () => void;
}) {
  const { phase, level, error, blocked } = state;
  const chord = chordLabel(speechPrefs.get().shortcuts.dictate, isMacLike());
  const label = here ? "Dictate here" : phase === "idle" ? `${LABEL.idle} (${chord})` : LABEL[phase];
  const style = { left: spot.left, top: spot.top, "--level": level.toFixed(2) } as CSSProperties;
  const icon = here ? <Mic aria-hidden />
    : phase === "listening" ? <Square aria-hidden />
    : phase === "starting" || phase === "finishing" ? <Loader2 className="ws-spin" aria-hidden />
    : phase === "error" ? <AlertCircle aria-hidden />
    : <Mic aria-hidden />;
  return (
    <>
      <button
        type="button"
        className={"ws-speech-chip" + (touch ? " ws-speech-chip--touch" : "")}
        data-phase={here ? "here" : phase}
        data-dictation="off"
        style={style}
        onPointerDown={keepFocus}
        onMouseDown={keepFocus}
        onClick={onPress}
        aria-busy={phase === "starting" || phase === "finishing" || undefined}
        aria-label={phase === "error" && error ? error : label}
        title={phase === "error" && error ? error : label}
      >
        {icon}
      </button>
      {phase === "error" && error && !blocked && (
        <div className="ws-speech-note" role="status" style={notePlace(spot, touch)}>{error}</div>
      )}
      {phase === "error" && error && blocked && (
        <div
          className="ws-speech-note ws-speech-note--blocked"
          role="alert"
          data-dictation="off"
          style={notePlaceBelow(spot, touch)}
          onPointerDown={keepFocus}
          onMouseDown={keepFocus}
        >
          <p>{error}</p>
          <div className="ws-speech-note__actions">
            {canOpenMicSettings() && (
              <Button size="sm" onClick={() => void openMicSettings()}>
                {micSettingsLabel(currentMicClient())}
              </Button>
            )}
            <Button size="sm" onClick={onRetry}>Try again</Button>
            <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
              <X aria-hidden />
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

// The note sits to the chip's left, ending where the chip begins, on the same
// line. Right-anchored, so it grows leftwards and never runs off the screen.
function notePlace(spot: Spot, touch: boolean): CSSProperties {
  const gap = 8;
  return { right: window.innerWidth - spot.left + gap, top: spot.top + (touch ? 6 : 0) };
}

// A note with buttons in it is taller than one line, so it hangs below the
// chip, right edges aligned, rather than covering the field to the chip's left.
function notePlaceBelow(spot: Spot, touch: boolean): CSSProperties {
  const gap = 6;
  const size = touch ? CHIP_SIZE_TOUCH : CHIP_SIZE;
  const right = window.innerWidth - spot.left - size;
  const below = spot.top + size + gap;
  return touch ? { right, bottom: window.innerHeight - spot.top + gap } : { right, top: below };
}
