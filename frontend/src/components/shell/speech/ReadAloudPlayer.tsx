import { useSyncExternalStore } from "react";
import { AlertCircle, Loader2, Pause, Play, SkipBack, SkipForward, Square, Volume2, X } from "lucide-react";
import { Button, Menu, MenuItem } from "@workshop/ui";
import { positionLabel } from "../../../lib/speech/readCursor";
import { BackToReading, HighlightButton } from "./ReadPlaceButtons";
import { reader, SPEEDS } from "./reader";

const speedLabel = (s: number) => `${s}x`;

/** The floating control while something is being read: what it is doing and
 *  where in the text, previous sentence, pause or play, next sentence, the
 *  speed (a drop-up of every speed, the current one checked), Highlight, and
 *  Stop; above it, Back to reading after the page was scrolled away.
 *  Hidden when nothing is. */
export function ReadAloudPlayer() {
  const state = useSyncExternalStore(reader.subscribe, reader.getState, reader.getState);
  if (state.phase === "idle") return null;
  if (state.phase === "error") {
    return (
      <div className="ws-speech-player" data-phase="error" role="status">
        <span className="ws-speech-player__state">
          <AlertCircle aria-hidden /> {state.error}
        </span>
        <Button variant="ghost" size="sm" icon onClick={() => reader.stop()} aria-label="Dismiss" title="Dismiss">
          <X aria-hidden />
        </Button>
      </div>
    );
  }
  const paused = state.phase === "paused";
  const word = state.phase === "loading" ? "Loading" : paused ? "Paused" : "Reading";
  const atEnd = state.index >= state.count - 1;
  return (
    <div className="ws-speech-player" data-phase={state.phase} role="region" aria-label="Read aloud">
      <BackToReading placed={state.placed} />
      <span className="ws-speech-player__state" aria-live="polite">
        {state.phase === "loading" ? <Loader2 className="ws-spin" aria-hidden /> : <Volume2 aria-hidden />}
        <span className="ws-speech-player__word">{word}</span>
      </span>
      {state.count > 1 && (
        <span className="ws-speech-player__pos" aria-label={`Sentence ${state.index + 1} of ${state.count}`}>
          {positionLabel(state.index, state.count)}
        </span>
      )}
      <Button variant="ghost" size="sm" icon onClick={() => reader.skip(-1)} aria-label="Previous sentence" title="Previous sentence">
        <SkipBack aria-hidden />
      </Button>
      {paused ? (
        <Button variant="ghost" size="sm" icon onClick={() => reader.resume()} aria-label="Play" title="Play">
          <Play aria-hidden />
        </Button>
      ) : (
        <Button variant="ghost" size="sm" icon onClick={() => reader.pause()} aria-label="Pause" title="Pause">
          <Pause aria-hidden />
        </Button>
      )}
      <Button
        variant="ghost"
        size="sm"
        icon
        disabled={atEnd}
        onClick={() => reader.skip(1)}
        aria-label="Next sentence"
        title="Next sentence"
      >
        <SkipForward aria-hidden />
      </Button>
      <Menu
        side="top"
        label="Reading speed"
        trigger={(
          <Button
            variant="ghost"
            size="sm"
            className="ws-speech-player__speed"
            aria-label={`Speed ${speedLabel(state.speed)}`}
            title="Speed"
          >
            {speedLabel(state.speed)}
          </Button>
        )}
      >
        {SPEEDS.map((s) => (
          <MenuItem key={s} checked={s === state.speed} onSelect={() => reader.setSpeed(s)}>
            {speedLabel(s)}
          </MenuItem>
        ))}
      </Menu>
      <HighlightButton />
      <Button variant="ghost" size="sm" icon onClick={() => reader.stop()} aria-label="Stop reading" title="Stop reading">
        <Square aria-hidden />
      </Button>
    </div>
  );
}
